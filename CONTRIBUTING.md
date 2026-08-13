# Contributing to PhytoLocus

PhytoLocus welcomes reproducible bug fixes, conservative schema improvements, documentation, and
neutral synthetic tests. Contributions must preserve the compiler's fail-closed scientific and
privacy boundaries.

## Before opening an issue

- Search existing issues and documentation.
- Reduce the problem to a neutral synthetic fixture.
- Remove credentials, personal paths, unpublished labels, real biological sequences, and
  controlled or identifying metadata.
- Use the private reporting process in [SECURITY.md](SECURITY.md) for vulnerabilities or accidental
  disclosure. Never post sensitive evidence in a public issue.

## Development setup

```bash
git clone https://github.com/xevansmithx-del/phytolocus.git
cd phytolocus
uv sync --frozen --extra dev --extra tooluniverse
uv run --frozen --no-sync pre-commit install
```

Run the complete local verification suite before submitting a pull request:

```bash
uv run --frozen --no-sync ruff check .
uv run --frozen --no-sync ruff format --check .
uv run --frozen --no-sync pytest -m 'not live'
uv run --frozen --no-sync python scripts/privacy_scan.py .
```

If the change touches the optional integration, also test with ToolUniverse 1.4.1 exactly. Include
the commands and results in the pull request.

The repository lockfile is part of the reviewed source and source distribution. Use the pinned uv
version documented in CI when changing dependencies, update `uv.lock` deliberately, and include the
lock diff in review. Do not replace frozen verification with an unreviewed dependency resolution.

## Scientific contract

A contribution must not:

- authenticate a physical stock solely from a metadata link;
- infer function, causality, phenotype, safety, efficacy, or wet-lab readiness;
- rank, select, or recommend a target;
- convert missing evidence, ambiguous copies, unphased variants, or incompatible releases into a
  positive claim;
- silently merge evidence across accessions, biological samples, assemblies, annotations, tissues,
  or stages;
- treat a self-authored checksum as independent authentication;
- write directly to a persistent-memory ledger owned by another system.

New positive states require a test showing every necessary evidence binding and at least one hostile
negative fixture that remains `HOLD` or is rejected. Prefer explicit unknown states over defaults.

## Test expectations

Place tests at the lowest applicable layer:

1. Source-contract tests cover schemas, pagination, versioning, checksums, and coordinates.
2. Reconciliation tests cover cross-record joins, ambiguity, conflicts, and refusal behavior.
3. Capsule tests cover deterministic output, semantic recomputation, exact membership, tampering,
   and external digest commitments.

Tests using live services must be isolated, bounded, clearly marked, and never required to prove a
scientific claim. Stable synthetic fixtures remain the primary regression evidence.

## Public fixture rules

Use invented identifiers and neutral labels such as `synthetic-locus-1`. Tiny sequence fragments
are permitted only when explicitly stored in a `synthetic_sequence` field and necessary for a
software test. Do not add biological file formats, archive accessions, raw reads, real sample names,
signed download URLs, or machine-specific paths.

Versioned public accessions may appear only in isolated, opt-in source-contract tests or audit
documentation when they are necessary neutral controls and contain no project-specific association.
They must not appear in the packaged synthetic fixture.

Run the privacy scanner on the repository and on built distributions. The scanner is release
hygiene, not proof that a contribution is safe or legally redistributable; human review remains
required.

## Pull requests

Keep changes focused. Describe the failure mode, the conservative behavior after the change, and
the tests that demonstrate it. Update schema and boundary documentation when an observable contract
changes. By contributing, you agree that your contribution is licensed under Apache-2.0.
