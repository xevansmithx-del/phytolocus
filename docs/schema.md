# Study and capsule schema

The schemas implemented by the installed PhytoLocus version are normative. This document explains
their invariants; the bundled [synthetic study](../examples/synthetic/study.json) is the executable
example. Do not infer compatibility from this summary alone—run `phytolocus validate`.

## Study principles

The study is a closed JSON object. Unknown fields and duplicate keys are rejected. Required values
cannot be replaced by empty strings, implicit defaults, or a generic `unknown` unless the schema
explicitly permits an unresolved claim. Every evidence record has a stable local identifier so that
relationships are expressed as references rather than by positional coincidence.

Resource bounds are normative: every parsed array, including nested arrays, is limited to exactly
10,000 items, the aggregate record count across a study is limited to 50,000, and the aggregate
count of nested identifier references is limited to 100,000. The reference budget includes every
`receipt_ids`, `sequence_object_ids`, and `observation_ids` entry, including repeated entries in raw
input. Inputs above any limit are rejected before compilation. These are representation and
denial-of-service boundaries, not scientific sampling targets or evidence-completeness thresholds.

The v0.1 top-level member set is exact:

```text
schema_version
study_id
species
bound_memory_head_sha256
receipts
materials
assemblies
sequence_objects
locus_models
transcript_models
observations
correspondence_claims
target_selection
```

`schema_version` is `phytolocus.study.v0.1`. `target_selection` is always
`{"allowed": false, "status": "PROHIBITED"}`. `bound_memory_head_sha256` is either a lowercase
SHA-256 digest or JSON `null`; a missing binding prevents appendable persistent-memory proposals.

The remaining members separate six kinds of declaration:

1. **Study policy** uses `schema_version`, `study_id`, `species`,
   `bound_memory_head_sha256`, and `target_selection`.
2. **Biological scope** uses `materials` to distinguish `STOCK`, `SAMPLE`, `INDIVIDUAL`, and
   `HAPLOTYPE_REPRESENTATION`, with explicit parent, biological-origin, and independence claims.
3. **Reference scope** uses `assemblies` and `sequence_objects` to record releases, phasing and
   completeness, content hashes, lengths, alphabets, source records, and source versions.
4. **Locus scope** uses `locus_models` and `transcript_models` to bind intervals and isoforms to their
   exact assembly and content-addressed sequence objects.
5. **Evidence** uses `observations` and `correspondence_claims` for explicitly typed results and
   relationships. An unphased observation is not a haplotype.
6. **Receipts** bind every graph object, observation, and correspondence claim to a closed subject
   projection and immutable artifact digest. Receipt syntax and digest equality do not authenticate
   the issuer.

This list is exhaustive. v0.1 has no semantic entities for experiments, sequencing runs, files,
variants or structural variants, tissues, stages, assays, or conditions. An artifact or source
response concerning any of those concepts can be bound only as byte/provenance evidence;
PhytoLocus does not parse it into an entity or reconcile its biological scope. In particular, an
observation kind such as `ALIGNMENT`, `COPY_NUMBER`, or `PHASE` is a typed result commitment between
existing graph objects, not a hidden alignment-file or variant model.

Field names and enumerations can evolve between alpha releases. Pin the package version and retain
the exact normalized `study_lock.json` with reported results.

## v0.1 object members

Every object below is closed. All listed members are required, including members whose value may be
JSON `null`, and no additional member is accepted, except that a raw legacy/manual receipt may omit
`provenance`; normalization inserts `provenance: null`.

### `receipts[]`

| Member | Contract |
| --- | --- |
| `id` | Unique opaque identifier |
| `kind` | `SOURCE`, `MATERIAL`, `ASSEMBLY`, `SEQUENCE`, `ANNOTATION`, `OBSERVATION`, or `CORRESPONDENCE` |
| `subject_sha256` | Digest of the referenced object's canonical subject projection, excluding `receipt_ids` |
| `artifact_sha256` | With provenance, digest of its canonical closed envelope; otherwise a caller-declared artifact digest |
| `issuer` | Nonempty issuer label; a label is not authentication |
| `verification_state` | `RECEIPT_VERIFIED` or `DECLARED_NOT_VERIFIED`; neither establishes external authority |
| `provenance` | Optional in raw input; a closed `phytolocus.receipt-provenance.v0.1` object or `null` after normalization |

