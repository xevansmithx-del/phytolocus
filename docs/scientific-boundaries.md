# Scientific boundaries

PhytoLocus supports technical evidence reconciliation. It does not perform biological adjudication.

## What the compiler can establish

Given complete, truthful, and correctly scoped inputs, PhytoLocus can reproducibly establish that:

- a document conforms to a specific closed schema;
- declared identifiers, versions, coordinates, and relationships are internally compatible;
- required content commitments are present and byte-consistent;
- material parent/origin, assembly release, sequence object, locus coordinate, transcript model,
  typed observation, correspondence, and receipt bindings agree under the declared rules;
- a result is deterministic for the same exact inputs;
- one owned capsule byte snapshot is intact relative to its manifest or an externally retained
  digest; validation does not promise that the mutable directory path is current at return;
- unresolved or conflicting technical prerequisites remain on hold.

These are properties of records and computation.

v0.1 has no semantic entities for experiments, sequencing runs, files, variants or structural
variants, tissues, stages, assays, or conditions. An artifact or source response concerning any of
those concepts can be bound only as byte/provenance evidence; PhytoLocus does not parse it into an
entity or reconcile its biological scope.

## What it cannot establish

PhytoLocus cannot establish:

- identity, purity, viability, or chain of custody of a physical stock;
- that a database submitter's sample description is true;
- biological function, causal mechanism, phenotype, effect direction, or generalizability;
- compatibility, identity, or completeness of an experiment, run, file, variant/SV, tissue, stage,
  assay, condition, or expression state, because those are not v0.1 entities;
- absence of an isoform or copy from an incomplete source search;
- safety, efficacy, environmental impact, or regulatory compliance;
- novelty, freedom to operate, authorship, consent, or redistribution rights;
- adequacy of controls, sample size, randomization, blinding, or statistical analysis;
- wet-lab readiness, publication readiness, target suitability, or permission to proceed.

It never selects or ranks a target.

## Interpretation rules

`HOLD` is an informative outcome, not an error to work around. A compiler-defined ready-for-review
state only means that its enumerated technical prerequisites were represented. Independent experts
must review provenance, experimental design, biosafety, ethics, legal constraints, and the physical
materials.

Orthology does not prove conserved function. Expression does not prove causality. A reference locus
does not prove the genotype of an actual stock. An `ALIGNMENT`, `COVERAGE`, `COPY_NUMBER`, or `PHASE`
observation is only a caller-supplied result commitment against existing graph endpoints. A valid
checksum does not prove a trustworthy source or import the semantics of the artifact it covers.

## Responsible use

Use PhytoLocus to expose uncertainty and prepare reviewable evidence. Do not use it to automate
high-consequence biological decisions, suppress adverse evidence, or convert missing information
into affirmative language. Publications should report unresolved findings and the precise technical
scope of validation.
