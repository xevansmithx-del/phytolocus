from __future__ import annotations

import copy
import json
import os
from dataclasses import replace

import pytest

from phytolocus import Study, ValidationError, compile_study
from phytolocus.engine import canonical_json_bytes, sha256_json, subject_sha256
from phytolocus.io import load_json, parse_json_bytes


def study_document() -> dict:
    return {
        "schema_version": "phytolocus.study.v0.1",
        "study_id": "neutral-study",
        "species": "Synthetic plant",
        "bound_memory_head_sha256": "0" * 64,
        "receipts": [],
        "materials": [
            {
                "id": "stock-a",
                "kind": "STOCK",
                "biological_origin_id": "origin-a",
                "parent_material_id": None,
                "independence_state": "DISTINCT_DECLARED",
                "receipt_ids": [],
            }
        ],
        "assemblies": [
            {
                "id": "assembly-a",
                "material_id": "stock-a",
                "release": "synthetic-v1",
                "phasing_state": "PHASED",
                "completeness_state": "DECLARED_COMPLETE",
                "receipt_ids": [],
            }
        ],
        "sequence_objects": [
            {
                "id": "genomic-a",
                "kind": "GENOMIC",
                "sequence_sha256": "1" * 64,
                "length": 1000,
                "alphabet": "DNA",
                "assembly_id": "assembly-a",
                "material_id": "stock-a",
                "source_record": "chr1",
                "source_version": "v1",
                "receipt_ids": [],
            },
            {
                "id": "transcript-a",
                "kind": "TRANSCRIPT",
                "sequence_sha256": "2" * 64,
                "length": 100,
                "alphabet": "RNA",
                "assembly_id": "assembly-a",
                "material_id": "stock-a",
                "source_record": "synthetic-transcript",
                "source_version": "v1",
                "receipt_ids": [],
            },
        ],
        "locus_models": [
            {
                "id": "locus-a",
                "assembly_id": "assembly-a",
                "contig": "chr1",
                "start": 1,
                "end": 1000,
                "strand": "+",
                "model_version": "v1",
                "sequence_object_ids": ["genomic-a"],
                "receipt_ids": [],
            }
        ],
        "transcript_models": [
            {
                "id": "model-a",
                "locus_model_id": "locus-a",
                "transcript_sequence_id": "transcript-a",
                "cds_sequence_id": None,
                "protein_sequence_id": None,
                "isoform_label": "isoform-1",
                "model_version": "v1",
                "receipt_ids": [],
            }
        ],
        "observations": [],
        "correspondence_claims": [],
        "target_selection": {"allowed": False, "status": "PROHIBITED"},
    }


def test_deterministic_compilation_is_authority_bounded() -> None:
    first = compile_study(study_document())
    second = compile_study(copy.deepcopy(study_document()))
    assert canonical_json_bytes(first.reconciliation) == canonical_json_bytes(second.reconciliation)
    assert first.reconciliation["producer_status"] == "HOLD"
    assert first.reconciliation["hold_reasons"] == [
        "COPY_UNRESOLVED",
        "EXTERNAL_RECEIPT_AUTHORITY_NOT_ESTABLISHED",
        "HAPLOTYPE_UNRESOLVED",
        "ISOFORM_UNRESOLVED",
    ]
    assert first.reconciliation["admissibility"]["all_material_identities"] == "UNRESOLVED"
    assert first.pec_candidate_evidence["scientific_gate_closure"] is False
    assert all(
        gate["disposition"] == "HOLD" for gate in first.pec_candidate_evidence["gate_candidates"]
    )
    assert first.phytoresolve_proposed_events["producer_wrote_persistent_state"] is False


def test_direct_api_bounds_top_level_collections() -> None:
    value = study_document()
    value["receipts"] = [{}] * 10_001
    with pytest.raises(ValidationError, match="10000-item collection limit"):
        Study.from_dict(value)


def test_direct_api_bounds_nested_identifier_collections() -> None:
    value = study_document()
    value["locus_models"][0]["sequence_object_ids"] = ["genomic-a"] * 10_001
    with pytest.raises(ValidationError, match="10000-item collection limit"):
        Study.from_dict(value)


def test_direct_api_bounds_aggregate_nested_references_before_model_expansion() -> None:
    value = study_document()
    template = value["locus_models"][0]
    value["locus_models"] = [
        {
            **copy.deepcopy(template),
            "id": f"locus-budget-{index}",
            "sequence_object_ids": ["genomic-a"] * 10_000,
        }
        for index in range(11)
    ]
    with pytest.raises(ValidationError, match="100000-reference aggregate limit"):
        Study.from_dict(value)


def test_coordinate_mirrors_are_representations_not_copies() -> None:
    value = study_document()
    mirror = copy.deepcopy(value["locus_models"][0])
    mirror.update({"id": "locus-mirror", "strand": "-"})
    value["locus_models"].append(mirror)
    result = compile_study(value).reconciliation
    assert "COPY_COLLAPSED" in result["hold_reasons"]
    component = result["coordinate_representation_components"][0]
    assert component["locus_model_ids"] == ["locus-a", "locus-mirror"]
    assert component["same_interval_not_copy_count"] is True


def test_same_strand_coordinate_duplicate_is_not_exported_as_two_targets() -> None:
    value = study_document()
    duplicate = copy.deepcopy(value["locus_models"][0])
    duplicate["id"] = "locus-duplicate"
    value["locus_models"].append(duplicate)
    result = compile_study(value).reconciliation
    assert "COPY_COLLAPSED" in result["hold_reasons"]
    handoff = result["downstream_handoffs"]["phytoperturb"]
    assert handoff["candidate_target_ids"] == []
    assert handoff["ambiguous_representation_groups"] == [["locus-a", "locus-duplicate"]]