`subject_sha256` always binds the actual graph-object subject projection; it is not a digest of
adapter arguments or artifact bytes. When `provenance` is present, `artifact_sha256` must equal the
SHA-256 digest of the canonical provenance object. Its exact fields are:

```text
schema_version             capture_method
content_sha256             arguments_sha256
artifact_kind              byte_count
tool_name                  tool_version
captured_at                source_record
source_release             collection_audit
format_version             coordinate_convention
reference_accession        source_checksum_sha256
```

`capture_method` is `LOCAL_ARTIFACT`, `TOOLUNIVERSE_CALL`, or `DECLARATION`; method-specific fields
must be present or null under the model's closed rules. `DECLARATION` carries only
`content_sha256`; every artifact, source-call, timestamp, collection, format, coordinate,
reference, checksum, and tool field must be `null`, so relabeling cannot bypass the other method
contracts. A collection audit, when present, retains
the exact returned count, reported total, requested limit, cursor-presence flag, completeness flag,
truncation flag, and deterministic coverage state. Local coordinate-bearing evidence retains its
format, artifact kind, byte count, coordinate convention, reference accession, and any source
checksum. A missing/null provenance envelope remains a declaration only, even when the legacy
receipt says `RECEIPT_VERIFIED`.

When present, `captured_at` is not merely shape-matched: it must be a calendar-valid UTC RFC 3339
timestamp ending in `Z`, with at most six fractional-second digits. Impossible months, dates, or
times are rejected by both core and ToolUniverse source-receipt conversion.

The core model, not only an adapter helper, enforces provenance-to-receipt scope. A
`TOOLUNIVERSE_CALL` envelope is valid only on a `SOURCE` receipt. A `LOCAL_ARTIFACT` envelope is
limited by `artifact_kind`: GFF3/GTF may support `ANNOTATION` or `SOURCE`; assembly FASTA and GFA may
support `ASSEMBLY`, `SEQUENCE`, or `SOURCE`; transcript FASTA may support `SEQUENCE` or `SOURCE`; and
BAM, CRAM, PAF, VCF, and BCF may support `OBSERVATION` or `SOURCE`. No direct `Study.from_dict`
caller can bypass that mapping by relabeling the receipt.

Artifact-kind-to-receipt-kind compatibility does not import artifact semantics. The envelope binds
bytes, declared format, coordinate convention, reference, and checksum; it does not add a file,
run, experiment, variant/SV, tissue, stage, assay, or condition object to the study.

Every supplied receipt must be used. Evidence objects may have an empty `receipt_ids` array, which
preserves missing receipt evidence and contributes to the compiler's non-authoritative outcome. A
generic `SOURCE` receipt is accepted structurally as a source binding but cannot silently substitute
for kind-specific evidence during reconciliation.

`reconciliation.json.receipt_bindings[]` carries the normalized envelope and an explicit
`provenance_envelope_bound` flag so downstream review cannot lose the source scope during
projection.

### `materials[]`

| Member | Contract |
| --- | --- |
| `id` | Globally unique graph-object identifier |
| `kind` | `STOCK`, `SAMPLE`, `INDIVIDUAL`, or `HAPLOTYPE_REPRESENTATION` |
| `biological_origin_id` | Opaque declared origin or `null` |
| `parent_material_id` | Another material identifier or `null`; cycles are prohibited |
| `independence_state` | `SAME_ORIGIN`, `DISTINCT_DECLARED`, or `UNRESOLVED` |
| `receipt_ids` | Unique receipt references; may be empty when evidence is missing |

`DISTINCT_DECLARED` is a claim, not independently verified replication.

### `assemblies[]`

| Member | Contract |
| --- | --- |
| `id` | Globally unique graph-object identifier |
| `material_id` | Material represented by the assembly |
| `release` | Exact caller-declared release/version label |
| `phasing_state` | `PHASED`, `UNPHASED`, `MIXED`, or `UNKNOWN` |
| `completeness_state` | `DECLARED_COMPLETE`, `PARTIAL`, or `UNKNOWN` |
| `receipt_ids` | Unique receipt references; may be empty when evidence is missing |

`DECLARED_COMPLETE` does not become an independently adjudicated completeness result.

### `sequence_objects[]`

