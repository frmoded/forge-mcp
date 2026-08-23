"""The fresh-note Description placeholder (drain 2026-08-23-2100).

The forge-mcp twin of the plugin's `src/description-placeholder-core.ts`.
Both authoring surfaces seed a brand-new note's `# Description` with the
same authoring hint, per the L44 frontmatter-stamp-contracts category:
whatever one writer stamps on a fresh note, the other stamps identically,
or the two surfaces produce notes that behave differently.

WHY A COPY RATHER THAN AN IMPORT
--------------------------------
Separate repos, no shared package. The protocol's mirror-drift rule
allows a copy when a live import is impractical, PROVIDED the suite
fails on divergence — `tests/test_description_placeholder_drift.py`
reads the plugin's TypeScript source and compares line-for-line. Drift
here fails OPEN (an MCP-created note whose hint the plugin's generate
guard no longer recognises would send the hint to the LLM as intent),
which is the direction that needs a mechanical check.

NO `Input` LINE, deliberately. Adjudicated 2026-08-23: a placeholder
declaration lies about the note's interface, and under the
all-or-nothing rule a leftover placeholder suppresses free-variable
promotion of the real variables the author writes next. The template
points at the door; the generators (wizard rule 2b, /generate as of
forge-transpile 0.2.30) walk through it.
"""

#: Seeded into a fresh note's `# Description` when no description is
#: supplied. Three lines, no backticks — the plugin's copy is
#: interpolated into a JS template literal carrying embedded Python,
#: where a backtick terminates the literal mid-Python.
DESCRIPTION_PLACEHOLDER = "\n".join([
  "Describe what this note should do, in plain English.",
  'Name any inputs it takes and what they mean — e.g. "...multiplied by an input scale (a number, default 1)".',
  "Forge turns this into a runnable Recipe with typed inputs.",
])


def is_description_placeholder(text: str) -> bool:
  """True when a Description body is the untouched placeholder.

  Exact match after trimming, matching the plugin's
  `isDescriptionPlaceholder`. One edited word and this is False, which
  is the behaviour we want: the note now carries real intent.
  """
  return (text or "").strip() == DESCRIPTION_PLACEHOLDER.strip()