def test_same_interval_with_incompatible_genomic_content_is_an_explicit_conflict() -> None:
    value = study_document()
    value["sequence_objects"].append(
        {
            **copy.deepcopy(value["sequence_objects"][0]),
            "id": "genomic-conflict",
            "sequence_sha256": "f" * 64,
        }
    )
    value["locus_models"].append(
        {
            **copy.deepcopy(value["locus_models"][0]),
            "id": "locus-conflict",
            "sequence_object_ids": ["genomic-conflict"],
        }
    )
    first = compile_study(value).reconciliation
    reversed_value = copy.deepcopy(value)
    reversed_value["sequence_objects"].reverse()
    reversed_value["locus_models"].reverse()
    second = compile_study(reversed_value).reconciliation
    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    assert "SEQUENCE_HASH_CONFLICT" in first["hold_reasons"]
    component = first["coordinate_representation_components"][0]
    assert component["locus_model_ids"] == ["locus-a", "locus-conflict"]
    assert component["sequence_content_conflict"] is True
    assert first["downstream_handoffs"]["phytoperturb"]["candidate_target_ids"] == []


def test_one_locus_rejects_incompatible_full_span_genomic_content_order_invariant() -> None:
    value = study_document()
    value["sequence_objects"].append(
        {
            **copy.deepcopy(value["sequence_objects"][0]),
            "id": "genomic-conflict",
            "sequence_sha256": "f" * 64,
        }
    )
    value["locus_models"][0]["sequence_object_ids"] = ["genomic-a", "genomic-conflict"]
    for order in (["genomic-a", "genomic-conflict"], ["genomic-conflict", "genomic-a"]):
        value["locus_models"][0]["sequence_object_ids"] = order
        with pytest.raises(ValidationError, match="SEQUENCE_HASH_CONFLICT"):
            Study.from_dict(copy.deepcopy(value))


def test_one_locus_distinguishes_full_span_from_partial_genomic_evidence() -> None:
    value = study_document()
    value["sequence_objects"].append(
        {
            **copy.deepcopy(value["sequence_objects"][0]),
            "id": "genomic-partial",
            "length": 500,
            "sequence_sha256": "f" * 64,
        }
    )
    value["locus_models"][0]["sequence_object_ids"] = ["genomic-a", "genomic-partial"]
    normalized = Study.from_dict(value).to_dict()
    assert normalized["locus_models"][0]["sequence_object_ids"] == [
        "genomic-a",
        "genomic-partial",
    ]


def test_exact_locus_representations_across_same_origin_assemblies_are_withheld() -> None:
    value = study_document()
    value["materials"].append(
        {
            "id": "sample-b",
            "kind": "SAMPLE",
            "biological_origin_id": "origin-a",
            "parent_material_id": "stock-a",
            "independence_state": "SAME_ORIGIN",
            "receipt_ids": [],
        }
    )
    value["assemblies"].append(
        {
            "id": "assembly-b",
            "material_id": "sample-b",
            "release": "synthetic-v2",
            "phasing_state": "PHASED",
            "completeness_state": "DECLARED_COMPLETE",
            "receipt_ids": [],
        }
    )
    value["sequence_objects"].append(
        {
            **copy.deepcopy(value["sequence_objects"][0]),
            "id": "genomic-b",
            "assembly_id": "assembly-b",
            "material_id": "sample-b",
        }
    )
    value["locus_models"].append(
        {
            **copy.deepcopy(value["locus_models"][0]),
            "id": "locus-b",
            "assembly_id": "assembly-b",
            "start": 101,
            "end": 1100,
            "sequence_object_ids": ["genomic-b"],
        }
    )
    result = compile_study(value).reconciliation
    assert "COPY_COLLAPSED" in result["hold_reasons"]
    component = result["coordinate_representation_components"][0]
    assert component["locus_model_ids"] == ["locus-a", "locus-b"]
    assert component["component_basis"] == ["SAME_ORIGIN_EXACT_LOCUS_SEQUENCE"]
    assert component["start_values"] == [1, 101]
    handoff = result["downstream_handoffs"]["phytoperturb"]
    assert handoff["candidate_target_ids"] == []
    assert handoff["ambiguous_representation_groups"] == [["locus-a", "locus-b"]]


def test_redundant_sequence_ids_cannot_bypass_representation_grouping() -> None:
    value = study_document()
    value["materials"].append(
        {
            "id": "sample-b",
            "kind": "SAMPLE",
            "biological_origin_id": "origin-a",
            "parent_material_id": "stock-a",
            "independence_state": "SAME_ORIGIN",
            "receipt_ids": [],
        }
    )
    value["assemblies"].append(
        {
            "id": "assembly-b",
            "material_id": "sample-b",
            "release": "synthetic-v2",
            "phasing_state": "UNKNOWN",
            "completeness_state": "PARTIAL",
            "receipt_ids": [],
        }
    )
    for sequence_id in ("genomic-b", "genomic-b-redundant"):
        value["sequence_objects"].append(
            {
                **copy.deepcopy(value["sequence_objects"][0]),
                "id": sequence_id,
                "assembly_id": "assembly-b",
                "material_id": "sample-b",
            }
        )
    value["locus_models"].append(
        {
            **copy.deepcopy(value["locus_models"][0]),
            "id": "locus-b",
            "assembly_id": "assembly-b",
            "sequence_object_ids": ["genomic-b", "genomic-b-redundant"],
        }
    )
    result = compile_study(value).reconciliation
    assert result["downstream_handoffs"]["phytoperturb"]["candidate_target_ids"] == []
    assert result["coordinate_representation_components"][0]["locus_model_ids"] == [
        "locus-a",
        "locus-b",
    ]


def test_large_duplicate_component_compiles_with_linear_union_edges() -> None:
    value = study_document()
    template = value["locus_models"][0]
    value["locus_models"] = [
        {**copy.deepcopy(template), "id": f"locus-{index:04d}"} for index in range(1_000)
    ]
    value["transcript_models"][0]["locus_model_id"] = "locus-0000"
    component = compile_study(value).reconciliation["coordinate_representation_components"][0]
    assert component["representation_count"] == 1_000
    assert len(component["locus_model_ids"]) == 1_000


def test_many_disjoint_pair_components_retain_linear_basis_aggregation() -> None:
    value = study_document()
    sequence_template = value["sequence_objects"][0]
    locus_template = value["locus_models"][0]
    value["sequence_objects"] = []
    value["locus_models"] = []
    value["transcript_models"] = []
    for index in range(1_000):
        sequence_id = f"genomic-pair-{index:04d}"
        value["sequence_objects"].append(
            {
                **copy.deepcopy(sequence_template),
                "id": sequence_id,
                "length": 1,
                "sequence_sha256": f"{index + 1:064x}",
                "source_record": f"record-{index:04d}",
            }
        )
        for suffix in ("a", "b"):
            value["locus_models"].append(
                {
                    **copy.deepcopy(locus_template),
                    "contig": f"record-{index:04d}",
                    "end": index + 1,
                    "id": f"locus-pair-{index:04d}-{suffix}",
                    "sequence_object_ids": [sequence_id],
                    "start": index + 1,
                }
            )
    components = compile_study(value).reconciliation["coordinate_representation_components"]
    assert len(components) == 1_000
    assert all(
        "SAME_ASSEMBLY_INTERVAL" in component["component_basis"]
        and component["representation_count"] == 2
        for component in components
    )


