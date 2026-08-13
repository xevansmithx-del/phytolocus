# ToolUniverse integration

PhytoLocus provides an optional local plugin for ToolUniverse 1.4.1. The plugin exposes compiler
operations without changing their scientific authority or network behavior.

## Tools

- `PhytoLocus_preflight` validates input and returns structural counts only without writing a
  capsule.
- `PhytoLocus_compile` validates input and returns deterministic document digests, hold reasons,
  and `ambiguous_representation_groups` without returning document bodies or writing any files.
  Its `document_sha256` map commits the four in-memory compiled documents. The full reconciliation
  forwards ambiguous groups to both PhytoParalog and PhytoPerturb. Use the CLI `compile` command to
  create a five-file capsule.
- `PhytoLocus_validate_capsule` verifies exact membership, canonical bytes, hashes, and optionally
  semantic recomputation or an external digest for one descriptor-owned snapshot. Its success data
  propagates `validation_subject: OWNED_BYTE_SNAPSHOT`, `snapshot_valid: true`, and
  `mutable_path_current_at_return_guaranteed: false`; the adapter never reopens the caller path to
  imply a later path-current guarantee.

Tool names, descriptions, and JSON schemas are versioned public contracts. Unknown parameters are
rejected. Errors use structured envelopes and must not expose input content, credentials, or local
absolute paths.

The wrapper delegates the complete study contract to the core: every array is limited to 10,000
items, aggregate records to 50,000, and aggregate nested identifier references to 100,000. These are
resource limits, not evidence-completeness claims. A source response can be converted with
`receipt_from_call` followed by `study_receipt_from_call`; the latter emits a closed inline
`TOOLUNIVERSE_CALL` provenance envelope on a `SOURCE` receipt. Core validation rejects attempts to
relabel that envelope as material, assembly, sequence, annotation, observation, or correspondence
authority.

v0.1 has no semantic entities for experiments, sequencing runs, files, variants or structural
variants, tissues, stages, assays, or conditions. A ToolUniverse response concerning any of those
concepts can be bound only as a closed `SOURCE` provenance envelope; PhytoLocus does not parse it
into an entity or reconcile its biological scope. Local artifact projection likewise commits bytes
and declared provenance only.

The packaged fixture validation mode is self-contained study recomputation and therefore rejects an
`expected_capsule_digest`; an external digest applies only to a caller-supplied `capsule_dir`.
Caller-supplied capsules require either `study` or `expected_capsule_digest`. A known malformed or
tampered capsule returns `CAPSULE_INVALID`; a missing or unreadable capsule returns
`CAPSULE_UNAVAILABLE`; parameter-contract failures return `INVALID_ARGUMENTS`.

## Install and discover

Install the project with its exact optional dependency:

```bash
python -m pip install -e '.[tooluniverse]'
```

Then load only the expected tools:

```python
from tooluniverse import ToolUniverse

names = [
    "PhytoLocus_preflight",
    "PhytoLocus_compile",
    "PhytoLocus_validate_capsule",
]
tu = ToolUniverse()
tu.load_tools(include_tools=names)
assert set(names) <= set(tu.all_tool_dict)
```

## Authority boundary

ToolUniverse registration proves that a wrapper can be discovered and invoked. It does not prove
that the wrapper's input is true, authenticate a physical stock, make a remote API complete, or
grant permission to upload data. A successful envelope means only that the requested software
operation completed. The embedded reconciliation state and diagnostics remain controlling.

The plugin must not:

- perform undeclared network discovery during compilation;
- accept private data through a packaged fixture;
- turn an adapter response into independent authentication;
- downgrade `HOLD`, discard diagnostics, or relabel output as PASS;
- rank or select a target;
- write directly to an external evidence or persistent-memory system.

## ToolUniverse 1.4.1 capability audit

The following neutral public-control observations were reproduced against ToolUniverse 1.4.1 on
2026-08-13. They are adapter contract evidence, not guarantees that an upstream service will remain
unchanged.

| Evidence area | Tested result | Consequence for PhytoLocus |
| --- | --- | --- |
| NCBI assembly metadata | PASS for the Arabidopsis reference; versioned assembly metadata and sequence reports were returned | Commit the response as `SOURCE` provenance; the caller must still supply the closed assembly and sequence objects |
| NCBI taxon assembly census | Reported 371 assemblies; tool schema returns at most 100 and exposes no cursor, offset, or page token | A census or absence claim is impossible; the source receipt records `TRUNCATED_WITHOUT_CURSOR` |
| Ensembl plant discovery | Species search returned zero Arabidopsis matches while direct plant lookup, overlap, sequence, and assembly endpoints worked | Commit the response only as `SOURCE` provenance; discovery failure cannot establish unsupported species or absent locus |
| Ensembl assembly release | Direct endpoint reported TAIR10 / `GCA_000001735.1`; NCBI reported TAIR10.1 / `GCF_000001735.4` with paired `GCA_000001735.2` | Commit each response separately; any assembly, sequence, or locus crosswalk remains a caller-supplied v0.1 claim |
| Ensembl archive | Plant identifier history failed in the tested wrapper | The failed response cannot establish a transcript-model version or `SUPERSEDES` claim |
| Ensembl orthology | Uppercase Compara plant call failed; lowercase homology returned a plant orthologue | Commit the exact response as `SOURCE` provenance only; v0.1 has no orthology entity |
| Expression Atlas | Upstream experiment discovery passed; the gene-expression endpoint returns an arbitrary gene sample and a missing target is not evidence of absence | The response can be committed only as `SOURCE` provenance; v0.1 has no experiment or expression entity |
| Bgee | Installed adapter exposes animal species only | Do not treat this adapter as plant evidence; v0.1 has no expression entity |
| SRA SDL and ENA | Exact upstream file locations, sizes, and MD5 values were returned | The response or downloaded artifact can be byte/provenance evidence only; v0.1 has no run or file entity, and search results remain non-exhaustive without complete pagination |
| EVA | The tested plant species key worked, but the tool has no species-key discovery surface | The response can be `SOURCE` provenance only; v0.1 has no variant/SV entity, and an empty guessed key is not absence |
| OMA | Gene xref resolution returned two protein identities; an orthology request with `per_page=5` emitted 609 records | Commit coverage and response hashes only; the caller must separately declare any intended protein sequence object, and v0.1 has no orthology entity |
| PubMed and Europe PMC | Literature discovery passed | Commit discovery as `SOURCE` provenance only; v0.1 has no literature-evidence entity |

No installed tool produced one deterministic, content-addressed chain from a physical-stock claim
to all evidence needed for wet-lab adjudication. PhytoLocus v0.1 does not claim to implement that
chain. It reconciles only its closed material/assembly/sequence/locus/transcript/observation/
correspondence/receipt graph, cannot authenticate actual stock, and keeps external authority and
unsupported scopes outside the compiler.

## Integration tests

Tests cover direct wrapper invocation, registration metadata, loaded-tool invocation, JSON-schema
closure, structured refusals, path redaction, resource-limit refusal, source-call provenance
conversion and anti-laundering, clean-wheel discovery, and equality with core compiler bytes. The
exact ToolUniverse version is pinned so upstream schema or plugin-loader changes cannot be silently
accepted.

Run opt-in neutral public monitoring with:

```bash
PHYTOLOCUS_RUN_LIVE=1 python -m pytest tests/test_tooluniverse_live.py -q
```

The default test suite remains offline and deterministic. A live PASS demonstrates only the
documented source contract at that time; a live failure can indicate either a wrapper defect or an
upstream change and must be reviewed before updating fixtures.
