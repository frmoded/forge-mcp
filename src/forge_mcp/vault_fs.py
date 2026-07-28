"""Local vault filesystem: V2a-aware note reader / Recipe splicer / writer.

Drain CW-MCP-2-C (Option C — see drain §Architecture Question). forge-
transpile has no vault-write endpoint (grep-verify: no `/vault/notes`
handler in `~/projects/forge-transpile/main.py` as of drain 2026-07-14).
Rather than route commits through a stateless HTTP service that can't
reach a local vault, `forge_commit_recipe` writes directly from the
forge-mcp process — same trust boundary as the vault, no wire-shape
translation needed.

Responsibilities:

- Path resolution + traversal defense (`note_id` → absolute Path inside
  vault root; reject `../` escapes).
- V2a note parse: read a file, split frontmatter + facet sections
  (`# Description`, `# Recipe`, `# Python`) — see `~/forge-vaults/bluh/`
  for the canonical shape.
- Recipe splicer: replace ONLY the Recipe facet body; preserve
  frontmatter + Description + Python + everything else byte-for-byte
  (D-B contract from tool-surface v1 spec).
- Version tracking: stamp `recipe_version` in frontmatter (monotonic
  integer). Agent sends `expected_version` on commit; on mismatch, we
  return `isError: true` with the expected + current numbers (D-mcp-3).
- Optional git commit: if vault is git-tracked, commit the write with
  a scripted message so `forge-recipe:///v{n}` can fetch prior Recipe
  bodies via `git show`. If no `.git`, skip (per drain §6 out-of-scope
  — "commit still works, but `forge-recipe:///v{n}` returns 'history
  unavailable' for prior versions").

Note-shape parsing is DELIBERATELY NOT a full markdown parser — the V2a
shape is line-oriented (`^# Description`, `^# Recipe`, `^# Python`) and
we only need to identify facet boundaries. A full markdown AST would be
overkill for the splice.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class VaultFSError(Exception):
  """Base for vault-fs errors."""


class NoteIdInvalid(VaultFSError):
  """`note_id` failed traversal / shape checks."""


class NoteNotFound(VaultFSError):
  """Note file doesn't exist inside the vault."""


class NoteExists(VaultFSError):
  """create_note_shell was called on a path where a note already lives.

  CW-MCP-multi-vault-create-dir: separates the fresh-note-creation path
  from the commit_recipe path. commit_recipe intentionally creates fresh
  notes as a side effect; create_note_shell refuses to overwrite so the
  agent can't accidentally clobber an authored note by asking for an
  empty shell.
  """

  def __init__(self, note_id: str, path: Path) -> None:
    super().__init__(f"Note {note_id!r} already exists at {path}.")
    self.note_id = note_id
    self.path = path


class DirInvalid(VaultFSError):
  """mkdir was asked to create an invalid path (traversal / hidden / …)."""


class VersionConflict(VaultFSError):
  """Caller's `expected_version` didn't match the note's current version.

  Attributes carry the numbers so the tool layer can format the D-mcp-3
  actionable error text ("expected N, is at M") without re-reading state.
  """

  def __init__(self, note_id: str, expected: int, current: int) -> None:
    super().__init__(
      f"Version conflict on note {note_id!r}: expected version "
      f"{expected}, note is at version {current}."
    )
    self.note_id = note_id
    self.expected = expected
    self.current = current


# ---------------------------------------------------------------------------
# Parsed-note dataclass
# ---------------------------------------------------------------------------


@dataclass
class ParsedNote:
  """The V2a note broken into slices sufficient to splice the Recipe
  body without touching anything else.

  `frontmatter_text` is the RAW frontmatter block content (between the
  two `---` fences, no fences). `frontmatter_dict` is a shallow key→str
  parse — enough for `recipe_version`; we don't need YAML depth here.

  `pre_recipe` + `recipe_body` + `post_recipe` reassemble byte-for-byte
  to the original file content (minus the frontmatter). Splicing the
  Recipe body means replacing `recipe_body` and re-joining.

  `has_recipe_facet` is False for notes that predate V2a or were
  hand-authored without a `# Recipe` section — splicer appends one in
  the canonical position.
  """

  frontmatter_text: str
  frontmatter_dict: dict[str, str] = field(default_factory=dict)
  body_before_frontmatter: str = ""  # rare: any content BEFORE `---`; usually ""
  pre_recipe: str = ""  # includes leading `---\n` fence + frontmatter + trailing `---\n` + Description
  recipe_body: str = ""  # what's between `# Recipe` header line and next `# ` header
  post_recipe: str = ""  # Python, Dependencies, everything after Recipe

  has_recipe_facet: bool = True


# ---------------------------------------------------------------------------
# Frontmatter helpers
# ---------------------------------------------------------------------------


# Only support the leading-fence YAML shape (`---\n...\n---\n`). Notes
# without frontmatter get a fresh block injected on write.
_FRONTMATTER_FENCE = "---"


def _parse_frontmatter_dict(text: str) -> dict[str, str]:
  """Shallow `key: value` parse. Doesn't support nested YAML / arrays /
  quoted strings — we only need scalar values for `recipe_version`
  bookkeeping. Lines that don't match `^key: value$` are ignored (they
  round-trip via `frontmatter_text` unchanged when we rewrite)."""
  out: dict[str, str] = {}
  for line in text.splitlines():
    m = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*)$", line)
    if m:
      out[m.group(1)] = m.group(2).strip()
  return out