def test_represents_requires_same_kind_biological_objects() -> None:
    value = study_document()
    value["observations"].append(
        {
            "id": "invalid-representation-observation",
            "kind": "MODEL_CROSSWALK",
            "subject_id": "transcript-a",
            "object_id": "stock-a",
            "result_state": "POSITIVE",
            "evidence_sha256": "9" * 64,
            "receipt_ids": [],
        }
    )
    value["correspondence_claims"].append(
        {
            "id": "invalid-representation",
            "left_id": "transcript-a",
            "right_id": "stock-a",
            "relation": "REPRESENTS",
            "declared_state": "EXACT",
            "observation_ids": ["invalid-representation-observation"],
            "receipt_ids": [],
        }
    )
    with pytest.raises(ValidationError, match="CORRESPONDENCE_SCOPE_OR_TYPE_INVALID"):
        Study.from_dict(value)


def test_transcript_declaring_another_assembly_requires_bound_crosswalk() -> None:
    value = study_document()
    value["materials"].append(
        {
            "id": "sample-b",
            "kind": "SAMPLE",
            "biological_origin_id": "origin-a",
            "parent_material_id": "stock-a",
            "independence_state": "SAME_ORIGIN",
            "receipt_ids": [],
        }
    )
    value["assemblies"].append(
        {
            "id": "assembly-b",
            "material_id": "sample-b",
            "release": "other-release",
            "phasing_state": "UNKNOWN",
            "completeness_state": "PARTIAL",
            "receipt_ids": [],
        }
    )
    value["sequence_objects"][1]["assembly_id"] = "assembly-b"
    value["sequence_objects"][1]["material_id"] = "sample-b"
    with pytest.raises(ValidationError, match="TRANSCRIPT_ASSEMBLY_SCOPE_UNRESOLVED"):
        Study.from_dict(value)


def test_transcript_annotation_version_mismatch_adds_explicit_hold() -> None:
    value = study_document()
    value["transcript_models"][0]["model_version"] = "unrelated-annotation-v99"
    result = compile_study(value).reconciliation
    assert "TRANSCRIPT_SCOPE_CROSSWALK_EXTERNAL_AUTHORITY_UNRESOLVED" in result["hold_reasons"]


def test_bare_supersession_cannot_remove_annotation_scope_hold() -> None:
    value = study_document()
    value["transcript_models"][0]["model_version"] = "unrelated-v99"
    old = copy.deepcopy(value["transcript_models"][0])
    old.update({"id": "model-old", "model_version": "v1"})
    value["transcript_models"].append(old)
    value["correspondence_claims"].append(
        {
            "id": "bare-version-edge",
            "left_id": "model-a",
            "right_id": "model-old",
            "relation": "SUPERSEDES",
            "declared_state": "UNRESOLVED",
            "observation_ids": [],
            "receipt_ids": [],
        }
    )
    result = compile_study(value).reconciliation
    assert "TRANSCRIPT_SCOPE_CROSSWALK_EXTERNAL_AUTHORITY_UNRESOLVED" in result["hold_reasons"]
    assert result["supersession_edges"] == []


@pytest.mark.parametrize("result_state", ["NEGATIVE", "NO_RESULT"])
def test_nonpositive_supersession_evidence_does_not_activate_version_graph(
    result_state: str,
) -> None:
    value = study_document()
    for model_id in ("model-b", "model-c"):
        model = copy.deepcopy(value["transcript_models"][0])
        model["id"] = model_id
        value["transcript_models"].append(model)
        observation_id = f"{model_id}-observation"
        value["observations"].append(
            {
                "id": observation_id,
                "kind": "MODEL_CROSSWALK",
                "subject_id": model_id,
                "object_id": "model-a",
                "result_state": result_state,
                "evidence_sha256": "e" * 64,
                "receipt_ids": [],
            }
        )
        value["correspondence_claims"].append(
            {
                "id": f"{model_id}-supersedes",
                "left_id": model_id,
                "right_id": "model-a",
                "relation": "SUPERSEDES",
                "declared_state": "SUPPORTED",
                "observation_ids": [observation_id],
                "receipt_ids": [],
            }
        )
    first = compile_study(value).reconciliation
    reversed_value = copy.deepcopy(value)
    reversed_value["observations"].reverse()
    reversed_value["correspondence_claims"].reverse()
    second = compile_study(reversed_value).reconciliation
    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    assert first["supersession_edges"] == []
    assert "MODEL_VERSION_CONFLICT" not in first["hold_reasons"]


def test_partial_stock_assembly_retains_copy_and_completeness_holds() -> None:
    value = study_document()
    value["assemblies"][0]["completeness_state"] = "PARTIAL"
    holds = compile_study(value).reconciliation["hold_reasons"]
    assert "ASSEMBLY_COMPLETENESS_UNRESOLVED" in holds
    assert "COPY_UNRESOLVED" in holds


