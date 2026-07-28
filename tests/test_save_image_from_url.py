"""CW-forge-mcp-save-image-from-url-tool (drain 2026-07-28-1400)."""
from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest
import respx

from forge_mcp.tools import save_image_from_url
from forge_mcp.vault_fs import VaultFS
from forge_mcp.vault_registry import VaultRegistry


_PNG_MAGIC = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24


@pytest.fixture
def single_vault_registry(tmp_path: Path) -> VaultRegistry:
  vault = tmp_path / "vault"
  vault.mkdir()
  return VaultRegistry({"default": VaultFS(root=vault)})


@pytest.mark.asyncio
async def test_saves_image_from_https_url(single_vault_registry: VaultRegistry):
  """Acceptance #2: happy path — downloads and writes the file, returns metadata."""
  async with respx.mock(base_url="https://example.com") as mock:
    mock.get("/scale.png").mock(
      return_value=httpx.Response(200, content=_PNG_MAGIC, headers={"content-type": "image/png"}),
    )
    async with httpx.AsyncClient() as client:
      result = await save_image_from_url.run(
        arguments={
          "url": "https://example.com/scale.png",
          "target_path": "music_theory/images/c_major.png",
        },
        bearer="tok",
        vault_registry=single_vault_registry,
        client=client,
      )
  assert result["isError"] is False, result
  vault_fs = single_vault_registry.get()
  saved = vault_fs.root / "music_theory" / "images" / "c_major.png"
  assert saved.is_file()
  assert saved.read_bytes() == _PNG_MAGIC
  structured = result["structuredContent"]
  assert structured["vault"] == "default"
  assert structured["path"] == "music_theory/images/c_major.png"
  assert structured["absolute_path"] == str(saved)
  assert structured["size_bytes"] == len(_PNG_MAGIC)
  assert structured["sha256"] == hashlib.sha256(_PNG_MAGIC).hexdigest()
  assert structured["content_type"] == "image/png"
  assert structured["url"] == "https://example.com/scale.png"


@pytest.mark.asyncio
async def test_refuses_non_image_content_type(single_vault_registry: VaultRegistry):
  """Acceptance #3: HTML page rejected because content-type isn't image/*."""
  async with respx.mock(base_url="https://example.com") as mock:
    mock.get("/page").mock(
      return_value=httpx.Response(200, content=b"<html>hello</html>", headers={"content-type": "text/html; charset=utf-8"}),
    )
    async with httpx.AsyncClient() as client:
      result = await save_image_from_url.run(
        arguments={
          "url": "https://example.com/page",
          "target_path": "images/oops.png",
        },
        bearer="tok",
        vault_registry=single_vault_registry,
        client=client,
      )
  assert result["isError"] is True
  text = result["content"][0]["text"]
  assert "text/html" in text
  assert "image/" in text
  # No file written.
  vault_fs = single_vault_registry.get()
  assert not (vault_fs.root / "images" / "oops.png").exists()


