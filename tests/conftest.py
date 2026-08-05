"""Shared fixtures. Drain 2026-08-05-1900.

Re-exports the fixture-vault harness's pytest fixtures so test modules
can take them as parameters without importing them (importing a fixture
into a module that also names it as a test parameter trips ruff F811).
The harness itself lives in tests/vault_fixtures.py — import
`make_vault` / `ACTION_NOTE` from there for bespoke topologies.
"""
from tests.vault_fixtures import fixture_vault_pair  # noqa: F401
