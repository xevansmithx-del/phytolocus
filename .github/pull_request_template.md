## Summary

Describe the failure mode, the conservative behavior after this change, and its scope.

## Scientific boundary

- [ ] Physical-stock identity is not inferred from metadata alone.
- [ ] Ambiguous, incomplete, stale, or conflicting evidence remains `HOLD` or is rejected.
- [ ] No function, causality, phenotype, safety, readiness, ranking, or target selection is inferred.
- [ ] Persistent-memory output remains a proposal bound to the exact declared head.

## Verification

- [ ] Source-contract, reconciliation, or capsule tests were added at the applicable layer.
- [ ] A hostile negative fixture covers any new positive state.
- [ ] `ruff check .`, `ruff format --check .`, and offline `pytest` pass.
- [ ] ToolUniverse 1.4.1 tests pass if the integration changed.
- [ ] `python scripts/privacy_scan.py .` passes.
- [ ] Public fixtures are neutral, synthetic, and redistribution-safe.
