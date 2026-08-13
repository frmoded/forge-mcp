r"""Cheap, NON-AUTHORITATIVE scan for free identifiers in a Recipe body.

Drain 2026-08-13-0230 — Option C from drain 2026-08-12-2135's investigation.

This is deliberately NOT input derivation. `forge_read_note`'s `inputs`
field reads declared metadata only and keeps doing so; drain 2135
established that contract is working as designed. The defect was that
`inputs: []` reads identically for "this note takes no parameters" and
"nobody declared any, but the body clearly wants some" — wizard trusted
the former reading, omitted `inputs=["bars"]` on commit, and got a
runtime TypeError from a zero-arg function.

So this answers one question only: does the Recipe body reference names
that nothing in the body binds? A `true` means "proceed with caution",
never "here is the input list". Options A and B from 2135 (reimplement
the engine's analyzer here, or vendor it) were both rejected precisely
to avoid a third divergent implementation of a semantic the engine owns
— the drift pattern behind drain 2130's closure-walker split and the
llm_prompts_v2 vendoring gap.

Regex-based on purpose. Section 3 of the drain sanctioned that once no
reusable tokenizer was found in forge-mcp (only `extract_wikilinks`,
reused below for Call targets). A false positive costs a caller one
moment of caution; a false negative costs nothing that exists today.
"""
from __future__ import annotations

import re

# `Let NAME = ...` / `Input NAME: TYPE ...` bind NAME.
_LET_RE = re.compile(r"\bLet\s+([A-Za-z_][A-Za-z0-9_]*)\s*=", re.M)
_INPUT_RE = re.compile(r"\bInput\s+([A-Za-z_][A-Za-z0-9_]*)\s*:", re.M)
# `For each VAR in ...` binds VAR.
_FOREACH_RE = re.compile(r"\bFor\s+each\s+([A-Za-z_][A-Za-z0-9_]*)\s+in\b", re.M)
# Any bare identifier occurrence.
_IDENT_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\b")
# `[[wikilink]]` targets name notes, not parameters.
_WIKILINK_RE = re.compile(r"\[\[[^\]]*\]\]")
_STRING_RE = re.compile(r"(\"[^\"]*\"|'[^']*')")

# Recipe grammar keywords and literals — never parameters.
_KEYWORDS = {
  "Let", "Input", "Return", "Call", "with", "Repeat", "times", "For",
  "each", "in", "If", "Otherwise", "True", "False", "None", "and", "or", "not",
}


def scan_undeclared_inputs(recipe: str | None) -> list[str]:
  """Free identifiers in `recipe`, in first-appearance order.

  Subtracts: Recipe keywords, `Let`/`Input` targets, `For each` loop
  variables, wikilink contents, string literals, and kwarg NAMES on the
  left of `=` (those are the callee's parameter names, not this note's).
  """
  if not recipe:
    return []

  bound: set[str] = set()
  bound |= set(_LET_RE.findall(recipe))
  bound |= set(_INPUT_RE.findall(recipe))
  bound |= set(_FOREACH_RE.findall(recipe))

  # Blank out spans that can never contain a parameter reference.
  scrubbed = _WIKILINK_RE.sub(" ", recipe)
  scrubbed = _STRING_RE.sub(" ", scrubbed)
  # `k=v` — drop the k, keep the v. The kwarg name belongs to the callee.
  scrubbed = re.sub(r"\b[A-Za-z_][A-Za-z0-9_]*\s*=", " ", scrubbed)

  free: list[str] = []
  for name in _IDENT_RE.findall(scrubbed):
    if name in _KEYWORDS or name in bound or name in free:
      continue
    free.append(name)
  return free