def test_same_coordinates_on_different_assemblies_are_not_collapsed() -> None:
    value = study_document()
    value["materials"].append(
        {
            "id": "sample-b",
            "kind": "SAMPLE",
            "biological_origin_id": "origin-b",
            "parent_material_id": None,
            "independence_state": "DISTINCT_DECLARED",
            "receipt_ids": [],
        }
    )
    value["assemblies"].append(
        {
            "id": "assembly-b",
            "material_id": "sample-b",
            "release": "synthetic-v1",
            "phasing_state": "PHASED",
            "completeness_state": "DECLARED_COMPLETE",
            "receipt_ids": [],
        }
    )
    value["sequence_objects"].append(
        {
            "id": "genomic-b",
            "kind": "GENOMIC",
            "sequence_sha256": "3" * 64,
            "length": 1000,
            "alphabet": "DNA",
            "assembly_id": "assembly-b",
            "material_id": "sample-b",
            "source_record": "chr1",
            "source_version": "v1",
            "receipt_ids": [],
        }
    )
    value["locus_models"].append(
        {
            "id": "locus-b",
            "assembly_id": "assembly-b",
            "contig": "chr1",
            "start": 1,
            "end": 1000,
            "strand": "+",
            "model_version": "v1",
            "sequence_object_ids": ["genomic-b"],
            "receipt_ids": [],
        }
    )
    result = compile_study(value).reconciliation
    assert result["coordinate_representation_components"] == []
    assert "COPY_COLLAPSED" not in result["hold_reasons"]
    handoff = result["downstream_handoffs"]["phytoperturb"]
    assert handoff["candidate_target_ids"] == ["locus-a"]
    assert handoff["withheld_non_stock_locus_ids"] == ["locus-b"]


def test_two_isoforms_remain_two_transcripts_under_one_locus() -> None:
    value = study_document()
    value["sequence_objects"].append(
        {
            "id": "transcript-short",
            "kind": "TRANSCRIPT",
            "sequence_sha256": "4" * 64,
            "length": 55,
            "alphabet": "RNA",
            "assembly_id": "assembly-a",
            "material_id": "stock-a",
            "source_record": "synthetic-transcript-short",
            "source_version": "v1",
            "receipt_ids": [],
        }
    )
    value["transcript_models"].append(
        {
            "id": "model-short",
            "locus_model_id": "locus-a",
            "transcript_sequence_id": "transcript-short",
            "cds_sequence_id": None,
            "protein_sequence_id": None,
            "isoform_label": "short",
            "model_version": "v1",
            "receipt_ids": [],
        }
    )
    compiled = compile_study(value)
    assert len(compiled.study_lock["normalized_study"]["locus_models"]) == 1
    assert len(compiled.study_lock["normalized_study"]["transcript_models"]) == 2


def test_same_sequence_exact_claim_with_different_digest_becomes_conflict() -> None:
    value = study_document()
    value["sequence_objects"].append(
        {
            "id": "transcript-b",
            "kind": "TRANSCRIPT",
            "sequence_sha256": "5" * 64,
            "length": 100,
            "alphabet": "RNA",
            "assembly_id": "assembly-a",
            "material_id": "stock-a",
            "source_record": "synthetic-transcript-b",
            "source_version": "v1",
            "receipt_ids": [],
        }
    )
    value["correspondence_claims"].append(
        {
            "id": "same-sequence-claim",
            "left_id": "transcript-a",
            "right_id": "transcript-b",
            "relation": "SAME_SEQUENCE",
            "declared_state": "EXACT",
            "observation_ids": [],
            "receipt_ids": [],
        }
    )
    row = compile_study(value).reconciliation["correspondence_claims"][0]
    assert row["technical_state"] == "CONFLICT"
    assert row["admissible_state"] == "CONFLICT"
    assert "SEQUENCE_HASH_CONFLICT" in row["conflict_codes"]


def test_negative_observation_never_becomes_absence() -> None:
    value = study_document()
    value["observations"].append(
        {
            "id": "failed-assay",
            "kind": "COVERAGE",
            "subject_id": "locus-a",
            "object_id": None,
            "result_state": "NEGATIVE",
            "evidence_sha256": "6" * 64,
            "receipt_ids": [],
        }
    )
    result = compile_study(value).reconciliation
    assert "NEGATIVE_NOT_ABSENCE" in result["hold_reasons"]
    assert result["admissibility"]["copy_count_state"] == "UNRESOLVED"


def test_same_origin_representations_count_once_technically_and_zero_admissibly() -> None:
    value = study_document()
    value["materials"].append(
        {
            "id": "hap-b",
            "kind": "HAPLOTYPE_REPRESENTATION",
            "biological_origin_id": "origin-a",
            "parent_material_id": "stock-a",
            "independence_state": "SAME_ORIGIN",
            "receipt_ids": [],
        }
    )
    material = compile_study(value).reconciliation["material_origin_analysis"]
    assert material["technical_independent_origin_count"] == 1
    assert material["admissible_independent_biological_unit_count"] == 0
    assert material["origin_groups"][0]["material_ids"] == ["hap-b", "stock-a"]


def test_same_origin_child_cannot_claim_conflicting_origin() -> None:
    value = study_document()
    value["materials"].append(
        {
            "id": "hap-b",
            "kind": "HAPLOTYPE_REPRESENTATION",
            "biological_origin_id": "origin-conflict",
            "parent_material_id": "stock-a",
            "independence_state": "SAME_ORIGIN",
            "receipt_ids": [],
        }
    )
    with pytest.raises(ValidationError, match="MATERIAL_ORIGIN_CONFLICT"):
        Study.from_dict(value)


def test_same_origin_component_closes_transitively_and_counts_once() -> None:
    value = study_document()
    value["materials"][0]["biological_origin_id"] = None
    for material_id, parent_id in (("sample-b", "stock-a"), ("sample-c", "sample-b")):
        value["materials"].append(
            {
                "id": material_id,
                "kind": "SAMPLE",
                "biological_origin_id": None,
                "parent_material_id": parent_id,
                "independence_state": "SAME_ORIGIN",
                "receipt_ids": [],
            }
        )
    result = compile_study(value).reconciliation["material_origin_analysis"]
    assert result["technical_independent_origin_count"] == 1
    assert result["origin_groups"][0]["material_ids"] == ["sample-b", "sample-c", "stock-a"]


def test_same_origin_component_rejects_conflicting_child_labels() -> None:
    value = study_document()
    value["materials"][0]["biological_origin_id"] = None
    for material_id, origin_id in (("sample-b", "origin-b"), ("sample-c", "origin-c")):
        value["materials"].append(
            {
                "id": material_id,
                "kind": "SAMPLE",
                "biological_origin_id": origin_id,
                "parent_material_id": "stock-a",
                "independence_state": "SAME_ORIGIN",
                "receipt_ids": [],
            }
        )
    with pytest.raises(ValidationError, match="MATERIAL_ORIGIN_CONFLICT"):
        Study.from_dict(value)


