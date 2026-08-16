"""Byte-identical copies of engine modules forge-mcp needs to CALL.

Drain 2026-08-17-0100 (sync_state Phase 2), engine-access decision
OPTION 1 (forge-core, 2026-08-17).

forge-mcp deliberately carries three dependencies and the engine is not
one of them, so a consumer that must call engine logic vendors it rather
than importing it — the same pattern forge-transpile's `engine_libs/`
uses. The alternative considered and rejected was a hand-written twin
(the `facet_hash.py` pattern): this arc exists precisely because one
question had several implementations that stopped agreeing.

EVERY FILE HERE IS A VERBATIM COPY. Never edit one in place — fix the
engine source and re-vendor:

    bash scripts/sync-vendored-engine.sh

`tests/test_vendored_engine_drift.py` fails the suite on any divergence,
and fails loudly rather than skipping when the engine repo is absent, so
a missing checkout can never be recorded as a clean result.
"""
