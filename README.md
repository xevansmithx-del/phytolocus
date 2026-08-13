# PhytoLocus

[![CI](https://github.com/xevansmithx-del/phytolocus/actions/workflows/ci.yml/badge.svg)](https://github.com/xevansmithx-del/phytolocus/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)

PhytoLocus is a provenance-first, fail-closed compiler for one exact plant-locus schema. It
reconciles caller-declared material and parent/origin relationships, assembly releases,
content-addressed sequence objects, one-based closed locus coordinates, transcript models, typed
observations and correspondences, and closed provenance envelopes. It produces deterministic
technical evidence for independent review and preserves missing, conflicting, or unauthenticated
links as `HOLD`.

> **Alpha software.** PhytoLocus cannot authenticate a physical seed, plant, tissue, tube, or
> sequencing library from metadata alone. It does not infer biological function, causality,
> phenotype, safety, wet-lab readiness, publication readiness, or target suitability. It never
> chooses a target. A structurally successful compilation is not a scientific PASS.

### Exact v0.1 scope

v0.1 has no semantic entities for experiments, sequencing runs, files, variants or structural
variants, tissues, stages, assays, or conditions. An artifact or source response concerning any of
those concepts can be bound only as byte/provenance evidence; PhytoLocus does not parse it into an
entity or reconcile its biological scope. Artifact-kind labels such as BAM, CRAM, VCF, or BCF
constrain receipt projection only.

## Why it exists

A gene symbol is not a stable locus object. Assembly releases, sequence records, locus coordinates,
and transcript models can disagree, while public records may describe a different declared
material origin. PhytoLocus makes only the joins represented by its closed v0.1 graph explicit and
reviewable before downstream interpretation.

The compiler is useful when a project needs to answer technical questions such as:

- Which declared assembly release and sequence record define this locus model?
- Are one-based closed coordinates, sequence commitments, and record versions internally
  consistent?
- Do transcript, CDS, and protein sequence objects remain within the locus material-origin scope?
- Are typed observations and correspondence claims endpoint-compatible and receipt-bound?
- Which claims remain unresolved and therefore keep the result on hold?

PhytoLocus answers none of these by guessing. The caller supplies or separately retrieves the
evidence. The compiler validates its declared scope, reconciles compatible records, and exposes
conflicts and missing links.

## Install

PhytoLocus requires Python 3.10 or newer. During alpha development, install from a reviewed source
checkout:

```bash
git clone https://github.com/xevansmithx-del/phytolocus.git
cd phytolocus
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

The initial project does not claim PyPI availability. Install a signed or checksummed release
artifact when one is published.

## Five-minute synthetic run

The command-line contract is:

```bash
phytolocus validate examples/synthetic/study.json
phytolocus compile examples/synthetic/study.json --out .phytolocus/capsule
phytolocus validate-capsule .phytolocus/capsule \
  --study examples/synthetic/study.json
```

To bind validation to a digest stored outside the capsule:

```bash
phytolocus validate-capsule .phytolocus/capsule \
  --expected-digest SHA256
```

Replace `SHA256` with the expected 64-character lowercase hexadecimal capsule digest. Exit status
zero means that the requested structural operation completed; it does not close a biological
claim. The bundled fixture is deliberately synthetic and includes no real organism, accession,
sequence, sample, study, or local path. Its expected state is `HOLD` because its memory head is not
bound, receipt authority is not externally established, and copy, haplotype, and isoform identity
remain unresolved.

The CLI writes one JSON object to standard output on success and exits `0`. Every command-line,
study, capsule-read, or capsule-write failure writes one redacted JSON object to standard error and
exits `2`; the object always contains `status`, `error`, `error_code`, `remediation`,
`target_selected`, and `target_selection`. Argparse failures use the same envelope and never echo a
caller-supplied path or value. `validate` reads a study and returns structural counts only.
`compile` exclusively creates a new five-file capsule directory and refuses an existing
destination. `validate-capsule` performs internal-integrity validation with no option,
external-digest anchoring with `--expected-digest`, semantic recompilation with `--study`, or both
checks when both options are supplied. Semantic `hold_reasons`, `producer_status`, and `study_id`
appear in validation output only when `--study` is supplied.

Capsule success is explicitly snapshot-scoped. `validation_subject` is
`OWNED_BYTE_SNAPSHOT`, `snapshot_valid` is true, and
`mutable_path_current_at_return_guaranteed` is false. The compatibility field
`capsule_valid: true` means only that the descriptor-owned bytes examined by that invocation passed;
it is not an atomic or path-current-at-return promise. A concurrent same-user writer can mutate the
directory after its last observation. Revalidate before later use and compare an independently
retained digest.

## Capsule contract

A v0.1 capsule contains exactly five regular files and no symbolic links:

```text
study_lock.json
reconciliation.json
pec_candidate_evidence.json
phytoresolve_proposed_events.json
manifest.json
```

`manifest.json` binds canonical bytes, file sizes, and SHA-256 digests for the other documents.
`validate-capsule` recomputes those commitments for one owned byte snapshot. Validation with
`--study` also recomputes semantic
outputs from the original input. Validation with `--expected-digest` compares the exact capsule
commitment with an externally retained value. A self-authored manifest proves internal consistency,
not stock identity or scientific truth.

## Fail-closed guarantees

- Unknown and missing fields are rejected by closed schemas.
- Every parsed array is capped at 10,000 items, each study at 50,000 aggregate records, and nested
  identifier references at 100,000 total; these software resource limits do not imply biological
  completeness.
- Caller-declared species, material origin, assembly release, sequence record/version, locus
  coordinates, transcript model version, and provenance claims remain distinct.
- Metadata identifiers are claims until their authority and content bindings are independently
  established.
- Material parent/origin links do not authenticate the physical stock handled in a laboratory.
- Records from different material-origin components, assembly releases, sequence identities, locus
  coordinates, or transcript model versions are not silently merged.
- Assembly phasing labels and `PHASE` observations remain scoped graph claims; v0.1 has no variant
  or structural-variant entity.
- Missing pagination, checksums, release versions, or provenance remains visible.
- Inline receipt provenance preserves capture/tool/release/coverage and artifact
  format/coordinate/reference/checksum semantics; `subject_sha256` still binds the graph object.
- Core validation restricts `TOOLUNIVERSE_CALL` provenance to `SOURCE` receipts and enforces the
  local artifact-kind-to-receipt-kind matrix even for direct Python callers.
- Legacy/manual receipts without provenance normalize to `null` and remain declarations only.
- Ambiguous locus representations, copy state, and transcript models remain unresolved; v0.1 has
  no paralog entity.
- Coordinate-identical model representations are exported as
  `ambiguous_representation_groups` to both PhytoParalog and PhytoPerturb; downstream consumers
  must not count or rank those representations as independent loci.
- Unresolved prerequisites produce `HOLD`; no output represents scientific approval.
- Persistent-memory exports are proposals only and cannot write another tool's ledger directly.

## ToolUniverse

An optional integration targets ToolUniverse 1.4.1 and exposes three local tools:

- `PhytoLocus_preflight`
- `PhytoLocus_compile`
- `PhytoLocus_validate_capsule`

The integration is an invocation surface, not a new scientific authority. It must preserve the
same schemas, deterministic outputs, refusal states, and privacy boundaries as the Python and CLI
surfaces. `PhytoLocus_compile` is in-memory only: it returns SHA-256 commitments for four compiled
documents and writes no files. Use the CLI `compile` command when a five-file capsule is required.
See [ToolUniverse integration](docs/tooluniverse.md).

## Verification model

The test program has three layers:

1. **Source contracts** verify receipt construction, pagination, source identifiers and versions,
   coordinate declarations, byte sizes, and checksums using neutral public controls or synthetic
   responses. They do not create unsupported semantic entities.
2. **Locus reconciliation** verifies the exact v0.1 material, assembly, sequence, locus, transcript,
   observation, correspondence, and receipt graph, including fail-closed behavior for mismatched
   releases, coordinates, content identities, material origins, and transcript models.
3. **End-to-end capsules** verify deterministic reruns, exact member sets, semantic recomputation,
   external-digest validation, tamper detection, and negative fixtures that must remain on hold.

Passing these tests demonstrates software behavior against their fixtures. It does not validate an
unseen biological specimen or establish that any wet-lab action should proceed.

## Documentation

- [Architecture](docs/architecture.md)
- [Study and capsule schema](docs/schema.md)
- [Provenance model](docs/provenance.md)
- [Scientific boundaries](docs/scientific-boundaries.md)
- [Threat model](docs/threat-model.md)
- [Testing strategy](docs/testing.md)
- [ToolUniverse integration](docs/tooluniverse.md)
- [Contributing](CONTRIBUTING.md)
- [Security and public-data handling](SECURITY.md)

## Development

```bash
uv sync --frozen --extra dev --extra tooluniverse
uv run --frozen --no-sync ruff check .
uv run --frozen --no-sync ruff format --check .
uv run --frozen --no-sync pytest -m 'not live'
uv run --frozen --no-sync python scripts/privacy_scan.py .
```

CI and release verification install the pinned uv runtime and execute the checked-in `uv.lock`.
Dependency changes must update and review that lockfile; verification must not resolve a fresh graph.

Only neutral synthetic fixtures belong in the public repository. Review
[CONTRIBUTING.md](CONTRIBUTING.md) before proposing a change.

## License and citation

PhytoLocus is licensed under the [Apache License 2.0](LICENSE). Cite the software using
[CITATION.cff](CITATION.cff), including the exact version and externally retained capsule digest
when reporting a compilation.
