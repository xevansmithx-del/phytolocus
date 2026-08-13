# Threat model

PhytoLocus assumes that input is untrusted and that apparently valid evidence can be incomplete,
mis-scoped, stale, or deliberately misleading.

This threat model covers only v0.1 material, assembly, sequence-object, locus, transcript-model,
observation, correspondence, receipt, and capsule records. Experiment, run, file, variant/SV,
tissue, stage, assay, and condition semantics are absent from the schema; related artifacts remain
byte/provenance receipts only.

## Protected properties

- Deterministic output from exact inputs.
- No silent broadening of evidence scope or authority.
- Exact capsule membership and content integrity.
- Preservation of conflicts, missing evidence, and ambiguity.
- No implicit network transmission by the core.
- No direct mutation of an external persistent-memory ledger.
- Public releases free of obvious secrets, local paths, and biological payloads.

## Representative threats and controls

| Threat | Required control |
| --- | --- |
| Assembly, locus-model, and transcript-model version mismatch | Versioned labels and explicit compatibility checks |
| Coordinate-system swap | Declared convention; boundary and hostile fixtures |
| First page treated as a full census | Total/page/cursor receipt; incomplete denominator stays hold |
| Sample or accession substitution | Explicit relationship graph; no lexical or positional joins |
| Assembly and transcript evidence from different material | Biological-scope equality or unresolved state |
| Unreceipted transcript crosswalk or version edge | Endpoint-bound positive observation and receipts; mismatch stays hold |
| Paralog or copy collapse | Explicit copy relationships and ambiguity-preserving output |
| Duplicate sequence aliases evade representation grouping | Canonical content identity within grouping keys |
| Invalid object promoted through `HAPLOTYPE_OF` | Endpoint type, material-origin scope, and relation-bound observation checks |
| Source response changes over time | Version/retrieval receipt and content digest |
| Receipt kind launders a source call or local file | Core-enforced capture-method and artifact-kind scope matrix |
| In-place input rewrite with restored mtime | Descriptor-owned read binds device/inode/size/mtime/ctime/link count |
| NUL or non-Unicode-scalar filesystem argument | Shared pre-I/O path validation across core, CLI, and ToolUniverse |
| Shape-valid but impossible capture timestamp | Calendar-valid UTC RFC 3339 parsing in core and source adapter |
| Manifest-only tampering | Semantic recompilation from study and external digest option |
| Extra capsule payload or symbolic-link escape | Exact member set, regular-file checks, no link following |
| Resource exhaustion | Bounded file sizes, 10,000-item arrays, 50,000 records, 100,000 references, nesting, and diagnostics |
| Secret or local-data publication | Conservative scanner plus mandatory human review |
| Adapter authority laundering | Adapter responses remain receipts, never independent judgment |

## Out of scope

The compiler is not a malware sandbox, laboratory information-management system, identity provider,
electronic lab notebook, access-control system, or cryptographic signing authority. It cannot defend
against a compromised operating system or an authorized caller intentionally supplying false but
internally consistent claims.

## Residual risk

A capsule can be deterministic, untampered, and wrong because its source claims are wrong. Public
databases can change, omit records, or reproduce submitter errors. Privacy patterns can miss novel
credential formats or sensitive contextual combinations. Qualified review and independent source
authentication remain necessary.
