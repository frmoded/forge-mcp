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


def splice_recipe(raw: str, new_recipe_body: str, new_version: int) -> str:
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
      content = (
        f"---\nrecipe_version: {new_version}\n---\n"
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
      new_content = splice_recipe(raw, new_recipe_body, new_version)
      _atomic_write(path, new_content)

    git_sha: str | None = None
    if _is_git_tracked(self.root):
      message = git_message or f"forge-mcp: commit recipe {note_id} v{new_version}"
      git_sha = _git_commit_file(self.root, path, message)

    return new_version, git_sha

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
    """Create a fresh note with minimal frontmatter + optional Description.
    NO Recipe facet — that's commit_recipe's job.

    Fails cleanly if the note already exists (raises NoteExists). Does
    NOT overwrite. If the parent directory doesn't exist, it is created
    (mkdir -p semantics).

    Returns the absolute path created.
    """
    path = self.note_path(note_id)
    if path.exists():
      raise NoteExists(note_id, path)
    # Minimal V2a shell: empty frontmatter + Description section.
    desc_body = description.strip()
    if desc_body:
      content = f"---\n---\n\n# Description\n\n{desc_body}\n"
    else:
      content = "---\n---\n\n# Description\n\n\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, content)
    return path

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