@pytest.mark.asyncio
async def test_refuses_path_traversal(single_vault_registry: VaultRegistry):
  """Acceptance #4: `../../../etc/passwd` rejected before any HTTP call."""
  result = await save_image_from_url.run(
    arguments={
      "url": "https://example.com/x.png",
      "target_path": "../../../etc/passwd",
    },
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is True
  text = result["content"][0]["text"].lower()
  assert "target_path" in text
  assert "forbidden" in text or "traversal" in text or ".." in text


@pytest.mark.asyncio
async def test_refuses_overwrite_by_default(single_vault_registry: VaultRegistry, tmp_path: Path):
  """Acceptance #5a: existing file blocks the write unless overwrite=True.

  Tool short-circuits before any HTTP call — no mock needed.
  """
  vault_fs = single_vault_registry.get()
  existing = vault_fs.root / "images" / "scale.png"
  existing.parent.mkdir(parents=True)
  existing.write_bytes(b"original")
  result = await save_image_from_url.run(
    arguments={
      "url": "https://example.com/x.png",
      "target_path": "images/scale.png",
    },
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is True
  assert existing.read_bytes() == b"original"
  assert "overwrite=True" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_overwrite_true_replaces_file(single_vault_registry: VaultRegistry):
  """Acceptance #5b: overwrite=True replaces the file atomically."""
  vault_fs = single_vault_registry.get()
  existing = vault_fs.root / "images" / "scale.png"
  existing.parent.mkdir(parents=True)
  existing.write_bytes(b"original")
  async with respx.mock(base_url="https://example.com") as mock:
    mock.get("/x.png").mock(
      return_value=httpx.Response(200, content=_PNG_MAGIC, headers={"content-type": "image/png"}),
    )
    async with httpx.AsyncClient() as client:
      result = await save_image_from_url.run(
        arguments={
          "url": "https://example.com/x.png",
          "target_path": "images/scale.png",
          "overwrite": True,
        },
        bearer="tok",
        vault_registry=single_vault_registry,
        client=client,
      )
  assert result["isError"] is False, result
  assert existing.read_bytes() == _PNG_MAGIC


@pytest.mark.asyncio
async def test_refuses_size_over_max(single_vault_registry: VaultRegistry):
  """Acceptance #6: >max_size_mb rejected before write."""
  big = b"\x89PNG" + b"\x00" * (1024 * 1024)  # ~1 MB payload
  async with respx.mock(base_url="https://example.com") as mock:
    mock.get("/big.png").mock(
      return_value=httpx.Response(200, content=big, headers={"content-type": "image/png"}),
    )
    async with httpx.AsyncClient() as client:
      result = await save_image_from_url.run(
        arguments={
          "url": "https://example.com/big.png",
          "target_path": "images/big.png",
          "max_size_mb": 0.5,  # 500 KB limit; 1 MB payload exceeds
        },
        bearer="tok",
        vault_registry=single_vault_registry,
        client=client,
      )
  assert result["isError"] is True
  assert "max_size_mb" in result["content"][0]["text"]
  vault_fs = single_vault_registry.get()
  assert not (vault_fs.root / "images" / "big.png").exists()


@pytest.mark.asyncio
async def test_creates_parent_directories(single_vault_registry: VaultRegistry):
  """Acceptance #7: nested target path auto-creates intermediate directories."""
  vault_fs = single_vault_registry.get()
  assert not (vault_fs.root / "music_theory").exists()
  async with respx.mock(base_url="https://example.com") as mock:
    mock.get("/x.png").mock(
      return_value=httpx.Response(200, content=_PNG_MAGIC, headers={"content-type": "image/png"}),
    )
    async with httpx.AsyncClient() as client:
      result = await save_image_from_url.run(
        arguments={
          "url": "https://example.com/x.png",
          "target_path": "music_theory/images/deep/nested/scale.png",
        },
        bearer="tok",
        vault_registry=single_vault_registry,
        client=client,
      )
  assert result["isError"] is False, result
  saved = vault_fs.root / "music_theory" / "images" / "deep" / "nested" / "scale.png"
  assert saved.is_file()


@pytest.mark.asyncio
async def test_sha256_matches_content(single_vault_registry: VaultRegistry):
  """Acceptance #8: returned sha256 matches the downloaded bytes."""
  payload = b"\x89PNGrandomcontent" + b"\x42" * 128
  expected_sha = hashlib.sha256(payload).hexdigest()
  async with respx.mock(base_url="https://example.com") as mock:
    mock.get("/x.png").mock(
      return_value=httpx.Response(200, content=payload, headers={"content-type": "image/png"}),
    )
    async with httpx.AsyncClient() as client:
      result = await save_image_from_url.run(
        arguments={
          "url": "https://example.com/x.png",
          "target_path": "images/scale.png",
        },
        bearer="tok",
        vault_registry=single_vault_registry,
        client=client,
      )
  assert result["isError"] is False, result
  assert result["structuredContent"]["sha256"] == expected_sha


# ---------------------------------------------------------------------------
# Extra safety tests (not part of the numbered acceptance-criteria set but
# spec'd in §Safety requirements).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refuses_http_url_by_default(single_vault_registry: VaultRegistry):
  """§Safety: http:// refused unless insecure=True."""
  result = await save_image_from_url.run(
    arguments={
      "url": "http://example.com/x.png",
      "target_path": "images/x.png",
    },
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is True
  assert "insecure=True" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_accepts_http_when_insecure_flag_set(single_vault_registry: VaultRegistry):
  """§Safety: insecure=True unlocks plain http:// for localhost testing."""
  async with respx.mock(base_url="http://localhost:8000") as mock:
    mock.get("/x.png").mock(
      return_value=httpx.Response(200, content=_PNG_MAGIC, headers={"content-type": "image/png"}),
    )
    async with httpx.AsyncClient() as client:
      result = await save_image_from_url.run(
        arguments={
          "url": "http://localhost:8000/x.png",
          "target_path": "images/x.png",
          "insecure": True,
        },
        bearer="tok",
        vault_registry=single_vault_registry,
        client=client,
      )
  assert result["isError"] is False, result


@pytest.mark.asyncio
async def test_refuses_md_extension(single_vault_registry: VaultRegistry):
  """§Safety: `.md` targets refused (this is for images, not markdown)."""
  result = await save_image_from_url.run(
    arguments={
      "url": "https://example.com/x.png",
      "target_path": "notes/vanilla.md",
    },
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is True
  assert ".md" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_bad_status_code_surfaces_error(single_vault_registry: VaultRegistry):
  """§Safety: non-200 responses reported instead of written to disk."""
  async with respx.mock(base_url="https://example.com") as mock:
    mock.get("/missing.png").mock(
      return_value=httpx.Response(404, content=b"not found"),
    )
    async with httpx.AsyncClient() as client:
      result = await save_image_from_url.run(
        arguments={
          "url": "https://example.com/missing.png",
          "target_path": "images/x.png",
        },
        bearer="tok",
        vault_registry=single_vault_registry,
        client=client,
      )
  assert result["isError"] is True
  assert "404" in result["content"][0]["text"]
