"""`forge_create_asset` — write NEW caller-supplied content to an asset path.

Drain 2026-08-14-0330. Wizard was blocked authoring four hand-drafted SVG
illustrations for the physics note cluster: `forge_move_asset` /
`forge_copy_asset` (drain 0250) only relocate files that already exist,
`forge_save_image_from_url` needs a URL, and the markdown-note tools force a
`.md` extension. Nothing could put drafted content at an asset path.

Same safety family as move/copy: `dest_path` resolves through the shared
`asset_path()` (traversal + symlink-escape + extension allowlist), the tool
refuses to overwrite, and it stages with `git add` on tracked vaults without
committing.

`content_encoding` is REQUIRED and explicit. Inferring it from the extension
would be fragile — `.svg` is text while most other allowlisted extensions are
binary — and a wrong guess corrupts the file silently.
"""
from __future__ import annotations

import base64
import binascii
from typing import Any

from ..schemas import CreateAssetResult
from ..vault_fs import NoteExists, NoteIdInvalid, VaultFSError
from ..vault_registry import VaultNotFoundError, VaultRegistry

TOOL_NAME = "forge_create_asset"

_ENCODINGS = ("text", "base64")

INPUT_SCHEMA: dict[str, Any] = {
  "type": "object",
  "required": ["content", "dest_path", "content_encoding"],
  "properties": {
    "content": {
      "type": "string",
      "description": (
        "The asset content. Raw text when `content_encoding` is `text` "
        "(e.g. SVG markup); a base64 string when it is `base64`."
      ),
    },
    "dest_path": {
      "type": "string",
      "description": (
        "Vault-relative destination path (e.g. "
        "`note/resources/images/frequency.svg`). Parent directories are "
        "created if missing. Must not already exist."
      ),
    },
    "content_encoding": {
      "type": "string",
      "enum": list(_ENCODINGS),
      "description": (
        "How to interpret `content`: `text` writes it as UTF-8, `base64` "
        "decodes it to raw bytes first. Required — never inferred from "
        "the file extension."
      ),
    },
    "vault": {
      "type": "string",
      "description": (
        "Vault name (from forge_list_vaults). Optional — defaults to "
        "the first-registered vault."
      ),
    },
  },
}

OUTPUT_SCHEMA: dict[str, Any] = {
  "type": "object",
  "required": ["vault", "dest_path", "created", "staged"],
  "properties": {
    "vault": {"type": "string"},
    "dest_path": {"type": "string"},
    "created": {"type": "boolean"},
    "bytes_written": {"type": "integer"},
    "staged": {"type": "boolean"},
  },
}

DESCRIPTION = (
  "Write new caller-supplied content to a vault asset path (image/audio). "
  "Use for hand-authored assets such as SVG illustrations; use "
  "forge_save_image_from_url when the content comes from a URL, and "
  "forge_move_asset/forge_copy_asset to relocate a file that already "
  "exists. `content_encoding` must be `text` or `base64` and is never "
  "inferred from the extension. Destination parent directories are "
  "created automatically. Errors if the destination already exists — "
  "never overwrites. Only asset extensions are permitted (not `.md` "
  "notes). On git-tracked vaults the new file is `git add`ed but NOT "
  "committed. Pass `vault` to target a specific vault; omit for the "
  "first-registered."
)


def _error(text: str, *, vault: str, dest_path: str) -> dict[str, Any]:
  return {
    "content": [{"type": "text", "text": text}],
    "structuredContent": {
      "vault": vault,
      "dest_path": dest_path,
      "created": False,
      "bytes_written": 0,
      "staged": False,
    },
    "isError": True,
  }


async def run(
  arguments: dict[str, Any],
  bearer: str,  # noqa: ARG001 — no upstream call
  vault_registry: VaultRegistry,
) -> dict[str, Any]:
  content = arguments.get("content")
  dest_path = arguments.get("dest_path")
  encoding = arguments.get("content_encoding")
  vault_name = arguments.get("vault")

  if not isinstance(dest_path, str) or not dest_path.strip():
    return _error(
      "Missing required argument: 'dest_path' (vault-relative asset path).",
      vault=str(vault_name or ""), dest_path="",
    )
  if not isinstance(content, str):
    return _error(
      "Missing required argument: 'content' (the asset body, as text or base64).",
      vault=str(vault_name or ""), dest_path=dest_path,
    )
  if encoding not in _ENCODINGS:
    return _error(
      f"Invalid 'content_encoding': {encoding!r}. Must be one of "
      f"{', '.join(_ENCODINGS)}. It is required and never inferred from "
      f"the file extension.",
      vault=str(vault_name or ""), dest_path=dest_path,
    )

  # Decode BEFORE touching the filesystem, so bad input cannot leave a
  # half-written file behind.
  if encoding == "base64":
    try:
      data = base64.b64decode(content, validate=True)
    except (binascii.Error, ValueError) as exc:
      return _error(
        f"Could not decode 'content' as base64: {exc}. Nothing was written.",
        vault=str(vault_name or ""), dest_path=dest_path,
      )
  else:
    data = content.encode("utf-8")

  try:
    vault_fs = vault_registry.get(vault_name)
  except VaultNotFoundError as exc:
    return _error(str(exc), vault=str(vault_name or ""), dest_path=dest_path)

  if vault_name is None or vault_name == "":
    vault_name = vault_registry.names()[0]

  try:
    _dst, staged = vault_fs.create_asset(dest_path, data)
  except NoteExists as exc:
    return _error(
      f"Destination already exists: {exc}. Nothing was written — this tool "
      f"never overwrites. Choose another path, or delete the existing file "
      f"first.",
      vault=vault_name, dest_path=dest_path,
    )
  except NoteIdInvalid as exc:
    return _error(f"Invalid asset path: {exc}", vault=vault_name, dest_path=dest_path)
  except VaultFSError as exc:
    return _error(f"Asset creation failed: {exc}", vault=vault_name, dest_path=dest_path)

  result = CreateAssetResult(
    vault=vault_name,
    dest_path=dest_path,
    created=True,
    bytes_written=len(data),
    staged=staged,
  )
  suffix = " and staged with git add" if staged else ""
  return {
    "content": [
      {
        "type": "text",
        "text": (
          f"Created {dest_path!r} ({len(data)} bytes) in vault "
          f"{vault_name!r}{suffix}."
        ),
      }
    ],
    "structuredContent": result.model_dump(mode="json"),
    "isError": False,
  }
