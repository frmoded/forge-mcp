#!/usr/bin/env bash
# check-vault-imports-drift.sh — drain 2026-08-09-2100.
#
# forge-mcp vendors vault_imports.py from its canonical home in the
# forge engine (forge/forge/core/vault_imports.py). forge-mcp does not
# depend on the forge package, so the sharing model is a byte-identical
# vendored copy — same posture as forge-transpile's engine_libs and the
# plugin's engine bundle. This check diffs the two files and fails on
# any drift. Run it before shipping a change to either copy.
set -euo pipefail

CANONICAL="${FORGE_REPO:-$HOME/projects/forge}/forge/core/vault_imports.py"
VENDORED="$(cd "$(dirname "$0")/.." && pwd)/src/forge_mcp/vault_imports.py"

if [ ! -f "$CANONICAL" ]; then
  echo "SKIP: canonical copy not found at $CANONICAL (forge repo not checked out?)"
  exit 0
fi

if diff -q "$CANONICAL" "$VENDORED" >/dev/null; then
  echo "clean: vault_imports.py vendored copy matches canonical ($CANONICAL)"
else
  echo "DRIFT DETECTED between:"
  echo "  canonical: $CANONICAL"
  echo "  vendored:  $VENDORED"
  diff "$CANONICAL" "$VENDORED" || true
  exit 1
fi
