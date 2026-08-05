r"""S9 hexa-state facet hashing, byte-compatible with the plugin.

Drain 2026-08-05-0720. The plugin has stamped these hashes reactively
since drain 1700; this is the MCP-side twin so a note authored entirely
through MCP carries the same frontmatter as one touched in Obsidian.

Parity target: `forge-client-obsidian/src/description-hash-core.ts`
(`computeDescriptionHash`), which `facet-hash-core.ts:computeFacetHash`
delegates to for all three facets.

Normalization, in order:
  1. rstrip each line
  2. drop leading and trailing fully-blank lines
  3. keep internal blank lines — paragraph breaks are meaning
  4. sha256 the UTF-8 bytes, lowercase hex

WHY THIS ISN'T `forge.core.slot_cache.compute_english_hash`
-----------------------------------------------------------
That function implements steps 1-4 too, and is itself parity-tested
against `english-hash-core.ts`. Reusing it was the obvious move, and it
is wrong by one character.

Both TypeScript files strip trailing whitespace with
`/[\s﻿\xA0]+$/`, which names U+FEFF explicitly. Python's
`str.rstrip()` does not treat U+FEFF as whitespace, so it keeps it.
Feed a body ending in a BOM and the two languages disagree:

    "hello﻿"   TS -> 2cf24dba5fb0a30e...   PY -> 459e1247f62ffaac...

Nine other cases — empty, trailing spaces, blank edges, internal
blanks, NBSP, tabs, non-ASCII, multi-line Python — agree exactly. Only
U+FEFF diverges, and neither side's parity tests feed one, which is why
it has gone unnoticed.

That divergence predates this drain and also affects the shipped
`english_hash`. Changing `compute_english_hash` would re-key a live
slot cache, so it is reported for its own drain rather than fixed here
in passing. This module strips U+FEFF, matching the plugin, because the
plugin is the side already writing these fields.
"""
from __future__ import annotations

import hashlib

# Characters the plugin's `[\s﻿\xA0]+$` strips that Python's
# `str.rstrip()` does not. NBSP (\xa0) IS Python whitespace, so it needs
# no help; U+FEFF is not, so name it. Listed rather than inlined so the
# reason survives the next reader.
_EXTRA_TRAILING = "﻿"


def normalize_facet_text(text: str | None) -> str:
  """Plugin-compatible normalization, exposed for tests and callers
  that want to see what is about to be hashed."""
  if text is None:
    text = ""
  if not isinstance(text, str):
    raise TypeError(f"text must be str or None, got {type(text).__name__}")

  lines = [line.rstrip().rstrip(_EXTRA_TRAILING).rstrip() for line in text.split("\n")]
  while lines and lines[0] == "":
    lines.pop(0)
  while lines and lines[-1] == "":
    lines.pop()
  return "\n".join(lines)


def compute_facet_hash(text: str | None) -> str:
  """Lowercase hex sha256 of the normalized facet body.

  `None` and `""` both hash to the empty-string hash so callers don't
  special-case an absent facet — an absent Python facet and an empty
  one mean the same thing to the state machine.
  """
  return hashlib.sha256(normalize_facet_text(text).encode("utf-8")).hexdigest()
