# Synthetic example

This directory contains a deliberately invented PhytoLocus study for testing the public command
contract. It describes no real biological system, accession, sequence, physical material, or
laboratory content. Identifiers beginning with `synthetic-` have meaning only inside this fixture.

The fixture exercises only v0.1 material, assembly, sequence-object, locus, transcript-model,
observation, correspondence, receipt, and target-prohibition fields. It contains no experiment,
sequencing-run, file, variant/SV, tissue, stage, assay, or condition entity because those entities
do not exist in the v0.1 schema.

Run:

```bash
phytolocus validate examples/synthetic/study.json
phytolocus compile examples/synthetic/study.json --out .phytolocus/capsule
phytolocus validate-capsule .phytolocus/capsule \
  --study examples/synthetic/study.json
```

The example demonstrates structural compilation and intentional unresolved conditions. Its expected
overall state is `HOLD`, with these exact reasons:

```text
BOUND_MEMORY_HEAD_MISSING
COPY_UNRESOLVED
EXTERNAL_RECEIPT_AUTHORITY_NOT_ESTABLISHED
HAPLOTYPE_UNRESOLVED
ISOFORM_UNRESOLVED
```

That outcome is part of the example. The assembly's phasing state is `UNKNOWN`; copy and isoform
identity are not independently locked; receipt verification is a local technical claim rather than
external authentication; legacy fixture receipts have normalized `provenance: null`; and no
persistent-memory head is bound. PhytoLocus preserves each missing link instead of inventing one.

Do not replace this fixture with real biological data. If a sequence fragment becomes necessary for
a regression test, it must be tiny, invented, and stored only in an explicitly named
`synthetic_sequence` field. The public-release scanner blocks biological file formats and longer
sequence-like payloads.