@pytest.mark.parametrize("parent_id", [None, "stock-a"])
def test_same_origin_component_rejects_multiple_distinct_materials(
    parent_id: str | None,
) -> None:
    value = study_document()
    value["materials"].append(
        {
            "id": "stock-b",
            "kind": "SAMPLE" if parent_id is not None else "STOCK",
            "biological_origin_id": "origin-a",
            "parent_material_id": parent_id,
            "independence_state": "DISTINCT_DECLARED",
            "receipt_ids": [],
        }
    )
    with pytest.raises(ValidationError, match="MATERIAL_ORIGIN_CONFLICT"):
        Study.from_dict(value)


def test_unresolved_disconnected_materials_do_not_become_independent_units() -> None:
    value = study_document()
    value["materials"][0]["biological_origin_id"] = None
    value["materials"][0]["independence_state"] = "UNRESOLVED"
    for material_id in ("sample-b", "sample-c"):
        value["materials"].append(
            {
                "id": material_id,
                "kind": "SAMPLE",
                "biological_origin_id": None,
                "parent_material_id": None,
                "independence_state": "UNRESOLVED",
                "receipt_ids": [],
            }
        )
    result = compile_study(value).reconciliation["material_origin_analysis"]
    assert result["technical_independent_origin_count"] == 0
    assert result["admissible_independent_biological_unit_count"] == 0
    assert result["unresolved_origin_component_count"] == 3
    assert all(
        group["origin_resolution_state"] == "UNRESOLVED_COMPONENT"
        and group["technical_independent_origin_count"] == 0
        for group in result["origin_groups"]
    )
    assert "MATERIAL_IDENTITY_MISSING" in compile_study(value).reconciliation["hold_reasons"]


def test_sample_child_assembly_and_transcript_remain_in_stock_component() -> None:
    value = study_document()
    value["materials"].append(
        {
            "id": "sample-b",
            "kind": "SAMPLE",
            "biological_origin_id": "origin-a",
            "parent_material_id": "stock-a",
            "independence_state": "SAME_ORIGIN",
            "receipt_ids": [],
        }
    )
    value["assemblies"][0]["material_id"] = "sample-b"
    value["sequence_objects"][0]["material_id"] = "sample-b"
    value["sequence_objects"][1]["material_id"] = "sample-b"
    result = compile_study(value).reconciliation
    assert "STOCK_EVIDENCE_MISSING" not in result["hold_reasons"]
    assert result["stock_locus_candidate_matrix"][0]["stock_declared"] is False
    assert result["stock_locus_candidate_matrix"][0]["stock_origin_component_declared"] is True
    assert result["downstream_handoffs"]["phytoperturb"]["candidate_target_ids"] == ["locus-a"]


def test_transcript_sequence_on_same_origin_sample_may_support_stock_locus() -> None:
    value = study_document()
    value["materials"].append(
        {
            "id": "sample-b",
            "kind": "SAMPLE",
            "biological_origin_id": "origin-a",
            "parent_material_id": "stock-a",
            "independence_state": "SAME_ORIGIN",
            "receipt_ids": [],
        }
    )
    value["sequence_objects"][1]["assembly_id"] = None
    value["sequence_objects"][1]["material_id"] = "sample-b"
    Study.from_dict(value)


def test_transcript_model_cannot_cross_stock_or_assembly_context() -> None:
    value = study_document()
    value["materials"].append(
        {
            "id": "stock-b",
            "kind": "STOCK",
            "biological_origin_id": "origin-b",
            "parent_material_id": None,
            "independence_state": "DISTINCT_DECLARED",
            "receipt_ids": [],
        }
    )
    value["assemblies"].append(
        {
            "id": "assembly-b",
            "material_id": "stock-b",
            "release": "synthetic-v1",
            "phasing_state": "PHASED",
            "completeness_state": "DECLARED_COMPLETE",
            "receipt_ids": [],
        }
    )
    value["sequence_objects"][1]["assembly_id"] = "assembly-b"
    value["sequence_objects"][1]["material_id"] = "stock-b"
    with pytest.raises(ValidationError, match="TRANSCRIPT_CONTEXT_CONFLICT"):
        Study.from_dict(value)


def test_two_new_models_superseding_one_old_model_is_a_fork() -> None:
    value = study_document()
    for model_id, version in (("model-b", "v2"), ("model-c", "v3")):
        model = copy.deepcopy(value["transcript_models"][0])
        model.update({"id": model_id, "model_version": version})
        value["transcript_models"].append(model)
        value["correspondence_claims"].append(
            {
                "id": f"{model_id}-supersedes",
                "left_id": model_id,
                "right_id": "model-a",
                "relation": "SUPERSEDES",
                "declared_state": "SUPPORTED",
                "observation_ids": [],
                "receipt_ids": [],
            }
        )
    result = compile_study(value).reconciliation
    assert "MODEL_VERSION_CONFLICT" in result["hold_reasons"]
    assert {
        "competing_newer_object_ids": ["model-b", "model-c"],
        "forked_older_object_id": "model-a",
    } in result["supersession_edges"]


def test_one_new_model_may_supersede_multiple_old_models_without_fork() -> None:
    value = study_document()
    for model_id, version in (("model-old-b", "v0b"), ("model-new", "v2")):
        model = copy.deepcopy(value["transcript_models"][0])
        model.update({"id": model_id, "model_version": version})
        value["transcript_models"].append(model)
    for old_id in ("model-a", "model-old-b"):
        value["correspondence_claims"].append(
            {
                "id": f"model-new-supersedes-{old_id}",
                "left_id": "model-new",
                "right_id": old_id,
                "relation": "SUPERSEDES",
                "declared_state": "SUPPORTED",
                "observation_ids": [],
                "receipt_ids": [],
            }
        )
    result = compile_study(value).reconciliation
    assert "MODEL_VERSION_CONFLICT" not in result["hold_reasons"]


def test_long_supersession_chain_is_processed_without_quadratic_retraversal() -> None:
    value = study_document()
    template = value["transcript_models"][0]
    value["transcript_models"] = [
        {**copy.deepcopy(template), "id": f"model-{index:04d}"} for index in range(1_000)
    ]
    value["correspondence_claims"] = [
        {
            "id": f"edge-{index:04d}",
            "left_id": f"model-{index:04d}",
            "right_id": f"model-{index - 1:04d}",
            "relation": "SUPERSEDES",
            "declared_state": "SUPPORTED",
            "observation_ids": [],
            "receipt_ids": [],
        }
        for index in range(1, 1_000)
    ]
    result = compile_study(value).reconciliation
    assert len(result["supersession_edges"]) == 999
    assert "MODEL_VERSION_CONFLICT" not in result["hold_reasons"]