| Member | Contract |
| --- | --- |
| `id` | Globally unique graph-object identifier |
| `kind` | `GENOMIC`, `TRANSCRIPT`, `CDS`, or `PROTEIN` |
| `sequence_sha256` | Lowercase digest of sequence content; sequence bytes are not stored in the study |
| `length` | Positive sequence length |
| `alphabet` | `DNA`, `RNA`, or `PROTEIN`, compatible with `kind` |
| `assembly_id` | Assembly scope or `null` |
| `material_id` | Material scope or `null` |
| `source_record` | Exact source record label |
| `source_version` | Exact source record version |
| `receipt_ids` | Unique receipt references; may be empty when evidence is missing |

At least one of `assembly_id` and `material_id` must be present. When both are present, their material
identity must agree.

### `locus_models[]`

| Member | Contract |
| --- | --- |
| `id` | Globally unique graph-object identifier |
| `assembly_id` | Assembly on which the model is defined |
| `contig` | Assembly-scoped contig/sequence-record label |
| `start`, `end` | One-based, closed interval with `start >= 1` and `end >= start` |
| `strand` | `+`, `-`, or `UNKNOWN` |
| `model_version` | Exact model or annotation-release label |
| `sequence_object_ids` | Nonempty genomic sequence-object references on the same assembly |
| `receipt_ids` | Unique receipt references; may be empty when evidence is missing |

Identical intervals do not automatically establish separate copies. Conflicting strand or copy
representations remain diagnostic evidence. Within cross-assembly grouping, duplicate sequence
object identifiers with the same sequence digest, length, and alphabet canonicalize to one sequence
identity; repeating equivalent identifiers cannot make an otherwise matching locus look distinct.
Within one locus, every genomic sequence object whose length exactly equals the modeled locus span
must have the same digest/length/alphabet identity; incompatible full-span objects are rejected as
`SEQUENCE_HASH_CONFLICT`. Non-full-span genomic objects remain distinct partial or broader-scope
evidence and do not establish exact locus content.

The reconciliation exports ambiguous locus representation components as
`ambiguous_representation_groups` to both PhytoParalog and PhytoPerturb. A component can arise from
an exact interval on one assembly or from the same declared material-origin component plus an exact
full-span genomic-sequence identity across assemblies. Cross-assembly coordinates may differ and
remain explicit rather than being treated as equal. Each full reconciliation component retains
`assembly_ids`, `contig_records`, `start_values`, `end_values`, `strand_set`, and the explicit
`component_basis`. `sequence_content_conflict` is true when exact same-assembly intervals bind
incompatible full-span genomic sequence identities and adds `SEQUENCE_HASH_CONFLICT`; string
equality for contig records across assemblies is never treated as an
independent crosswalk. Downstream consumers must not count component members as independent copies
or rank them independently. An empty list means only that these specific ambiguity rules did not
fire; it does not establish copy completeness.

### `transcript_models[]`

| Member | Contract |
| --- | --- |
| `id` | Globally unique graph-object identifier |
| `locus_model_id` | Parent locus model |
| `transcript_sequence_id` | A `TRANSCRIPT` sequence object |
| `cds_sequence_id` | A `CDS` sequence object or `null` |
| `protein_sequence_id` | A `PROTEIN` sequence object or `null` |
| `isoform_label` | Caller-declared isoform label |
| `model_version` | Exact model or annotation-release label |
| `receipt_ids` | Unique receipt references; may be empty when evidence is missing |

Transcript, CDS, and protein sequences must remain in the parent locus biological-origin component.
A sequence that declares a different assembly requires a receipt-bound `TRANSCRIPT_OF` claim with a
positive, endpoint-matched `MODEL_CROSSWALK` observation that itself has a receipt. A transcript
model version outside the locus annotation/model version remains an explicit external-crosswalk
hold; an unobserved or unreceipted `SUPERSEDES` declaration does not remove it.

### `observations[]`

| Member | Contract |
| --- | --- |
| `id` | Identifier unique from graph objects and other observations |
| `kind` | `ALIGNMENT`, `COVERAGE`, `COPY_NUMBER`, `PHASE`, `TRANSCRIPT_SUPPORT`, or `MODEL_CROSSWALK` |
| `subject_id` | Existing graph-object identifier |
| `object_id` | Existing graph-object identifier or `null` |
| `result_state` | `POSITIVE`, `NEGATIVE`, `AMBIGUOUS`, `NO_RESULT`, `NOT_ASSESSED`, or `FAILED` |
| `evidence_sha256` | Digest of exact observation evidence |
| `receipt_ids` | Unique receipt references; may be empty when evidence is missing |

