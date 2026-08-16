#!/usr/bin/env bash
# Re-vendor the engine modules forge-mcp calls. Idempotent.
#
# Drain 2026-08-17-0100. See src/forge_mcp/_vendor/__init__.py for why
# these are copies rather than imports.
#
# Usage:  bash scripts/sync-vendored-engine.sh
# Env:    ENGINE_REPO=/path/to/forge   (default: ../forge)
set -euo pipefail

ENGINE_REPO="${ENGINE_REPO:-../forge}"
VENDOR_DIR="src/forge_mcp/_vendor"

# vendored-name : engine-relative-source
FILES="sync_state.py:forge/core/sync_state.py"

if [[ ! -d "$ENGINE_REPO" ]]; then
  echo "ERROR: engine repo not found at $ENGINE_REPO" >&2
  echo "Run from the forge-mcp repo root, or set ENGINE_REPO." >&2
  echo "NOTE: this is a setup error — the sync did NOT run. Do not" >&2
  echo "record it as a clean result." >&2
  exit 2
fi
[[ -d "$VENDOR_DIR" ]] || { echo "ERROR: $VENDOR_DIR missing" >&2; exit 2; }

changed=0
for entry in $FILES; do
  name="${entry%%:*}"; src="$ENGINE_REPO/${entry#*:}"
  if [[ ! -f "$src" ]]; then
    echo "ERROR: no engine source at $src" >&2; exit 2
  fi
  if cmp -s "$src" "$VENDOR_DIR/$name"; then
    echo "  unchanged: $name"
  else
    cp "$src" "$VENDOR_DIR/$name"; echo "  updated:   $name  <- $src"; changed=1
  fi
done
echo ""
[[ $changed -eq 0 ]] && echo "Vendored engine files already current." \
  || echo "Re-vendored. Commit $VENDOR_DIR/ with the engine change."