def test_receipt_subject_binding_is_technical_not_external_authority() -> None:
    value = study_document()
    material = Study.from_dict(value).materials[0]
    value["receipts"].append(
        {
            "id": "receipt-material",
            "kind": "MATERIAL",
            "subject_sha256": subject_sha256(material),
            "artifact_sha256": "7" * 64,
            "issuer": "caller-created",
            "verification_state": "RECEIPT_VERIFIED",
        }
    )
    value["materials"][0]["receipt_ids"] = ["receipt-material"]
    row = compile_study(value).reconciliation["receipt_bindings"][0]
    assert row["subject_matches"] is True
    assert row["external_authority_established"] is False


def test_receipt_provenance_is_inline_and_digest_bound() -> None:
    value = study_document()
    material = Study.from_dict(value).materials[0]
    provenance = {
        "arguments_sha256": "8" * 64,
        "artifact_kind": None,
        "byte_count": None,
        "capture_method": "TOOLUNIVERSE_CALL",
        "captured_at": "2026-08-13T18:00:00Z",
        "collection_audit": {
            "complete": False,
            "coverage_state": "TRUNCATED_WITHOUT_CURSOR",
            "next_cursor_present": False,
            "reported_total": 371,
            "requested_limit": 100,
            "returned_count": 100,
            "truncated_without_cursor": True,
        },
        "content_sha256": "9" * 64,
        "coordinate_convention": None,
        "format_version": None,
        "reference_accession": None,
        "schema_version": "phytolocus.receipt-provenance.v0.1",
        "source_checksum_sha256": None,
        "source_record": "taxon-3702",
        "source_release": "current",
        "tool_name": "NCBIDatasets_list_genomes_by_taxon",
        "tool_version": "ToolUniverse-1.4.1",
    }
    value["receipts"].append(
        {
            "artifact_sha256": sha256_json(provenance),
            "id": "receipt-material",
            "issuer": "ToolUniverse adapter",
            "kind": "SOURCE",
            "provenance": provenance,
            "subject_sha256": subject_sha256(material),
            "verification_state": "RECEIPT_VERIFIED",
        }
    )
    value["materials"][0]["receipt_ids"] = ["receipt-material"]
    row = compile_study(value).reconciliation["receipt_bindings"][0]
    assert row["provenance_envelope_bound"] is True
    assert row["provenance"]["collection_audit"]["coverage_state"] == ("TRUNCATED_WITHOUT_CURSOR")
    forged = copy.deepcopy(value)
    forged["receipts"][0]["provenance"]["source_release"] = "changed"
    with pytest.raises(ValidationError, match="does not bind"):
        Study.from_dict(forged)
    impossible_time = copy.deepcopy(value)
    impossible_time["receipts"][0]["provenance"]["captured_at"] = "2026-99-99T99:99:99Z"
    impossible_time["receipts"][0]["artifact_sha256"] = sha256_json(
        impossible_time["receipts"][0]["provenance"]
    )
    with pytest.raises(ValidationError, match="UTC RFC 3339"):
        Study.from_dict(impossible_time)


def test_local_artifact_provenance_cannot_masquerade_as_material_receipt() -> None:
    value = study_document()
    material = Study.from_dict(value).materials[0]
    provenance = {
        "arguments_sha256": None,
        "artifact_kind": "ANNOTATION_GFF3",
        "byte_count": 123,
        "capture_method": "LOCAL_ARTIFACT",
        "captured_at": None,
        "collection_audit": None,
        "content_sha256": "8" * 64,
        "coordinate_convention": "ONE_BASED_CLOSED",
        "format_version": "GFF3",
        "reference_accession": "synthetic-reference-v1",
        "schema_version": "phytolocus.receipt-provenance.v0.1",
        "source_checksum_sha256": None,
        "source_record": "synthetic-annotation",
        "source_release": "v1",
        "tool_name": None,
        "tool_version": None,
    }
    value["receipts"].append(
        {
            "artifact_sha256": sha256_json(provenance),
            "id": "receipt-material",
            "issuer": "caller",
            "kind": "MATERIAL",
            "provenance": provenance,
            "subject_sha256": subject_sha256(material),
            "verification_state": "RECEIPT_VERIFIED",
        }
    )
    value["materials"][0]["receipt_ids"] = ["receipt-material"]
    with pytest.raises(ValidationError, match="cannot support the requested receipt kind"):
        Study.from_dict(value)


def test_declaration_provenance_cannot_masquerade_as_local_artifact() -> None:
    value = study_document()
    material = Study.from_dict(value).materials[0]
    provenance = {
        "arguments_sha256": None,
        "artifact_kind": "ANNOTATION_GFF3",
        "byte_count": 123,
        "capture_method": "DECLARATION",
        "captured_at": None,
        "collection_audit": None,
        "content_sha256": "8" * 64,
        "coordinate_convention": "ONE_BASED_CLOSED",
        "format_version": "GFF3",
        "reference_accession": "synthetic-reference-v1",
        "schema_version": "phytolocus.receipt-provenance.v0.1",
        "source_checksum_sha256": None,
        "source_record": "synthetic-annotation",
        "source_release": "v1",
        "tool_name": None,
        "tool_version": None,
    }
    value["receipts"].append(
        {
            "artifact_sha256": sha256_json(provenance),
            "id": "receipt-material-declaration",
            "issuer": "caller",
            "kind": "MATERIAL",
            "provenance": provenance,
            "subject_sha256": subject_sha256(material),
            "verification_state": "DECLARED_NOT_VERIFIED",
        }
    )
    value["materials"][0]["receipt_ids"] = ["receipt-material-declaration"]
    with pytest.raises(ValidationError, match="DECLARATION provenance cannot masquerade"):
        Study.from_dict(value)