`NEGATIVE` is an observed result state, not proof of biological absence. `NO_RESULT`, `FAILED`, and
`AMBIGUOUS` remain explicit blockers where the corresponding evidence is required.

### `correspondence_claims[]`

| Member | Contract |
| --- | --- |
| `id` | Identifier unique from graph objects and other claims |
| `left_id`, `right_id` | Distinct, type-compatible graph-object identifiers |
| `relation` | `SAME_SEQUENCE`, `SAME_LOCUS`, `ALLELE_OF`, `HAPLOTYPE_OF`, `TRANSCRIPT_OF`, `PROTEIN_OF`, `SUPERSEDES`, or `REPRESENTS` |
| `declared_state` | `EXACT`, `SUPPORTED`, `AMBIGUOUS`, `CONFLICT`, or `UNRESOLVED` |
| `observation_ids` | Unique supporting observation references; may be empty |
| `receipt_ids` | Unique receipt references; may be empty when evidence is missing |

`REPRESENTS` requires same-kind endpoints; it cannot bridge arbitrary graph-object types. Declared
correspondence never acquires external authority merely because its endpoint types and receipt
digests validate. Transcript-model sequence/CDS/protein references must also agree with the parent
locus assembly and biological-material scope. `HAPLOTYPE_OF` requires a genomic locus, genomic
sequence, or explicit `HAPLOTYPE_REPRESENTATION` on the left; a stock, sample, or individual in the
same declared material-origin component on the right; and any supporting observation must match
both endpoints and an allowed relation-specific evidence kind. Transcript sequences cannot be
recast as haplotypes.

## Coordinate invariants

In the v0.1 core, `locus_models.start` and `locus_models.end` are **one-based, closed** coordinates.
The core requires `start >= 1` and `end >= start`. Contig names are scoped to `assembly_id`; lexical
equality across assemblies does not establish identity.

The artifact integration separately requires incoming coordinate-bearing artifacts to declare
`ONE_BASED_CLOSED` or `ZERO_BASED_HALF_OPEN` and a reference accession. Conversion into the core
representation must be explicit and receipt-bound. The compiler never silently reinterprets,
lift-overs, or overwrites source coordinates.

Material-origin analysis distinguishes a declared technical partition from admissible independent
biological replication. Each component reports `declared_distinct_material_ids`,
`origin_resolution_state`, and a technical count; unresolved components contribute zero. The
top-level `unresolved_origin_component_count` remains visible, while
`admissible_independent_biological_unit_count` remains zero because v0.1 has no external material
authority adapter. A shared origin label alone therefore cannot manufacture an independent unit.

## Completeness invariants

A response that reports more records than it returns is incomplete unless every page is represented
and bound. The compiler does not treat the first page, a search snippet, a top hit, or an arbitrary
source result as a complete denominator. When local artifact provenance is supplied, its byte count,
content digest, and optional verified source checksum must satisfy the closed envelope contract.
That receipt still does not create a semantic file entity.

## Capsule members

The capsule has an exact member set:

| File | Purpose | Authority |
| --- | --- | --- |
| `study_lock.json` | Canonical input and evidence commitments | Caller claims, normalized |
| `reconciliation.json` | Technical joins, conflicts, diagnostics, and state | Compiler-derived |
| `pec_candidate_evidence.json` | Candidate export to an independent judge | Proposal only |
| `phytoresolve_proposed_events.json` | Candidate persistent-memory events | Proposal only |
| `manifest.json` | Sizes, hashes, schema/version, and capsule commitment | Internal integrity |

Extra files, missing files, non-canonical JSON, symbolic links, and digest or size mismatches are
invalid.

## Digest semantics

Per-file hashes bind exact canonical bytes. The capsule digest binds the complete, ordered manifest
commitment according to the versioned implementation. Recomputing the manifest after tampering does
not make a capsule semantically valid when validation includes the original study. An externally
retained digest detects replacement of the entire self-consistent capsule.

Digests establish identity of bytes, not truth, authorship, collection provenance, or physical-stock
identity.

Capsule validation reports describe an `OWNED_BYTE_SNAPSHOT`. `snapshot_valid: true` and the legacy
compatibility field `capsule_valid: true` mean that snapshot passed; they never assert that a mutable
directory path is still current at return. The report always carries
`mutable_path_current_at_return_guaranteed: false`. Compilation similarly returns the digest of its
intended and reread verified snapshot, not a promise about later path contents.
