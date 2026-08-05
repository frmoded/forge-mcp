"""Cross-language parity for `compute_facet_hash` (drain 2026-08-05-0720).

The expected values below were produced by running the PLUGIN's
`computeDescriptionHash` (description-hash-core.ts, which
facet-hash-core.ts:computeFacetHash delegates to for all three facets)
over each input. They are hardcoded on purpose: a parity test that
recomputes both sides in-process would pass even if both drifted
together, which is exactly the failure it exists to catch.

To regenerate after an intentional normalization change, run
computeDescriptionHash over CASES in the plugin repo and paste the
results — do not "fix" a red test by recomputing in Python.
"""
from __future__ import annotations

import pytest

from forge_mcp.facet_hash import compute_facet_hash, normalize_facet_text

# input -> hash emitted by the plugin's computeDescriptionHash
PLUGIN_HASHES = {
  "": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "hello": "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
  "hello   \nworld  ": "26c60a61d01db5836ca70fefd44a6a016620413c8ef5f259a6c5612d4f79d3b8",
  "\n\n hello \n\n": "1c9c535c80b3298829957b9fbfcd00bec337baca0866812efc749b7669613d43",
  "a\n\nb": "38022fd2b8dbc5cb3d2cee74e083edbf59e3d4e13d067ebcb5db633d4cff4d8c",
  "h\u00e9llo \u2014 w\u00f6rld": "9e0d798a47e0b3ea7fd35b63da728bcded244adf9a0e7cbf8a679f8b9ca796f3",
  "def compute(context):\n    return None\n": "e5d1a4fc162f7e942b790e395406201fb6b83935dfe1843a475fc8837c15f097",
}



def test_empty_and_none_agree_with_plugin():
  assert compute_facet_hash("") == PLUGIN_HASHES[""]
  assert compute_facet_hash(None) == PLUGIN_HASHES[""]


def test_simple_body_agrees_with_plugin():
  assert compute_facet_hash("hello") == PLUGIN_HASHES["hello"]


def test_trailing_whitespace_is_stripped_per_line():
  # "hello   \nworld  " must hash identically to "hello\nworld".
  assert compute_facet_hash("hello   \nworld  ") == compute_facet_hash("hello\nworld")


def test_leading_and_trailing_blank_lines_are_dropped():
  assert compute_facet_hash("\n\nhello\n\n") == compute_facet_hash("hello")


def test_leading_whitespace_on_a_line_is_PRESERVED():
  """Only TRAILING whitespace is stripped. Indentation is content.

  Caught by the measured table: I first asserted
  `"\n\n hello \n\n" == "hello"`, and the plugin's own hash says
  otherwise — that body normalizes to `" hello"`, leading space intact.
  Python facets live or die by indentation, so this is the correct
  behaviour and worth pinning rather than a quirk worth tolerating.
  """
  assert compute_facet_hash("\n\n hello \n\n") != compute_facet_hash("hello")
  assert compute_facet_hash("\n\n hello \n\n") == compute_facet_hash(" hello")
  assert normalize_facet_text("\n\n hello \n\n") == " hello"


def test_internal_blank_lines_are_preserved():
  # Paragraph breaks are meaning — these must NOT collide.
  assert compute_facet_hash("a\n\nb") != compute_facet_hash("a\nb")


def test_nbsp_is_stripped_like_the_plugin():
  # U+00A0 is whitespace to Python's rstrip AND named in the plugin's
  # character class, so both strip it. Pinned because the two reach the
  # same answer by different routes.
  assert compute_facet_hash("hello ") == compute_facet_hash("hello")


def test_bom_is_stripped_like_the_plugin():
  """U+FEFF is the one character where naive reuse of the engine's
  `compute_english_hash` would diverge from the plugin.

  The plugin's `/[\\s﻿\\xA0]+$/` names U+FEFF; Python's
  `str.rstrip()` does not treat it as whitespace and would keep it.
  This is the single case that made a purpose-built module necessary
  instead of importing the engine's hash — see facet_hash.py's
  docstring, and the FEEDBACK note that the shipped `english_hash`
  still has this divergence.
  """
  assert compute_facet_hash("hello﻿") == compute_facet_hash("hello")
  assert compute_facet_hash("hello﻿") == PLUGIN_HASHES["hello"]


def test_hashing_is_deterministic():
  body = "Return Call [[mcq]] with guess=guess."
  assert compute_facet_hash(body) == compute_facet_hash(body)


def test_output_shape_is_lowercase_hex_64():
  h = compute_facet_hash("anything")
  assert len(h) == 64
  assert h == h.lower()
  assert all(c in "0123456789abcdef" for c in h)


def test_non_string_rejected():
  with pytest.raises(TypeError):
    compute_facet_hash(42)  # type: ignore[arg-type]


def test_normalize_is_inspectable():
  # Exposed so a failing parity case can be diagnosed by looking at the
  # normalized text rather than by staring at two hex strings.
  assert normalize_facet_text("\n  a  \n\n  b  \n\n") == "  a\n\n  b"


@pytest.mark.parametrize("body,expected", sorted(PLUGIN_HASHES.items()))
def test_every_measured_case_matches_the_plugin(body, expected):
  """The whole table, not just the cases with prose above.

  Covers non-ASCII (é, em-dash, ö) and a realistic multi-line Python
  facet — the two shapes most likely to expose an encoding or
  line-ending difference between the runtimes.
  """
  assert compute_facet_hash(body) == expected
