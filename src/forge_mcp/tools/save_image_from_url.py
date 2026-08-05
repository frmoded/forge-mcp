"""`forge_save_image_from_url` — download an image URL and save into a vault.

CW-forge-mcp-save-image-from-url-tool (drain 2026-07-28-1400).

Vanilla theory notes in the music vault need images (staff diagrams,
screenshots, chord charts). The wizard is a blind lane — no filesystem
writes, no browser control, no image generation. Driver produces images
out-of-band, hosts them somewhere accessible (imgur, dropbox, github raw,
temporary CDN), and sends the URL to the wizard. This tool downloads the
URL, validates content-type + size, saves under vault root at a caller-
specified relative path, and returns metadata for provenance / cache
validation. The wizard then embeds the image via `![[<path>]]` in the
target vanilla note.

Safety:
- HTTPS-only by default; `insecure=True` opens http:// for localhost.
- Content-Type must start with `image/` (rejects HTML pages, etc.).
- Size cap `max_size_mb` (default 10) enforced before write.
- target_path sanitized: no absolute paths, no `..` segments, no hidden
  segments, no NUL, no `.md` extension (this tool is for image assets).
- Refuses overwrite unless `overwrite=True`.
- Creates parent directories as needed (mkdir -p).
- 30-second HTTP timeout.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import httpx

from ..vault_registry import VaultNotFoundError, VaultRegistry

TOOL_NAME = "forge_save_image_from_url"

_DEFAULT_MAX_SIZE_MB = 10.0
_HTTP_TIMEOUT_SECONDS = 30.0

# Mirrors vault_fs._NOTE_ID_SEGMENT so image filenames follow the same
# character allowlist as note ids. `.png`, `.jpeg`, `.svg` etc. are fine
# because `.` is in the allowlist.
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_.\-][A-Za-z0-9_.\- ]*$")


INPUT_SCHEMA: dict[str, Any] = {
  "type": "object",
  "required": ["url", "target_path"],
  "properties": {
    "url": {
      "type": "string",
      "description": (
        "HTTPS URL of the image to download. Plain http:// is refused "
        "unless `insecure=True` (for localhost testing)."
      ),
    },
    "target_path": {
      "type": "string",
      "description": (
        "Vault-relative destination path, e.g. "
        "`music_theory/images/c_major_scale.png`. Absolute paths, `..` "
        "segments, hidden segments (starting with `.`), and `.md` "
        "extensions are rejected."
      ),
    },
    "vault": {
      "type": "string",
      "description": (
        "Vault name (from forge_list_vaults). Optional — defaults to the "
        "first-registered vault."
      ),
    },
    "overwrite": {
      "type": "boolean",
      "description": "If false (default), refuse when target_path already exists.",
    },
    "insecure": {
      "type": "boolean",
      "description": "Accept plain http:// URLs. Default false — HTTPS-only.",
    },
    "max_size_mb": {
      "type": "number",
      "description": "Max download size in MB. Default 10.",
    },
  },
}

OUTPUT_SCHEMA: dict[str, Any] = {
  "type": "object",
  "required": [
    "vault", "path", "absolute_path",
    "size_bytes", "sha256", "content_type", "url",
  ],
  "properties": {
    "vault": {"type": "string"},
    "path": {"type": "string"},
    "absolute_path": {"type": "string"},
    "size_bytes": {"type": "integer"},
    "sha256": {"type": "string"},
    "content_type": {"type": "string"},
    "url": {"type": "string"},
  },
}

DESCRIPTION = (
  "Download an image from an HTTP(S) URL and save it into a vault at a "
  "caller-specified vault-relative path. Refuses non-image content, "
  "path traversal, `.md` extensions, and downloads larger than "
  "max_size_mb (default 10). Refuses to overwrite an existing file "
  "unless overwrite=True. Creates parent directories as needed. Returns "
  "absolute path, size, SHA-256 hash, content-type, and echoes the "
  "source URL for provenance. HTTPS-only by default; pass insecure=True "
  "to accept plain http:// (dev/localhost)."
  "Auto-commits the written file when the vault is git-tracked and returns git_sha (null if untracked or the commit failed — the file is written either way)."
)


def _error(text: str, *, vault: str = "", url: str = "") -> dict[str, Any]:
  return {
    "content": [{"type": "text", "text": text}],
    "structuredContent": {
      "vault": vault,
      "path": "",
      "absolute_path": "",
      "size_bytes": 0,
      "sha256": "",
      "content_type": "",
      "url": url,
    },
    "isError": True,
  }


def _validate_target_path(path: str) -> Path:
  """Return the validated relative Path or raise ValueError with a clear message."""
  if not path or not path.strip():
    raise ValueError("target_path is empty")
  if "\x00" in path:
    raise ValueError("target_path contains NUL byte")
  if path.startswith("/"):
    raise ValueError(f"target_path must be vault-relative, got {path!r}")
  if path.lower().endswith(".md"):
    raise ValueError(
      f"target_path {path!r} has a `.md` extension. This tool is for "
      "image assets — use forge_create_markdown_note or "
      "forge_commit_recipe for markdown files."
    )
  for seg in path.split("/"):
    if seg in ("", ".", ".."):
      raise ValueError(
        f"target_path {path!r} contains a forbidden segment {seg!r}"
      )
    if seg.startswith("."):
      raise ValueError(
        f"target_path {path!r} refers to a hidden segment {seg!r}"
      )
    if not _SEGMENT_RE.match(seg):
      raise ValueError(
        f"target_path {path!r} contains a segment with unsupported "
        f"characters: {seg!r}"
      )
  return Path(path)


async def run(
  arguments: dict[str, Any],
  bearer: str,  # noqa: ARG001 — no upstream forge-transpile call
  vault_registry: VaultRegistry,
  *,
  client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
  """Handler for forge_save_image_from_url.

  `client` is a DI seam for tests (respx.mock patches the transport of
  a passed-in client, or a default one when None).
  """
  url = arguments.get("url")
  target_path = arguments.get("target_path")
  vault_name = arguments.get("vault")
  overwrite = bool(arguments.get("overwrite", False))
  insecure = bool(arguments.get("insecure", False))
  max_size_mb_raw = arguments.get("max_size_mb", _DEFAULT_MAX_SIZE_MB)

  if not isinstance(url, str) or not url.strip():
    return _error("Missing required argument: 'url'.", vault=str(vault_name or ""))
  if not isinstance(target_path, str) or not target_path.strip():
    return _error(
      "Missing required argument: 'target_path'.",
      vault=str(vault_name or ""), url=url,
    )
  try:
    max_size_mb = float(max_size_mb_raw)
    if max_size_mb <= 0:
      raise ValueError("must be positive")
  except (TypeError, ValueError):
    return _error(
      f"'max_size_mb' must be a positive number, got {max_size_mb_raw!r}.",
      vault=str(vault_name or ""), url=url,
    )
  max_size_bytes = int(max_size_mb * 1024 * 1024)

  scheme = url.split("://", 1)[0].lower() if "://" in url else ""
  if scheme == "https":
    pass
  elif scheme == "http":
    if not insecure:
      return _error(
        "URL uses http:// — refused by default. Pass insecure=True for "
        "localhost / testing URLs.",
        vault=str(vault_name or ""), url=url,
      )
  else:
    return _error(
      f"URL scheme {scheme!r} is not supported. Only http(s):// URLs are accepted.",
      vault=str(vault_name or ""), url=url,
    )

  try:
    vault_fs = vault_registry.get(vault_name)
  except VaultNotFoundError as exc:
    return _error(str(exc), vault=str(vault_name or ""), url=url)
  if vault_name is None or vault_name == "":
    vault_name = vault_registry.names()[0]

  try:
    rel = _validate_target_path(target_path)
  except ValueError as exc:
    return _error(f"Invalid target_path: {exc}", vault=vault_name, url=url)

  abs_path = (vault_fs.root / rel).resolve()
  try:
    abs_path.relative_to(vault_fs.root)
  except ValueError:
    return _error(
      f"target_path {target_path!r} resolves outside vault root {vault_fs.root}.",
      vault=vault_name, url=url,
    )

  if abs_path.exists() and not overwrite:
    return _error(
      f"Refusing to overwrite existing file at {abs_path}. Pass "
      "overwrite=True to replace.",
      vault=vault_name, url=url,
    )

  own_client = client is None
  http_client = client if client is not None else httpx.AsyncClient(
    timeout=_HTTP_TIMEOUT_SECONDS, follow_redirects=True,
  )
  try:
    try:
      response = await http_client.get(url)
    except httpx.TimeoutException:
      return _error(
        f"Download timed out after {_HTTP_TIMEOUT_SECONDS:.0f}s: {url}",
        vault=vault_name, url=url,
      )
    except httpx.HTTPError as exc:
      return _error(f"HTTP request failed: {exc}", vault=vault_name, url=url)

    if response.status_code != 200:
      body_preview = response.text[:200] if response.text else ""
      return _error(
        f"HTTP {response.status_code} from {url}: {body_preview}",
        vault=vault_name, url=url,
      )

    content_type_full = response.headers.get("content-type", "").strip()
    content_type = content_type_full.split(";", 1)[0].strip().lower()
    if not content_type.startswith("image/"):
      return _error(
        f"Content-Type {content_type_full!r} is not an image type. Expected "
        "content-type starting with 'image/' (e.g. image/png, image/jpeg, "
        "image/svg+xml).",
        vault=vault_name, url=url,
      )

    data = response.content
  finally:
    if own_client:
      await http_client.aclose()

  size_bytes = len(data)
  if size_bytes > max_size_bytes:
    return _error(
      f"Downloaded {size_bytes} bytes exceeds max_size_mb={max_size_mb} "
      f"({max_size_bytes} bytes). Increase max_size_mb to accept larger files.",
      vault=vault_name, url=url,
    )

  abs_path.parent.mkdir(parents=True, exist_ok=True)
  tmp = abs_path.with_suffix(abs_path.suffix + ".tmp")
  tmp.write_bytes(data)
  tmp.replace(abs_path)

  sha256 = hashlib.sha256(data).hexdigest()
  rel_path_str = str(abs_path.relative_to(vault_fs.root))

  structured = {
    "vault": vault_name,
    "path": rel_path_str,
    "absolute_path": str(abs_path),
    "size_bytes": size_bytes,
    "sha256": sha256,
    "content_type": content_type,
    "url": url,
  }
  # drain 2026-07-31-1130 — auto-commit downloaded assets.
  _commit = vault_fs.auto_commit(
    abs_path, f"forge_save_image_from_url: {rel_path_str}",
    expected_content=abs_path.read_bytes(),
  )
  git_sha = _commit.git_sha
  structured["git_sha"] = git_sha
  structured["foreign_changes_detected"] = _commit.foreign_changes_detected
  structured["foreign_changes_summary"] = _commit.foreign_changes_summary
  commit_note = (
    f" Committed {git_sha[:8]}." if git_sha
    else " Not committed (vault not git-tracked)."
  )
  return {
    "content": [{
      "type": "text",
      "text": (
        f"Saved {size_bytes} bytes to {rel_path_str!r} in vault "
        f"{vault_name!r} (sha256={sha256[:12]}, content-type={content_type})."
        f"{commit_note}"
      ),
    }],
    "structuredContent": structured,
    "isError": False,
  }