def test_local_artifact_provenance_rejects_mismatching_source_checksum() -> None:
    value = study_document()
    assembly = Study.from_dict(value).assemblies[0]
    provenance = {
        "arguments_sha256": None,
        "artifact_kind": "ASSEMBLY_FASTA",
        "byte_count": 123,
        "capture_method": "LOCAL_ARTIFACT",
        "captured_at": None,
        "collection_audit": None,
        "content_sha256": "8" * 64,
        "coordinate_convention": "NOT_APPLICABLE",
        "format_version": "FASTA",
        "reference_accession": None,
        "schema_version": "phytolocus.receipt-provenance.v0.1",
        "source_checksum_sha256": "9" * 64,
        "source_record": "synthetic-assembly",
        "source_release": None,
        "tool_name": None,
        "tool_version": None,
    }
    value["receipts"].append(
        {
            "artifact_sha256": sha256_json(provenance),
            "id": "receipt-assembly-checksum",
            "issuer": "caller",
            "kind": "ASSEMBLY",
            "provenance": provenance,
            "subject_sha256": subject_sha256(assembly),
            "verification_state": "DECLARED_NOT_VERIFIED",
        }
    )
    value["assemblies"][0]["receipt_ids"] = ["receipt-assembly-checksum"]
    with pytest.raises(ValidationError, match="source checksum does not match"):
        Study.from_dict(value)


def test_collection_provenance_rejects_count_above_reported_total() -> None:
    value = study_document()
    material = Study.from_dict(value).materials[0]
    provenance = {
        "arguments_sha256": "8" * 64,
        "artifact_kind": None,
        "byte_count": None,
        "capture_method": "TOOLUNIVERSE_CALL",
        "captured_at": "2026-08-13T18:00:00Z",
        "collection_audit": {
            "complete": False,
            "coverage_state": "COMPLETENESS_UNRESOLVED",
            "next_cursor_present": False,
            "reported_total": 1,
            "requested_limit": 100,
            "returned_count": 2,
            "truncated_without_cursor": False,
        },
        "content_sha256": "9" * 64,
        "coordinate_convention": None,
        "format_version": None,
        "reference_accession": None,
        "schema_version": "phytolocus.receipt-provenance.v0.1",
        "source_checksum_sha256": None,
        "source_record": "taxon-3702",
        "source_release": "current",
        "tool_name": "source-tool",
        "tool_version": "1.0",
    }
    value["receipts"].append(
        {
            "artifact_sha256": sha256_json(provenance),
            "id": "receipt-material",
            "issuer": "adapter",
            "kind": "MATERIAL",
            "provenance": provenance,
            "subject_sha256": subject_sha256(material),
            "verification_state": "DECLARED_NOT_VERIFIED",
        }
    )
    value["materials"][0]["receipt_ids"] = ["receipt-material"]
    with pytest.raises(ValidationError, match="exceeds reported_total"):
        Study.from_dict(value)


def test_tooluniverse_provenance_requires_method_specific_fields() -> None:
    value = study_document()
    material = Study.from_dict(value).materials[0]
    provenance = {
        "arguments_sha256": None,
        "artifact_kind": None,
        "byte_count": None,
        "capture_method": "TOOLUNIVERSE_CALL",
        "captured_at": None,
        "collection_audit": None,
        "content_sha256": "9" * 64,
        "coordinate_convention": None,
        "format_version": None,
        "reference_accession": None,
        "schema_version": "phytolocus.receipt-provenance.v0.1",
        "source_checksum_sha256": None,
        "source_record": None,
        "source_release": None,
        "tool_name": None,
        "tool_version": None,
    }
    value["receipts"].append(
        {
            "artifact_sha256": sha256_json(provenance),
            "id": "receipt-material",
            "issuer": "adapter",
            "kind": "MATERIAL",
            "provenance": provenance,
            "subject_sha256": subject_sha256(material),
            "verification_state": "DECLARED_NOT_VERIFIED",
        }
    )
    value["materials"][0]["receipt_ids"] = ["receipt-material"]
    with pytest.raises(ValidationError, match="TOOLUNIVERSE_CALL provenance requires"):
        Study.from_dict(value)


def test_receipt_id_order_is_set_canonical() -> None:
    value = study_document()
    material = Study.from_dict(value).materials[0]
    for receipt_id, artifact in (("receipt-a", "a" * 64), ("receipt-b", "b" * 64)):
        value["receipts"].append(
            {
                "artifact_sha256": artifact,
                "id": receipt_id,
                "issuer": "caller",
                "kind": "MATERIAL",
                "subject_sha256": subject_sha256(material),
                "verification_state": "DECLARED_NOT_VERIFIED",
            }
        )
    value["materials"][0]["receipt_ids"] = ["receipt-a", "receipt-b"]
    first = compile_study(value).study_lock["input_sha256"]
    value["materials"][0]["receipt_ids"] = ["receipt-b", "receipt-a"]
    assert compile_study(value).study_lock["input_sha256"] == first


def test_direct_dataclass_mutation_is_revalidated() -> None:
    study = Study.from_dict(study_document())
    bad = replace(study, target_selection={"allowed": True, "status": "SELECTED"})
    with pytest.raises(ValidationError, match="target selection"):
        compile_study(bad)


def test_parent_cycle_and_unknown_fields_reject() -> None:
    value = study_document()
    value["materials"][0]["parent_material_id"] = "sample-b"
    value["materials"].append(
        {
            "id": "sample-b",
            "kind": "SAMPLE",
            "biological_origin_id": "origin-a",
            "parent_material_id": "stock-a",
            "independence_state": "SAME_ORIGIN",
            "receipt_ids": [],
        }
    )
    with pytest.raises(ValidationError, match="parent cycle"):
        Study.from_dict(value)
    bad = study_document()
    bad["recommended_target"] = "locus-a"
    with pytest.raises(ValidationError, match="extra"):
        Study.from_dict(bad)


def test_json_boundary_rejects_duplicates_nonfinite_and_huge_integer() -> None:
    with pytest.raises(ValidationError, match="duplicate key"):
        parse_json_bytes(b'{"x":1,"x":2}')
    with pytest.raises(ValidationError, match="non-finite"):
        parse_json_bytes(b'{"x":1e999}')
    with pytest.raises(ValidationError, match="digit limit"):
        parse_json_bytes(b'{"x":' + b"9" * 1001 + b"}")


