# Testing strategy

PhytoLocus uses three integration layers plus ordinary unit and property tests. Each layer answers a
different question; none alone proves biological validity.

## Layer 1: source contracts

Source-contract tests verify adapters and receipt construction against neutral public controls or
recorded synthetic responses. They must check:

- request and response schema;
- stable accession and version parsing;
- explicit source record/release and local-artifact coordinate/reference provenance;
- pagination until the reported denominator is complete;
- local-artifact byte size and checksum extraction;
- rate-limit, timeout, empty, malformed, and partial-response behavior;
- endpoint and service-version provenance when exposed.

A source that reports more hits than the adapter can enumerate must not emit a completeness claim.
Live tests are informative monitoring, isolated from deterministic release tests.

These tests validate provenance transport, not unsupported entity models. v0.1 has no semantic
entities for experiments, sequencing runs, files, variants or structural variants, tissues, stages,
assays, or conditions. An artifact or response about one of those concepts can only produce a
byte/provenance receipt.

## Layer 2: locus reconciliation

Reconciliation tests use a neutral public-locus control represented by redistributable metadata,
plus fully synthetic hostile cases. They verify the exact v0.1 graph: material parent/origin links,
assembly releases, content-addressed sequence objects, locus coordinates, transcript models, typed
observations, endpoint-constrained correspondences, and receipt bindings.

Required negative cases include:

- stale or swapped assembly, sequence-record, locus-model, and transcript-model versions;
- zero-based/one-based and inclusive/half-open coordinate swaps;
- missing pages or checksums;
- assembly and transcript sequence objects from different material-origin components;
- an invalid or unscoped `HAPLOTYPE_OF` correspondence;
- duplicate locus representations that could inflate copy counts;
- conflicting transcript identifiers or incomplete isoform search;
- nonpositive or endpoint-mismatched observations promoted to supported correspondence;
- a provenance receipt promoted into scientific or external authority.

Every unresolved case must remain `HOLD` or be rejected. No test may update expected output simply to
accept a broadened positive state without independent review.

## Layer 3: end-to-end capsules

End-to-end tests compile the bundled synthetic study through the CLI, Python API, and optional
ToolUniverse surface. They require:

- deterministic canonical bytes across repeated clean runs;
- an exact five-file member set with no symbolic or hard links;
- manifest size and digest verification;
- descriptor-bound reread, rehash, metadata, and final-stat checks across the full validation
  window;
- explicit snapshot-only reporting when a mutation can occur after the earliest member's last stat,
  plus rejection by a fresh subsequent validation;
- semantic recompilation with `--study`;
- exact external commitment with `--expected-digest`;
- rejection after byte tampering, member insertion/removal, manifest refresh, or study substitution;
- equivalent structured refusal envelopes across invocation surfaces;
- negative capsules that remain on hold.

## Unit and property tests

Unit tests cover closed models, duplicate JSON keys, the 10,000-item per-array and 50,000-record
aggregate study bounds, the 100,000-reference aggregate bound, Unicode, numeric edge cases, path
safety (including status-change-time mutation), canonical serialization, state propagation, and
diagnostics. Thousand-record stress regressions exercise coordinate-representation grouping and a
long supersession chain without quadratic all-pairs or repeated graph traversal. Duplicate-equivalent
sequence identifiers are also tested to ensure that they cannot evade representation grouping.
Property tests should demonstrate
idempotent normalization and stable output under irrelevant input object-key ordering.

## Release verification

CI tests Python 3.10–3.13 on Ubuntu and runs the core/compiler/CLI contract on Ubuntu, macOS, and
Windows. Linux and macOS execute the full capsule suite; Windows verifies the explicit fail-closed
capsule refusal because v0.1 requires POSIX descriptor semantics. Heavier packaging, ToolUniverse,
and privacy jobs remain on Ubuntu. CI also builds source and wheel distributions, scans extracted
artifacts, installs each distribution in a separate clean environment, and runs the complete
non-live suite plus explicit loaded-tool malformed-argument probes against each installed artifact.
Tests run outside the checkout and assert that `phytolocus` imports from the clean environment, so
checkout files cannot mask omitted or stale package bytes. A pinned uv runtime synchronizes the
checked-in `uv.lock` with `--frozen`; test and build commands then use
`uv run --frozen --no-sync`. The source distribution must contain the same lockfile byte-for-byte.
The optional plugin is tested against ToolUniverse 1.4.1 exactly.

Release evidence demonstrates the behavior of the released bytes. It does not authenticate an
external dataset or actual stock.