def _extract_inputs(frontmatter_dict: dict[str, str], description_body: str) -> list[str]:
  """CW-MCP-read-note. Return the note's declared inputs as a list of
  bare names.

  Priority:
    1. Frontmatter `inputs: [a, b, c]` — the canonical V2a form. The
       shallow frontmatter parser stores this as a string `"[a, b, c]"`;
       we split-and-strip.
    2. Description-body `Inputs:` line — legacy fallback shape.

  Returns [] when neither is present. Non-alphanumeric junk is filtered
  (names must match `[A-Za-z_][A-Za-z0-9_]*`)."""
  raw = frontmatter_dict.get("inputs")
  if raw:
    inner = raw.strip()
    if inner.startswith("[") and inner.endswith("]"):
      inner = inner[1:-1]
    tokens = [t.strip().strip('"').strip("'") for t in inner.split(",")]
    return [t for t in tokens if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", t)]
  # Description-body fallback: look for a line like `Inputs: a, b, c`
  # (case-insensitive header, comma-separated body).
  for line in description_body.splitlines():
    m = re.match(r"^\s*inputs\s*:\s*(.*)$", line, re.IGNORECASE)
    if m:
      body = m.group(1).strip()
      if body.startswith("[") and body.endswith("]"):
        body = body[1:-1]
      tokens = [t.strip().strip('"').strip("'") for t in body.split(",")]
      return [t for t in tokens if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", t)]
  return []


def _render_inputs_yaml(inputs: list[str]) -> str:
  """Render a list of input names as inline YAML `[a, b, c]`.

  CW-forge-mcp-commit-recipe-accept-inputs-param (drain 2026-07-27-2005).

  Matches the shape `create_note_shell` uses (`inputs: []`) so authored
  inputs from `commit_recipe(inputs=...)` are byte-compatible with the
  plugin's read path + drain 1730's introspector.
  """
  if not inputs:
    return "[]"
  # Quote names that aren't bare identifiers; bare identifiers stay
  # unquoted for readability (matches how the plugin writes them).
  parts: list[str] = []
  for name in inputs:
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
      parts.append(name)
    else:
      # Fall back to JSON-safe quoting for non-identifier names.
      parts.append(f'"{name}"')
  return "[" + ", ".join(parts) + "]"


def _update_frontmatter_line(text: str, key: str, value: str) -> str:
  """Replace `key: <old>` with `key: <value>`; append if key not present.
  Preserves everything else in the block byte-for-byte."""
  lines = text.splitlines()
  new_line = f"{key}: {value}"
  for i, line in enumerate(lines):
    if re.match(rf"^{re.escape(key)}\s*:", line):
      lines[i] = new_line
      return "\n".join(lines) + ("\n" if text.endswith("\n") else "")
  # Not present — append. Keep trailing newline behavior consistent.
  if text and not text.endswith("\n"):
    text = text + "\n"
  return text + new_line + "\n"


# ---------------------------------------------------------------------------
# Note parsing
# ---------------------------------------------------------------------------


# Header line regex — `# ` followed by a facet name at column 0.
# Only these three headers are structural; other `# whatever` inside
# body prose (e.g., inside a code fence) is ignored via naive scan
# (we don't parse markdown). In practice, V2a notes' facet headers are
# the only top-level `# ` lines outside code fences.
_FACET_HEADERS = ("# Description", "# Recipe", "# Python", "# E--")


def parse_note(raw: str) -> ParsedNote:
  """Split a note's raw text into slices for spliceable rewrite.

  Handles:
    - Leading YAML frontmatter (optional; may be absent for hand-
      authored fragments).
    - Facet headers in the V2a order (Description → Recipe → Python).
    - Notes that lack a Recipe facet (`has_recipe_facet=False`; splicer
      appends one in the canonical position).
    - Notes with `# E--` instead of `# Recipe` (older shape used by
      Untitled.md et al) — treated as the Recipe facet since that's what
      the current V2a naming convention would call it.
  """
  # Step 1: frontmatter split.
  frontmatter_text = ""
  body = raw
  body_before_frontmatter = ""
  if raw.lstrip().startswith(_FRONTMATTER_FENCE):
    # Preserve any leading whitespace before the fence (rare but possible).
    idx = raw.find(_FRONTMATTER_FENCE)
    body_before_frontmatter = raw[:idx]
    after_first = raw[idx + len(_FRONTMATTER_FENCE):]
    # Skip the newline after the opening fence.
    if after_first.startswith("\n"):
      after_first = after_first[1:]
    end_idx = after_first.find(f"\n{_FRONTMATTER_FENCE}")
    if end_idx != -1:
      frontmatter_text = after_first[:end_idx]
      body = after_first[end_idx + 1 + len(_FRONTMATTER_FENCE):]
      # Skip the newline after the closing fence.
      if body.startswith("\n"):
        body = body[1:]
  frontmatter_dict = _parse_frontmatter_dict(frontmatter_text)

  # Step 2: locate facet headers by line-oriented scan.
  # Match at start-of-line only. `# Recipe` (or the legacy `# E--`) is
  # the load-bearing anchor; Description/Python/anything-else are just
  # boundaries.
  body_lines = body.splitlines(keepends=True)
  recipe_start_line = None  # 0-based line index of the `# Recipe` header
  next_header_line = None  # 0-based line index of the header AFTER Recipe
  for i, line in enumerate(body_lines):
    stripped = line.rstrip("\n").rstrip("\r")
    if stripped == "# Recipe" or stripped == "# E--":
      if recipe_start_line is None:
        recipe_start_line = i
    elif recipe_start_line is not None and stripped.startswith("# ") and stripped in _FACET_HEADERS:
      next_header_line = i
      break

  # Step 3: assemble the pre/mid/post slices.
  frontmatter_block = ""
  if raw.lstrip().startswith(_FRONTMATTER_FENCE):
    frontmatter_block = f"{_FRONTMATTER_FENCE}\n{frontmatter_text}\n{_FRONTMATTER_FENCE}\n"

  if recipe_start_line is None:
    # No Recipe facet present. `pre_recipe` = frontmatter + entire body.
    return ParsedNote(
      frontmatter_text=frontmatter_text,
      frontmatter_dict=frontmatter_dict,
      body_before_frontmatter=body_before_frontmatter,
      pre_recipe=body_before_frontmatter + frontmatter_block + body,
      recipe_body="",
      post_recipe="",
      has_recipe_facet=False,
    )

  pre_recipe_lines = body_lines[: recipe_start_line + 1]  # includes the `# Recipe` header line
  if next_header_line is None:
    recipe_body_lines = body_lines[recipe_start_line + 1 :]
    post_recipe_lines: list[str] = []
  else:
    recipe_body_lines = body_lines[recipe_start_line + 1 : next_header_line]
    post_recipe_lines = body_lines[next_header_line:]

  return ParsedNote(
    frontmatter_text=frontmatter_text,
    frontmatter_dict=frontmatter_dict,
    body_before_frontmatter=body_before_frontmatter,
    pre_recipe=body_before_frontmatter + frontmatter_block + "".join(pre_recipe_lines),
    recipe_body="".join(recipe_body_lines),
    post_recipe="".join(post_recipe_lines),
    has_recipe_facet=True,
  )


def extract_all_facets(raw: str) -> dict[str, str]:
  """Return a dict mapping facet-header name (without the leading `# `)
  to the facet's body text. Covers Description / Recipe / E-- / Python /
  Data / Inputs — every top-level `# Foo` header the walker sees.

  CW-MCP-read-note (2026-07-17). parse_note focuses on Recipe splicing
  and only slices the Recipe body; forge_read_note needs full-content
  access to every facet. Rather than complicate parse_note's contract,
  this helper walks the body separately with the same line-oriented
  header scan.

  Legacy `# E--` is treated as an alias for `# Recipe` (matches
  parse_note's behavior; V2a notes may still use the old header).

  Body between the frontmatter closing `---` and the first `# Foo`
  header is discarded (typical V2a notes don't have leading prose).
  """
  # Skip frontmatter if present.
  body = raw
  if raw.lstrip().startswith(_FRONTMATTER_FENCE):
    idx = raw.find(_FRONTMATTER_FENCE)
    after_first = raw[idx + len(_FRONTMATTER_FENCE):]
    if after_first.startswith("\n"):
      after_first = after_first[1:]
    end_idx = after_first.find(f"\n{_FRONTMATTER_FENCE}")
    if end_idx != -1:
      body = after_first[end_idx + 1 + len(_FRONTMATTER_FENCE):]
      if body.startswith("\n"):
        body = body[1:]

  facets: dict[str, str] = {}
  current_key: str | None = None
  current_lines: list[str] = []

  def _flush() -> None:
    nonlocal current_key
    if current_key is not None:
      facets[current_key] = "".join(current_lines).strip("\n")

  for line in body.splitlines(keepends=True):
    stripped = line.rstrip("\n").rstrip("\r")
    if stripped.startswith("# ") and not stripped.startswith("## "):
      # New top-level facet header.
      _flush()
      header = stripped[2:].strip()
      # Normalize the legacy alias so callers see a single canonical key.
      if header == "E--":
        header = "Recipe"
      current_key = header
      current_lines = []
    elif current_key is not None:
      current_lines.append(line)
  _flush()
  return facets


def splice_recipe(
  raw: str, new_recipe_body: str, new_version: int,
  *, inputs: list[str] | None = None,
) -> str:
  """Return `raw` with the Recipe facet body replaced by
  `new_recipe_body` and `recipe_version: {new_version}` stamped in the
  frontmatter. Byte-for-byte-preserves everything else — including
  Description, Python facet, dependencies section, trailing content.

  When the source note lacks a `# Recipe` header, append one in the
  canonical position: after Description (if present) OR at the end.

  `new_recipe_body` should end with a single trailing newline for
  clean concatenation; we normalize (trim right, add `\\n\\n`) so
  callers don't have to think about it.
  """
  parsed = parse_note(raw)

  # Normalize the new Recipe body: strip trailing whitespace + ensure
  # exactly one blank line between the body and the next facet header
  # (or a single trailing newline at EOF).
  body_norm = new_recipe_body.rstrip() + "\n"
  # If there's a post_recipe (Python etc.), we want a blank line between
  # our body and the next `# ` header.
  if parsed.post_recipe:
    body_norm = body_norm + "\n"

  # Bump the frontmatter version stamp.
  new_frontmatter_text = _update_frontmatter_line(
    parsed.frontmatter_text, "recipe_version", str(new_version)
  )
  # CW-forge-mcp-commit-recipe-accept-inputs-param (drain 2026-07-27-2005).
  # When caller provided `inputs`, overwrite the `inputs:` line in
  # frontmatter (creating it if absent). When None, leave existing
  # frontmatter `inputs:` untouched — back-compat with pre-drain
  # callers that never passed the param.
  if inputs is not None:
    new_frontmatter_text = _update_frontmatter_line(
      new_frontmatter_text, "inputs", _render_inputs_yaml(inputs)
    )

  # Rebuild the frontmatter block.
  if new_frontmatter_text or parsed.frontmatter_text:
    new_frontmatter_block = f"{_FRONTMATTER_FENCE}\n{new_frontmatter_text.rstrip()}\n{_FRONTMATTER_FENCE}\n"
  else:
    new_frontmatter_block = f"{_FRONTMATTER_FENCE}\nrecipe_version: {new_version}\n{_FRONTMATTER_FENCE}\n"

  if parsed.has_recipe_facet:
    # Recompute pre_recipe with the fresh frontmatter block. The
    # original pre_recipe includes the OLD frontmatter block + body
    # before-Recipe; we need to swap the block only.
    old_frontmatter_block = ""
    if parsed.frontmatter_text or raw.lstrip().startswith(_FRONTMATTER_FENCE):
      old_frontmatter_block = f"{_FRONTMATTER_FENCE}\n{parsed.frontmatter_text}\n{_FRONTMATTER_FENCE}\n"
    new_pre_recipe = parsed.pre_recipe.replace(
      old_frontmatter_block, new_frontmatter_block, 1
    ) if old_frontmatter_block else new_frontmatter_block + parsed.pre_recipe
    return new_pre_recipe + body_norm + parsed.post_recipe

  # No Recipe facet in source. Append one in the canonical position
  # (after everything). Split pre_recipe into (frontmatter, body); we
  # want to place `# Recipe\n<body>\n` at the end of the body.
  # For simplicity: rebuild = new_frontmatter_block + body + `\n# Recipe\n<body>\n`.
  old_frontmatter_block = ""
  if parsed.frontmatter_text or raw.lstrip().startswith(_FRONTMATTER_FENCE):
    old_frontmatter_block = f"{_FRONTMATTER_FENCE}\n{parsed.frontmatter_text}\n{_FRONTMATTER_FENCE}\n"
  # Extract the body-after-frontmatter from pre_recipe.
  body_after_frontmatter = parsed.pre_recipe
  if old_frontmatter_block:
    body_after_frontmatter = parsed.pre_recipe.replace(old_frontmatter_block, "", 1)
  # Trim trailing whitespace so `\n# Recipe` sits cleanly.
  body_after_frontmatter = body_after_frontmatter.rstrip() + "\n\n"
  return new_frontmatter_block + body_after_frontmatter + "# Recipe\n\n" + body_norm


# ---------------------------------------------------------------------------
# VaultFS — path safety + read/write + git commit
# ---------------------------------------------------------------------------


# Reject any note_id that:
#   * is absolute (`/foo`),
#   * contains `..` segments (traversal),
#   * contains a NUL byte (path-injection defense),
#   * starts with `.` (hidden files like `.obsidian/config.json` — not
#     for agent-writable content).
_NOTE_ID_SEGMENT = re.compile(r"^[A-Za-z0-9_.\-][A-Za-z0-9_.\- ]*$")


def _validate_dir_path(path: str) -> None:
  """Path-traversal defense for `mkdir` targets. Mirrors _validate_note_id
  but permits empty segments (path may end with `/` — normalized away).

  CW-MCP-multi-vault-create-dir. Same segment allowlist as note_id so
  the agent can't create a `.git` directory or escape via `..`.
  """
  if not path or not path.strip("/"):
    raise DirInvalid("directory path is empty")
  if "\x00" in path:
    raise DirInvalid("directory path contains NUL byte")
  if path.startswith("/"):
    raise DirInvalid(f"directory path must be vault-relative, got {path!r}")
  for seg in path.split("/"):
    if seg in ("", ".", ".."):
      if seg == "":
        continue  # trailing slash — ok
      raise DirInvalid(f"directory path {path!r} contains a forbidden segment {seg!r}")
    if seg.startswith("."):
      raise DirInvalid(f"directory path {path!r} refers to a hidden path {seg!r}")
    if not _NOTE_ID_SEGMENT.match(seg):
      raise DirInvalid(
        f"directory path {path!r} contains a segment with unsupported "
        f"characters: {seg!r}"
      )


def _validate_note_id(note_id: str) -> None:
  if not note_id:
    raise NoteIdInvalid("note_id is empty")
  if "\x00" in note_id:
    raise NoteIdInvalid("note_id contains NUL byte")
  if note_id.startswith("/"):
    raise NoteIdInvalid(f"note_id must be vault-relative, got {note_id!r}")
  segments = note_id.split("/")
  for seg in segments:
    if seg in ("", ".", ".."):
      raise NoteIdInvalid(
        f"note_id {note_id!r} contains a forbidden segment {seg!r}"
      )
    if seg.startswith("."):
      raise NoteIdInvalid(
        f"note_id {note_id!r} refers to a hidden path {seg!r}"
      )
    if not _NOTE_ID_SEGMENT.match(seg):
      raise NoteIdInvalid(
        f"note_id {note_id!r} contains a segment with unsupported "
        f"characters: {seg!r}"
      )


@dataclass
class VaultFS:
  """Bound to a specific vault root. All read/write goes through the
  root's `Path.resolve()` and is asserted to live under it — traversal
  defense per drain §5 test #6."""

  root: Path

  def __post_init__(self) -> None:
    # Resolve up-front so subsequent `.is_relative_to(self.root)` checks
    # compare canonical paths. `strict=False` because the vault dir MUST
    # exist at construction (we don't create it).
    self.root = self.root.expanduser().resolve()
    if not self.root.is_dir():
      raise VaultFSError(f"vault root does not exist or is not a directory: {self.root}")

  # -- Path resolution ------------------------------------------------------

  def note_path(self, note_id: str) -> Path:
    """Map `note_id` (e.g., `mcp-scratch/agent_test`) to an absolute
    path inside the vault, appending `.md` if not present. Raises
    NoteIdInvalid on traversal / shape violations."""
    _validate_note_id(note_id)
    rel = note_id if note_id.endswith(".md") else f"{note_id}.md"
    candidate = (self.root / rel).resolve()
    # Belt: `.resolve()` collapses symlinks + `..` — verify the result
    # still lives under the root. Suspenders: `_validate_note_id`
    # already rejected `..` segments, but a symlink out of the vault
    # could bypass that.
    try:
      candidate.relative_to(self.root)
    except ValueError as exc:
      raise NoteIdInvalid(
        f"note_id {note_id!r} resolves outside vault root {self.root}"
      ) from exc
    return candidate

  # -- Read / write ---------------------------------------------------------

  def read_note(self, note_id: str) -> str:
    path = self.note_path(note_id)
    if not path.is_file():
      raise NoteNotFound(f"note {note_id!r} not found at {path}")
    return path.read_text(encoding="utf-8")

  def current_recipe_version(self, note_id: str) -> int:
    """Read the note's `recipe_version` frontmatter stamp. Missing / non-
    integer stamps default to 0 (fresh notes). NoteNotFound raises."""
    raw = self.read_note(note_id)
    parsed = parse_note(raw)
    v = parsed.frontmatter_dict.get("recipe_version", "0")
    try:
      return int(v)
    except ValueError:
      return 0

  def commit_recipe(
    self,
    note_id: str,
    new_recipe_body: str,
    expected_version: int | None,
    *,
    git_message: str | None = None,
    inputs: list[str] | None = None,
  ) -> tuple[int, str | None]:
    """Splice `new_recipe_body` into the note's Recipe facet, stamp
    `recipe_version: current+1`, write atomically. If the vault is git-
    tracked, commit the write with `git_message` (default:
    `forge-mcp: commit recipe {note_id} v{new_version}`) so `forge-
    recipe:///{note_id}/v{n}` can fetch prior versions.

    Version-conflict semantics (D-mcp-3):
      * `expected_version=None` → skip the check (first commit / agent
        opts out). Still increments.
      * `expected_version != current` → raises VersionConflict WITHOUT
        writing.
      * `expected_version == current` → write + increment.

    Returns `(new_version, git_sha_or_None)`. git_sha is None when
    the vault has no `.git` (per drain §6 out-of-scope).

    New notes (path doesn't exist) create the file with a minimal
    frontmatter + Recipe facet — Description stays empty until the
    driver adds one via Obsidian.
    """
    path = self.note_path(note_id)
    if not path.exists():
      # Fresh note — bypass version check (there's no existing version).
      new_version = 1
      # CW-forge-mcp-commit-recipe-accept-inputs-param
      # (drain 2026-07-27-2005). When caller passes `inputs`, stamp
      # frontmatter `inputs: [...]` at note-creation time; when None,
      # omit the field entirely (older callers that never populated
      # inputs still produce byte-equivalent notes).
      inputs_line = ""
      if inputs is not None:
        inputs_line = f"inputs: {_render_inputs_yaml(inputs)}\n"
      content = (
        f"---\n{inputs_line}recipe_version: {new_version}\n---\n"
        f"\n# Description\n\n\n# Recipe\n\n{new_recipe_body.rstrip()}\n"
      )
      path.parent.mkdir(parents=True, exist_ok=True)
      _atomic_write(path, content)
    else:
      current = self.current_recipe_version(note_id)
      if expected_version is not None and expected_version != current:
        raise VersionConflict(note_id, expected_version, current)
      new_version = current + 1
      raw = self.read_note(note_id)
      # CW-forge-mcp-commit-recipe-accept-inputs-param — thread `inputs`
      # through the splicer. When None, splicer leaves the existing
      # `inputs:` line untouched (back-compat with pre-drain callers).
      new_content = splice_recipe(
        raw, new_recipe_body, new_version, inputs=inputs,
      )
      _atomic_write(path, new_content)

    git_sha: str | None = None
    if _is_git_tracked(self.root):
      # CW-MCP-commit-message-param: caller-supplied messages must still
      # end with `v{new_version}` so `read_recipe_version` can grep the
      # git log by subject-suffix. If the caller included it, respect
      # verbatim; otherwise append.
      suffix = f"v{new_version}"
      if git_message:
        message = (
          git_message
          if git_message.rstrip().endswith(suffix)
          else f"{git_message} {suffix}"
        )
      else:
        message = f"forge-mcp: commit recipe {note_id} {suffix}"
      git_sha = _git_commit_file(self.root, path, message)

    return new_version, git_sha

  # -- Read note content (CW-MCP-read-note) ---------------------------------

  def read_note_content(self, note_id: str) -> dict:
    """Read a vault note and return the full parsed content: frontmatter
    (dict), each V2a facet body (Description / Recipe / Python / Data /
    Inputs), inputs list (from frontmatter or Description), and the raw
    markdown source.

    Reuses the standard path-traversal defense via `note_path`.
    Raises NoteNotFound if the path doesn't resolve to an existing file.

    Fields returned (values are strings or None; `inputs` is a list;
    `frontmatter` is a dict):
      raw, frontmatter, description, recipe, python, data, inputs
    """
    path = self.note_path(note_id)
    if not path.is_file():
      raise NoteNotFound(f"note {note_id!r} not found at {path}")
    raw = path.read_text(encoding="utf-8")
    parsed = parse_note(raw)
    facets = extract_all_facets(raw)
    description = facets.get("Description") or ""
    recipe = facets.get("Recipe")
    python = facets.get("Python")
    data = facets.get("Data")
    inputs = _extract_inputs(parsed.frontmatter_dict, description)
    # Drain 2026-07-23-1700 Phase 1 — surface `sync_state` from
    # frontmatter for external observers (wizard / CC / CCQA / cohort
    # scripts) that read note state without loading the plugin.
    sync_state = parsed.frontmatter_dict.get("sync_state")
    # Drain 2026-07-26-1200 — surface a discriminated `type` field so
    # callers can distinguish action notes (frontmatter `type: action`)
    # from data notes (`type: data`) from vanilla prose notes (no
    # `type` frontmatter). Vanilla notes may still have arbitrary
    # frontmatter fields — we only key on the `type` field itself.
    note_type = _classify_note_type(parsed.frontmatter_dict)
    return {
      "raw": raw,
      "frontmatter": dict(parsed.frontmatter_dict),
      "description": description,
      "recipe": recipe,
      "python": python,
      "data": data,
      "inputs": inputs,
      "sync_state": sync_state,
      "type": note_type,
    }

  # -- Vanilla markdown notes (CW-mcp-and-plugin-support-vanilla-notes) -----

  def write_markdown_note(
    self, note_id: str, body: str, *, allow_overwrite: bool = False,
  ) -> Path:
    """Write a raw markdown file at `note_id`, byte-for-byte from `body`.

    Drain 2026-07-26-1200 — supports vanilla prose notes (e.g. music-
    theory documentation) that live in a Forge vault without any
    Forge-managed frontmatter. Unlike `create_note_shell`, this does
    NOT inject `type: action` / `# Description` scaffolding — the
    caller's `body` is written verbatim.

    Path validated via `note_path` (path-traversal defense, hidden-
    segment reject, symlink escape reject). Parent directories created
    if missing.

    - `allow_overwrite=False` (default) raises `NoteExists` if the
      path is already occupied. Used by `forge_create_markdown_note`.
    - `allow_overwrite=True` replaces the file's content atomically.
      Used by `forge_edit_markdown_note` (which independently guards
      against clobbering action notes at the tool layer).

    Returns the absolute path written.
    """
    path = self.note_path(note_id)
    if path.exists() and not allow_overwrite:
      raise NoteExists(note_id, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, body)
    return path

  # -- Directory + note creation (CW-MCP-multi-vault-create-dir) ------------

  def mkdir(self, path: str) -> Path:
    """Create a directory inside the vault (parents=True, exist_ok=True).

    Path-traversal defense: rejects `..` segments, absolute paths, hidden
    segments, NUL bytes, unsupported characters. Symlink escape check
    same as note_path (resolve + relative_to).

    Idempotent — mkdir -p semantics; safe to call twice.

    Returns the absolute path created.
    """
    _validate_dir_path(path)
    rel = path.rstrip("/")
    candidate = (self.root / rel).resolve()
    try:
      candidate.relative_to(self.root)
    except ValueError as exc:
      raise DirInvalid(
        f"directory path {path!r} resolves outside vault root {self.root}"
      ) from exc
    candidate.mkdir(parents=True, exist_ok=True)
    return candidate

  def create_note_shell(self, note_id: str, description: str = "") -> Path:
    """Create a fresh note with proper V2a frontmatter + optional
    Description. NO Recipe facet — that's commit_recipe's job.

    CW-create-note-shell-v2a-frontmatter — writes the canonical V2a
    frontmatter fields (`type: action`, `inputs: []`, `recipe_version:
    0`) so the plugin's editor-attribute facet detects the note as a
    Forge action note (via `type: action`), and so that a subsequent
    `commit_recipe` call's `splice_recipe` can merge the version stamp
    in-place instead of prepending a duplicate frontmatter block.

    Fails cleanly if the note already exists (raises NoteExists). Does
    NOT overwrite. If the parent directory doesn't exist, it is created
    (mkdir -p semantics).

    Returns the absolute path created.
    """
    path = self.note_path(note_id)
    if path.exists():
      raise NoteExists(note_id, path)
    desc_body = description.strip()
    # Canonical V2a shape — matches the plugin's `actionTemplate`
    # (welcome-shape-classifier tests + modal.test.ts) and satisfies
    # `parse_note`'s frontmatter recognition (which requires a non-
    # empty block between the fences to find the closing `\n---`).
    frontmatter_block = (
      "---\n"
      "type: action\n"
      "inputs: []\n"
      "recipe_version: 0\n"
      "---\n"
    )
    if desc_body:
      content = f"{frontmatter_block}\n# Description\n\n{desc_body}\n"
    else:
      content = f"{frontmatter_block}\n# Description\n\n\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, content)
    return path

  # -- Rename / delete (CW-MCP-rename-delete-note) --------------------------

  def rename_note(
    self,
    old_note_id: str,
    new_note_id: str,
    message: str | None = None,
  ) -> tuple[Path, str | None, str | None]:
    """Rename a note within this vault.

    Both note_ids validated via `note_path` (path-traversal defense,
    hidden-segment reject, symlink escape reject). If the vault is
    git-tracked, uses `git mv` to preserve history; else plain
    `Path.rename`. Parent dirs for `new_note_id` are created if
    absent.

    On git-tracked vaults, this method DISCARDS any unstaged working-
    tree modifications to the source file before renaming it
    (`git checkout HEAD -- <rel_source>` precedes `git mv`). This
    handles the common case where an external process (e.g., the
    Obsidian plugin) has re-derived a facet between the MCP client's
    prior commit and this rename call. Rename semantics: move the note
    as it exists in HEAD to the new path, discarding any transient in-
    flight edits. If HEAD has no such file (uncommitted / just-added-
    never-committed), falls through to plain `Path.rename()`.

    CW-forge-mcp-delete-and-rename-note-auto-commit (drain 2026-07-24-1500):
    On git-tracked vaults where the source was tracked in HEAD (so
    `git mv` actually ran), we now commit the rename immediately using
    a path-scoped `git commit -- <old_rel> <new_rel>` so unrelated
    staged changes elsewhere in the vault are NOT swept in. If a
    caller-supplied `message` is None, defaults to
    `rename note <old_note_id> → <new_note_id>`.

    Returns `(new_path, git_sha_or_None, commit_message_or_None)`.
    git_sha is None when the vault isn't git-tracked OR when HEAD didn't
    track the source (nothing was committed).

    Raises NoteNotFound if `old_note_id` doesn't resolve to a file.
    Raises NoteExists if `new_note_id` already resolves to a file.
    Raises NoteIdInvalid on traversal / shape violations of either id.
    """
    old_path = self.note_path(old_note_id)
    new_path = self.note_path(new_note_id)
    if not old_path.is_file():
      raise NoteNotFound(f"note {old_note_id!r} not found at {old_path}")
    if new_path.exists():
      raise NoteExists(new_note_id, new_path)
    new_path.parent.mkdir(parents=True, exist_ok=True)
    if _is_git_tracked(self.root):
      rel_old = old_path.relative_to(self.root)
      rel_new = new_path.relative_to(self.root)
      # CW-forge-rename-note-handle-dirty-working-tree (drain 1905):
      # Mirror delete_note's pattern (drain 1800). `git ls-tree HEAD`
      # pre-check tells us whether HEAD tracks the source. If it does,
      # reset any unstaged working-tree modifications before `git mv`
      # (else `git mv` silently promotes the dirty content into the
      # rename target, mixing the plugin's re-derivation into what the
      # MCP client thinks is a pure move). If HEAD does NOT track the
      # source (uncommitted / just-added-never-committed), skip the
      # reset and fall through to `Path.rename()` — no HEAD entry to
      # reset from, and `git mv` on such a source behaves indistinguish-
      # ably from `Path.rename()` + a target add.
      ls_tree = subprocess.run(
        ["git", "-C", str(self.root), "ls-tree", "HEAD", "--", str(rel_old)],
        capture_output=True, text=True, check=False,
      )
      in_head = ls_tree.returncode == 0 and ls_tree.stdout.strip() != ""
      if not in_head:
        # Source never committed — no HEAD entry to reset from. Plain
        # rename; git-status will show target as untracked (?? <rel_new>).
        old_path.rename(new_path)
        return new_path, None, None
      # Source is tracked in HEAD. Reset any working-tree drift first.
      try:
        subprocess.run(
          ["git", "-C", str(self.root), "checkout", "HEAD", "--", str(rel_old)],
          capture_output=True, text=True, check=True,
        )
      except subprocess.CalledProcessError as exc:
        raise VaultFSError(
          f"git checkout HEAD failed for {old_note_id!r} (pre-rename reset): "
          f"{exc.stderr.strip() or exc.stdout.strip() or exc}"
        ) from exc
      try:
        subprocess.run(
          ["git", "-C", str(self.root), "mv", str(rel_old), str(rel_new)],
          capture_output=True, text=True, check=True,
        )
      except subprocess.CalledProcessError as exc:
        raise VaultFSError(
          f"git mv failed for {old_note_id!r} → {new_note_id!r}: "
          f"{exc.stderr.strip() or exc.stdout.strip() or exc}"
        ) from exc
      # Auto-commit the rename (drain 2026-07-24-1500). Path-scoped so
      # unrelated staged changes are NOT swept in. Both old and new rels
      # are named so git commits the deletion + addition atomically.
      commit_message = message or (
        f"rename note {_stem(rel_old)} → {_stem(rel_new)}"
      )
      git_sha = _git_commit_paths(
        self.root, [rel_old, rel_new], commit_message,
      )
      return new_path, git_sha, commit_message
    else:
      old_path.rename(new_path)
    return new_path, None, None

  def delete_note(
    self, note_id: str, message: str | None = None,
  ) -> tuple[Path, str | None, str | None]:
    """Delete a note from this vault.

    Path validated via `note_path`. If the vault is git-tracked, uses
    `git rm` (stages the removal, then commits); else plain
    `Path.unlink`.

    On git-tracked vaults, this method DESTROYS any unstaged working-
    tree modifications to the target file before removing it
    (`git checkout HEAD -- <rel>` precedes `git rm`). This handles the
    common case where an external process (e.g., the Obsidian plugin)
    has re-derived a facet between the MCP client's prior commit and
    this delete call. Delete semantics: remove the note entirely,
    including any transient working-tree state. If HEAD has no such
    file (uncommitted / just-added-never-committed), falls through to
    plain `Path.unlink()`.

    CW-forge-mcp-delete-and-rename-note-auto-commit (drain 2026-07-24-1500):
    On git-tracked vaults where the file was tracked in HEAD (so
    `git rm` actually ran), we now commit the removal immediately using
    a path-scoped `git commit -- <rel>` so unrelated staged changes
    elsewhere in the vault are NOT swept in. If a caller-supplied
    `message` is None, defaults to `delete note <note_id>`.

    Returns `(path, git_sha_or_None, commit_message_or_None)`. git_sha
    is None when the vault isn't git-tracked OR when HEAD didn't track
    the file (nothing was committed).

    Raises NoteNotFound if the note doesn't exist.
    Raises NoteIdInvalid on traversal / shape violations.
    """
    path = self.note_path(note_id)
    if not path.is_file():
      raise NoteNotFound(f"note {note_id!r} not found at {path}")
    if _is_git_tracked(self.root):
      rel = path.relative_to(self.root)
      # CW-forge-delete-note-handle-dirty-working-tree (drain 1800):
      # `git ls-tree HEAD -- <rel>` pre-check tells us whether HEAD
      # tracks this path. If it does, reset any unstaged working-tree
      # modifications before `git rm` (else `git rm` refuses with
      # "local modifications"). If HEAD does NOT track the path
      # (uncommitted / just-added-never-committed), skip the reset
      # and fall through to `Path.unlink()` — the file is effectively
      # untracked from HEAD's perspective and `git rm` would also
      # refuse. `ls-tree` is quiet (empty stdout when the path is
      # absent, non-empty when present) and returns 0 either way for
      # a valid ref, so we probe stdout rather than exit code.
      ls_tree = subprocess.run(
        ["git", "-C", str(self.root), "ls-tree", "HEAD", "--", str(rel)],
        capture_output=True, text=True, check=False,
      )
      in_head = ls_tree.returncode == 0 and ls_tree.stdout.strip() != ""
      if not in_head:
        # File never committed — no HEAD entry to reset from. Plain
        # unlink; git-status will then show "?? <rel>" cleaned.
        path.unlink()
        return path, None, None
      # File is tracked in HEAD. Reset any working-tree drift first.
      try:
        subprocess.run(
          ["git", "-C", str(self.root), "checkout", "HEAD", "--", str(rel)],
          capture_output=True, text=True, check=True,
        )
      except subprocess.CalledProcessError as exc:
        raise VaultFSError(
          f"git checkout HEAD failed for {note_id!r} (pre-delete reset): "
          f"{exc.stderr.strip() or exc.stdout.strip() or exc}"
        ) from exc
      try:
        subprocess.run(
          ["git", "-C", str(self.root), "rm", "--", str(rel)],
          capture_output=True, text=True, check=True,
        )
      except subprocess.CalledProcessError as exc:
        raise VaultFSError(
          f"git rm failed for {note_id!r}: "
          f"{exc.stderr.strip() or exc.stdout.strip() or exc}"
        ) from exc
      # Auto-commit the deletion (drain 2026-07-24-1500). Path-scoped
      # so unrelated staged changes are NOT swept in.
      commit_message = message or f"delete note {_stem(rel)}"
      git_sha = _git_commit_paths(self.root, [rel], commit_message)
      return path, git_sha, commit_message
    else:
      path.unlink()
    return path, None, None

  # -- Listing (for forge_read_notes_in_vault) ------------------------------

  def list_notes(self, filter: str | None = None) -> list[dict]:
    """Walk the vault dir + return a list-of-dicts summary of every
    `.md` note. Result is sorted by `note_id`.

    Drain CW-MCP-2-E — replaces the Sprint 1 HTTP proxy that pointed at
    a non-existent `/vault/notes` endpoint on forge-transpile. Reads
    from the same vault that `commit_recipe` writes to → symmetric
    surface.

    Filter is a plain substring match on `note_id` (case-sensitive).
    None returns all notes.

    Skips any path segment starting with `.` (`.obsidian/`, `.trash/`,
    `.git/`, etc.) — these are editor / vcs internals, not agent-
    writable content.

    Each dict has:
      * `note_id`: path relative to vault root, minus `.md` extension.
      * `name`: bare filename stem.
      * `path`: full vault-relative path including `.md`.
      * `has_recipe`: True iff the note has a `# Recipe` (or legacy
        `# E--`) facet section.
      * `recipe_version`: the `recipe_version` frontmatter stamp as
        an int, OR None when the stamp is absent (never committed via
        forge_commit_recipe).

    Notes that aren't parseable as V2a (random prose, corrupt YAML,
    etc.) are still included with `has_recipe=False, recipe_version=None`
    — one bad note shouldn't break the whole listing.
    """
    entries: list[dict] = []
    for path in self.root.rglob("*.md"):
      try:
        rel = path.relative_to(self.root)
      except ValueError:
        # Symlink escape — skip.
        continue
      if any(part.startswith(".") for part in rel.parts):
        continue
      note_id = str(rel.with_suffix(""))
      if filter is not None and filter not in note_id:
        continue
      has_recipe = False
      recipe_version: int | None = None
      sync_state: str | None = None
      # Drain 2026-07-26-1200 — surface `type` per entry. Default to
      # `vanilla` on unreadable/unparseable files (same fallback as
      # `has_recipe=False`).
      note_type = "vanilla"
      try:
        raw = path.read_text(encoding="utf-8")
        parsed = parse_note(raw)
        has_recipe = parsed.has_recipe_facet
        stamp = parsed.frontmatter_dict.get("recipe_version")
        if stamp is not None:
          try:
            recipe_version = int(stamp)
          except ValueError:
            recipe_version = None
        # Drain 2026-07-23-1700 Phase 1 — sync_state per note.
        # str | None; whatever the plugin wrote (or None if absent).
        raw_sync = parsed.frontmatter_dict.get("sync_state")
        if isinstance(raw_sync, str):
          sync_state = raw_sync
        note_type = _classify_note_type(parsed.frontmatter_dict)
      except (OSError, UnicodeDecodeError):
        # Unreadable file (permission, binary content mislabeled as
        # .md) — surface as unparseable, don't fail the listing.
        pass
      entries.append({
        "note_id": note_id,
        "name": rel.stem,
        "path": str(rel),
        "has_recipe": has_recipe,
        "recipe_version": recipe_version,
        "sync_state": sync_state,
        "type": note_type,
      })
    entries.sort(key=lambda e: e["note_id"])
    return entries

  # -- Versioned Recipe fetch (for the forge-recipe:/// resource) -----------

  def read_recipe_version(self, note_id: str, version: int) -> str | None:
    """Return the Recipe body as it was at the git commit that stamped
    `recipe_version: {version}`. Returns None if the vault isn't git-
    tracked, OR if no matching commit was found.

    Naive strategy: `git log` the note file, grep the log for the
    matching version stamp in the commit message, `git show
    <sha>:{relpath}` to get the file at that commit, parse for the
    Recipe body. Sufficient for the drain's scope (Sprint 2 exit-
    criteria smoke); a more principled versioning story is Sprint 3+.
    """
    if not _is_git_tracked(self.root):
      return None
    path = self.note_path(note_id)
    rel = path.relative_to(self.root)
    # `git log --format=%H %s -- <path>` lists commits touching the file
    # with subject lines. Match the version stamp in our commit-message
    # convention.
    try:
      result = subprocess.run(
        ["git", "-C", str(self.root), "log", "--format=%H %s", "--", str(rel)],
        capture_output=True, text=True, check=True,
      )
    except subprocess.CalledProcessError:
      return None
    marker = f"v{version}"
    for line in result.stdout.splitlines():
      sha, _, subject = line.partition(" ")
      # Match `... v3` at end of subject (or `... v3 ` with trailing
      # whitespace) — the commit-message convention above.
      if subject.rstrip().endswith(marker):
        try:
          show = subprocess.run(
            ["git", "-C", str(self.root), "show", f"{sha}:{rel}"],
            capture_output=True, text=True, check=True,
          )
        except subprocess.CalledProcessError:
          return None
        parsed = parse_note(show.stdout)
        return parsed.recipe_body.rstrip("\n") if parsed.has_recipe_facet else None
    return None


# ---------------------------------------------------------------------------
# Filesystem + git helpers
# ---------------------------------------------------------------------------


def _atomic_write(path: Path, content: str) -> None:
  """Write to a temp file next to `path`, then rename over. Atomic
  within a single filesystem; guards against half-written files if the
  process is killed mid-write."""
  tmp = path.with_suffix(path.suffix + ".tmp")
  tmp.write_text(content, encoding="utf-8")
  tmp.replace(path)


def _is_git_tracked(root: Path) -> bool:
  return (root / ".git").exists()


def _git_commit_file(root: Path, path: Path, message: str) -> str | None:
  """`git add <path>` + `git commit -m <message>`. Returns the commit
  SHA on success; None on any failure (no exception — commits are
  best-effort per drain §6).

  Explicit args (not `git commit -am`) so we don't sweep in unrelated
  working-tree changes.
  """
  rel = path.relative_to(root)
  try:
    subprocess.run(
      ["git", "-C", str(root), "add", "--", str(rel)],
      capture_output=True, text=True, check=True,
    )
    subprocess.run(
      ["git", "-C", str(root), "commit", "-m", message, "--", str(rel)],
      capture_output=True, text=True, check=True,
    )
    sha_res = subprocess.run(
      ["git", "-C", str(root), "rev-parse", "HEAD"],
      capture_output=True, text=True, check=True,
    )
    return sha_res.stdout.strip() or None
  except subprocess.CalledProcessError:
    return None


def _stem(rel: Path) -> str:
  """Vault-relative `.md`-less path for use in default commit messages."""
  s = str(rel)
  return s[:-3] if s.endswith(".md") else s


def _classify_note_type(frontmatter: dict) -> str:
  """Discriminated note type per drain 2026-07-26-1200.

  Frontmatter `type` field values seen in the wild:
    - `"action"`: Forge action note (Description/Recipe/Python facets).
    - `"data"`: Forge data note (`# Data` facet or pure JSON body).
    - anything else / absent: vanilla prose note (no Forge structure).

  Returns one of `"action"` | `"data"` | `"vanilla"`. Unknown `type`
  values collapse to `"vanilla"` so future/typo values fail safely
  (won't accidentally trigger action-note pipelines).
  """
  raw = frontmatter.get("type")
  if raw == "action":
    return "action"
  if raw == "data":
    return "data"
  return "vanilla"


def _git_commit_paths(
  root: Path, rels: list[Path], message: str,
) -> str | None:
  """Path-scoped `git commit -m <message> -- <rel...>`. Returns the
  commit SHA on success; None on any failure (delete/rename remain
  best-effort at the commit layer — the git operation already staged
  the change and a manual commit will pick it up if this fails).

  Path-scoped commit deliberately does NOT touch unrelated staged
  changes elsewhere in the vault. This matches
  forge_commit_recipe's contract (drain §5 acceptance-criteria #2).

  Unlike `_git_commit_file`, we do NOT run `git add` — for delete/rename
  the operations that produced these staged changes (`git rm` / `git mv`)
  already staged them, and running `git add` on a deleted-in-index path
  is either a no-op (with modern git's `--all` inference) or a footgun
  depending on git version. Path-scoped commit picks up the staged
  changes on those rels directly.
  """
  try:
    subprocess.run(
      ["git", "-C", str(root), "commit", "-m", message, "--"]
      + [str(r) for r in rels],
      capture_output=True, text=True, check=True,
    )
    sha_res = subprocess.run(
      ["git", "-C", str(root), "rev-parse", "HEAD"],
      capture_output=True, text=True, check=True,
    )
    return sha_res.stdout.strip() or None
  except subprocess.CalledProcessError:
    return None