def test_file_loader_detects_in_place_rewrite_even_if_mtime_is_restored(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "large.json"
    path.write_bytes(b'{"payload":"' + b"A" * 1_100_000 + b'"}')
    original_status = path.stat()
    original_read = os.read
    mutated = False

    def mutate_after_first_read(descriptor: int, size: int) -> bytes:
        nonlocal mutated
        block = original_read(descriptor, size)
        if not mutated and block:
            mutated = True
            with path.open("r+b") as handle:
                handle.seek(20)
                handle.write(b"X")
                handle.seek(1_050_000)
                handle.write(b"Y")
                handle.flush()
                os.fsync(handle.fileno())
            os.utime(
                path,
                ns=(original_status.st_atime_ns, original_status.st_mtime_ns),
            )
        return block

    monkeypatch.setattr("phytolocus.io.os.read", mutate_after_first_read)
    with pytest.raises(ValidationError, match="changed while reading"):
        load_json(path)


def test_direct_mapping_rejects_lone_surrogate_at_model_boundary() -> None:
    value = study_document()
    value["species"] = "bad\ud800species"
    with pytest.raises(ValidationError, match="Unicode scalar"):
        Study.from_dict(value)


def test_haplotype_relation_requires_material_on_right() -> None:
    value = study_document()
    other = copy.deepcopy(value["locus_models"][0])
    other["id"] = "locus-b"
    other["start"] = 2001
    other["end"] = 3000
    value["locus_models"].append(other)
    value["correspondence_claims"].append(
        {
            "id": "invalid-haplotype-claim",
            "left_id": "locus-a",
            "right_id": "locus-b",
            "relation": "HAPLOTYPE_OF",
            "declared_state": "SUPPORTED",
            "observation_ids": [],
            "receipt_ids": [],
        }
    )
    with pytest.raises(ValidationError, match="CORRESPONDENCE_SCOPE_OR_TYPE_INVALID"):
        Study.from_dict(value)


def test_transcript_sequence_cannot_claim_haplotype_of_stock() -> None:
    value = study_document()
    value["observations"].append(
        {
            "id": "invalid-phase",
            "kind": "PHASE",
            "subject_id": "transcript-a",
            "object_id": "stock-a",
            "result_state": "POSITIVE",
            "evidence_sha256": "a" * 64,
            "receipt_ids": [],
        }
    )
    value["correspondence_claims"].append(
        {
            "id": "invalid-transcript-haplotype",
            "left_id": "transcript-a",
            "right_id": "stock-a",
            "relation": "HAPLOTYPE_OF",
            "declared_state": "EXACT",
            "observation_ids": ["invalid-phase"],
            "receipt_ids": [],
        }
    )
    with pytest.raises(ValidationError, match="CORRESPONDENCE_SCOPE_OR_TYPE_INVALID"):
        Study.from_dict(value)


def test_claim_observation_must_match_relation_endpoints_and_kind() -> None:
    value = study_document()
    value["observations"].append(
        {
            "id": "copy-observation",
            "kind": "COPY_NUMBER",
            "subject_id": "stock-a",
            "object_id": "assembly-a",
            "result_state": "POSITIVE",
            "evidence_sha256": "c" * 64,
            "receipt_ids": [],
        }
    )
    value["correspondence_claims"].append(
        {
            "id": "bad-transcript-support",
            "left_id": "model-a",
            "right_id": "locus-a",
            "relation": "TRANSCRIPT_OF",
            "declared_state": "SUPPORTED",
            "observation_ids": ["copy-observation"],
            "receipt_ids": [],
        }
    )
    with pytest.raises(ValidationError, match="CORRESPONDENCE_OBSERVATION_SCOPE_INVALID"):
        Study.from_dict(value)


@pytest.mark.parametrize(
    ("result_state", "expected_code"),
    [
        ("NEGATIVE", "NEGATIVE_NOT_ABSENCE"),
        ("NO_RESULT", "NO_RESULT"),
        ("NOT_ASSESSED", "NOT_ASSESSED"),
        ("FAILED", "RETRIEVAL_FAILED"),
        ("AMBIGUOUS", "CORRESPONDENCE_AMBIGUOUS"),
    ],
)
def test_nonpositive_claim_evidence_never_preserves_supported_state(
    result_state: str, expected_code: str
) -> None:
    value = study_document()
    value["observations"].append(
        {
            "id": "crosswalk-observation",
            "kind": "MODEL_CROSSWALK",
            "subject_id": "model-a",
            "object_id": "locus-a",
            "result_state": result_state,
            "evidence_sha256": "d" * 64,
            "receipt_ids": [],
        }
    )
    value["correspondence_claims"].append(
        {
            "id": "transcript-support",
            "left_id": "model-a",
            "right_id": "locus-a",
            "relation": "TRANSCRIPT_OF",
            "declared_state": "SUPPORTED",
            "observation_ids": ["crosswalk-observation"],
            "receipt_ids": [],
        }
    )
    result = compile_study(value).reconciliation
    row = result["correspondence_claims"][0]
    assert row["technical_state"] == "UNRESOLVED"
    assert row["admissible_state"] == "UNRESOLVED"
    assert expected_code in row["conflict_codes"]
    assert "CORRESPONDENCE_CONFLICT" not in result["hold_reasons"]


def test_locus_contig_must_match_referenced_genomic_record() -> None:
    value = study_document()
    value["locus_models"][0]["contig"] = "different-record"
    with pytest.raises(ValidationError, match="LOCUS_RECORD_SCOPE_MISMATCH"):
        Study.from_dict(value)


def test_direct_integer_boundary_is_typed_and_bounded() -> None:
    value = study_document()
    value["locus_models"][0]["end"] = 10**5000
    with pytest.raises(ValidationError, match="must be an integer"):
        Study.from_dict(value)


def test_input_order_is_canonical() -> None:
    value = study_document()
    first = compile_study(value)
    shuffled = copy.deepcopy(value)
    for key in (
        "receipts",
        "materials",
        "assemblies",
        "sequence_objects",
        "locus_models",
        "transcript_models",
        "observations",
        "correspondence_claims",
    ):
        shuffled[key] = list(reversed(shuffled[key]))
    second = compile_study(shuffled)
    assert first.study_lock["input_sha256"] == second.study_lock["input_sha256"]
    assert json.loads(canonical_json_bytes(first.reconciliation)) == json.loads(
        canonical_json_bytes(second.reconciliation)
    )
