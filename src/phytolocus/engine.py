"""Deterministic stock-aware locus reconciliation compiler."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .models import (
    CompletenessState,
    CorrespondenceRelation,
    CorrespondenceState,
    MaterialKind,
    ModelError,
    ReceiptKind,
    ResultState,
    StudySpec,
    receipt_subject_sha256,
)

TOOL_VERSION = "0.1.1"
TARGET_SELECTION = {"allowed": False, "status": "PROHIBITED"}


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return (
            json.dumps(
                value,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ValueError("value cannot be serialized as canonical JSON") from exc


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def subject_document(value: Any) -> dict[str, Any]:
    """Return the exact receipt subject, excluding the circular receipt list."""

    if hasattr(value, "subject_dict"):
        return value.subject_dict()
    document = value.to_dict()
    document.pop("receipt_ids", None)
    return document


def subject_sha256(value: Any) -> str:
    try:
        return receipt_subject_sha256(value)
    except (AttributeError, TypeError):
        return sha256_json(subject_document(value))


@dataclass(frozen=True)
class CompiledStudy:
    study_lock: dict[str, Any]
    reconciliation: dict[str, Any]
    pec_candidate_evidence: dict[str, Any]
    phytoresolve_proposed_events: dict[str, Any]

    def documents(self) -> dict[str, dict[str, Any]]:
        return {
            "pec_candidate_evidence.json": self.pec_candidate_evidence,
            "phytoresolve_proposed_events.json": self.phytoresolve_proposed_events,
            "reconciliation.json": self.reconciliation,
            "study_lock.json": self.study_lock,
        }


class _UnionFind:
    def __init__(self, values: set[str]) -> None:
        self.parent = {value: value for value in values}

    def find(self, value: str) -> str:
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = self.parent[value]
        return value

    def union(self, left: str, right: str) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root == right_root:
            return
        low, high = sorted((left_root, right_root))
        self.parent[high] = low

    def components(self) -> list[list[str]]:
        groups: dict[str, list[str]] = defaultdict(list)
        for value in sorted(self.parent):
            groups[self.find(value)].append(value)
        return sorted((sorted(values) for values in groups.values()), key=lambda x: x)


def _entity_maps(study: StudySpec) -> tuple[dict[str, Any], dict[str, str]]:
    entities: dict[str, Any] = {}
    kinds: dict[str, str] = {}
    for label, items in (
        ("MATERIAL", study.materials),
        ("ASSEMBLY", study.assemblies),
        ("SEQUENCE", study.sequence_objects),
        ("LOCUS", study.locus_models),
        ("TRANSCRIPT_MODEL", study.transcript_models),
        ("OBSERVATION", study.observations),
    ):
        for item in items:
            entities[item.id] = item
            kinds[item.id] = label
    return entities, kinds


def _receipt_analysis(study: StudySpec) -> tuple[list[dict[str, Any]], set[str]]:
    receipt_by_id = {item.id: item for item in study.receipts}
    results: list[dict[str, Any]] = []
    mismatched: set[str] = set()
    expected_kind: list[tuple[ReceiptKind, Any]] = []
    expected_kind.extend((ReceiptKind.MATERIAL, item) for item in study.materials)
    expected_kind.extend((ReceiptKind.ASSEMBLY, item) for item in study.assemblies)
    expected_kind.extend((ReceiptKind.SEQUENCE, item) for item in study.sequence_objects)
    expected_kind.extend((ReceiptKind.ANNOTATION, item) for item in study.locus_models)
    expected_kind.extend((ReceiptKind.ANNOTATION, item) for item in study.transcript_models)
    expected_kind.extend((ReceiptKind.OBSERVATION, item) for item in study.observations)
    expected_kind.extend((ReceiptKind.CORRESPONDENCE, item) for item in study.correspondence_claims)
    for kind, item in sorted(expected_kind, key=lambda pair: pair[1].id):
        expected_subject = subject_sha256(item)
        for receipt_id in sorted(item.receipt_ids):
            receipt = receipt_by_id[receipt_id]
            subject_matches = receipt.subject_sha256 == expected_subject
            kind_matches = receipt.kind in {kind, ReceiptKind.SOURCE}
            if not subject_matches or not kind_matches:
                mismatched.add(receipt_id)
            results.append(
                {
                    "artifact_sha256": receipt.artifact_sha256,
                    "external_authority_established": False,
                    "issuer": receipt.issuer,
                    "kind_matches": kind_matches,
                    "object_id": item.id,
                    "provenance": (
                        None if receipt.provenance is None else receipt.provenance.to_dict()
                    ),
                    "provenance_envelope_bound": receipt.provenance is not None,
                    "receipt_id": receipt_id,
                    "subject_matches": subject_matches,
                    "technical_verification_state": receipt.verification_state.value,
                }
            )
    return results, mismatched


def _material_component_map(study: StudySpec) -> dict[str, str]:
    return StudySpec._material_components({item.id: item for item in study.materials})


def _material_analysis(study: StudySpec) -> dict[str, Any]:
    component_map = _material_component_map(study)
    component_members: dict[str, list[str]] = defaultdict(list)
    component_labels: dict[str, set[str]] = defaultdict(set)
    component_declared_distinct: dict[str, list[str]] = defaultdict(list)
    unresolved: list[str] = []
    for material in study.materials:
        component = component_map[material.id]
        component_members[component].append(material.id)
        if material.biological_origin_id is None:
            unresolved.append(material.id)
        else:
            component_labels[component].add(material.biological_origin_id)
        if material.independence_state.value == "DISTINCT_DECLARED":
            component_declared_distinct[component].append(material.id)
    groups = [
        {
            "biological_origin_id": next(iter(sorted(component_labels[root])), None),
            "component_id": f"material-component-{index:04d}",
            "declared_distinct_material_ids": sorted(component_declared_distinct[root]),
            "material_ids": sorted(values),
            "origin_resolution_state": (
                "DISTINCT_DECLARED_COMPONENT"
                if component_declared_distinct[root]
                else "UNRESOLVED_COMPONENT"
            ),
            "technical_independent_origin_count": (1 if component_declared_distinct[root] else 0),
        }
        for index, (root, values) in enumerate(sorted(component_members.items()), 1)
    ]
    declared_distinct = sorted(
        item.id for item in study.materials if item.independence_state.value == "DISTINCT_DECLARED"
    )
    return {
        "admissible_independent_biological_unit_count": 0,
        "declared_distinct_material_ids": declared_distinct,
        "external_material_authority_established": False,
        "origin_groups": groups,
        "technical_independent_origin_count": sum(
            bool(component_declared_distinct[root]) for root in component_members
        ),
        "unresolved_origin_component_count": sum(
            not component_declared_distinct[root] for root in component_members
        ),
        "unresolved_origin_material_ids": sorted(unresolved),
    }


def _sequence_analysis(study: StudySpec) -> tuple[list[dict[str, Any]], set[str]]:
    by_digest: dict[str, list[Any]] = defaultdict(list)
    for item in study.sequence_objects:
        by_digest[item.sequence_sha256].append(item)
    conflicts: set[str] = set()
    classes = []
    for digest, values in sorted(by_digest.items()):
        alphabets = sorted({item.alphabet.value for item in values})
        lengths = sorted({item.length for item in values})
        if len(alphabets) > 1 or len(lengths) > 1:
            conflicts.update(item.id for item in values)
        classes.append(
            {
                "alphabet_conflict": len(alphabets) > 1,
                "alphabets": alphabets,
                "length_conflict": len(lengths) > 1,
                "lengths": lengths,
                "sequence_object_ids": sorted(item.id for item in values),
                "sequence_sha256": digest,
            }
        )
    return classes, conflicts


def _coordinate_components(
    study: StudySpec,
) -> tuple[list[dict[str, Any]], set[str], set[str]]:
    """Return locus-representation components that cannot inflate copy count.

    Two records on the same assembly interval are representations regardless of
    strand labels.  Across assemblies, records are joined only when they derive
    from the same explicit material-origin component, use the same interval,
    and bind the same complete tuple of genomic sequence identities.  This is
    deliberately conservative: it withholds the component from downstream
    target lists but does not assert that it is one biological copy.
    """

    loci = {item.id: item for item in study.locus_models}
    sequence_by_id = {item.id: item for item in study.sequence_objects}
    assembly_by_id = {item.id: item for item in study.assemblies}
    material_components = _material_component_map(study)
    parent = {item.id: item.id for item in study.locus_models}
    bases: dict[tuple[str, str], set[str]] = defaultdict(set)

    def find(item_id: str) -> str:
        while parent[item_id] != item_id:
            parent[item_id] = parent[parent[item_id]]
            item_id = parent[item_id]
        return item_id

    def union(left: str, right: str, basis: str) -> None:
        left_root, right_root = find(left), find(right)
        pair = tuple(sorted((left, right)))
        bases[pair].add(basis)
        if left_root == right_root:
            return
        low, high = sorted((left_root, right_root))
        parent[high] = low

    by_interval: dict[tuple[str, str, int, int], list[str]] = defaultdict(list)
    for locus in study.locus_models:
        by_interval[(locus.assembly_id, locus.contig, locus.start, locus.end)].append(locus.id)
    interval_sequence_conflicts: set[str] = set()
    for values in by_interval.values():
        representative = min(values)
        for item_id in sorted(values):
            if item_id != representative:
                union(representative, item_id, "SAME_ASSEMBLY_INTERVAL")
        signatures = {
            tuple(
                sorted(
                    {
                        (
                            sequence_by_id[sequence_id].sequence_sha256,
                            sequence_by_id[sequence_id].length,
                            sequence_by_id[sequence_id].alphabet.value,
                        )
                        for sequence_id in loci[locus_id].sequence_object_ids
                        if sequence_by_id[sequence_id].length
                        == loci[locus_id].end - loci[locus_id].start + 1
                    }
                )
            )
            for locus_id in values
        }
        if all(signatures) and len(signatures) > 1:
            interval_sequence_conflicts.update(values)

    by_origin_locus_sequence: dict[tuple[str, tuple[tuple[str, int, str], ...]], list[str]] = (
        defaultdict(list)
    )
    for locus in study.locus_models:
        material_id = assembly_by_id[locus.assembly_id].material_id
        span = locus.end - locus.start + 1
        if any(sequence_by_id[item].length != span for item in locus.sequence_object_ids):
            continue
        sequence_signature = tuple(
            sorted(
                {
                    (
                        sequence_by_id[sequence_id].sequence_sha256,
                        sequence_by_id[sequence_id].length,
                        sequence_by_id[sequence_id].alphabet.value,
                    )
                    for sequence_id in locus.sequence_object_ids
                }
            )
        )
        key = (material_components[material_id], sequence_signature)
        by_origin_locus_sequence[key].append(locus.id)
    for values in by_origin_locus_sequence.values():
        representative = min(values)
        for item_id in sorted(values):
            if item_id != representative:
                union(representative, item_id, "SAME_ORIGIN_EXACT_LOCUS_SEQUENCE")

    component_members: dict[str, list[str]] = defaultdict(list)
    for locus_id in sorted(loci):
        component_members[find(locus_id)].append(locus_id)
    component_bases_by_root: dict[str, set[str]] = defaultdict(set)
    for pair, pair_bases in bases.items():
        root = find(pair[0])
        if root != find(pair[1]):
            raise ModelError("coordinate representation basis escaped its component")
        component_bases_by_root[root].update(pair_bases)
    components: list[dict[str, Any]] = []
    collapsed_representations: set[str] = set()
    for root, values in sorted(component_members.items(), key=lambda item: item[1]):
        if len(values) < 2:
            continue
        collapsed_representations.update(values)
        value_set = set(values)
        component_bases = sorted(component_bases_by_root[root])
        component_loci = [loci[item] for item in values]
        components.append(
            {
                "assembly_ids": sorted({item.assembly_id for item in component_loci}),
                "component_basis": component_bases,
                "contig_records": sorted({item.contig for item in component_loci}),
                "end_values": sorted({item.end for item in component_loci}),
                "locus_model_ids": values,
                "representation_count": len(values),
                "same_interval_not_copy_count": True,
                "sequence_content_conflict": bool(value_set & interval_sequence_conflicts),
                "start_values": sorted({item.start for item in component_loci}),
                "strand_set": sorted({item.strand.value for item in component_loci}),
            }
        )
    return components, collapsed_representations, interval_sequence_conflicts


def _relation_type_valid(relation: CorrespondenceRelation, left: str, right: str) -> bool:
    if relation is CorrespondenceRelation.SAME_SEQUENCE:
        return left == right == "SEQUENCE"
    if relation in {CorrespondenceRelation.SAME_LOCUS, CorrespondenceRelation.ALLELE_OF}:
        return left == right == "LOCUS"
    if relation is CorrespondenceRelation.TRANSCRIPT_OF:
        return left == "TRANSCRIPT_MODEL" and right == "LOCUS"
    if relation is CorrespondenceRelation.PROTEIN_OF:
        return left == "SEQUENCE" and right == "TRANSCRIPT_MODEL"
    if relation is CorrespondenceRelation.SUPERSEDES:
        return left == right
    if relation is CorrespondenceRelation.HAPLOTYPE_OF:
        return left in {"LOCUS", "SEQUENCE", "MATERIAL"} and right == "MATERIAL"
    if relation is CorrespondenceRelation.REPRESENTS:
        return left == right
    return False


def _claim_analysis(study: StudySpec) -> tuple[list[dict[str, Any]], set[str]]:
    entities, kinds = _entity_maps(study)
    observations = {item.id: item for item in study.observations}
    conflicts: set[str] = set()
    results: list[dict[str, Any]] = []
    for claim in sorted(study.correspondence_claims, key=lambda item: item.id):
        type_valid = _relation_type_valid(
            claim.relation, kinds[claim.left_id], kinds[claim.right_id]
        )
        reasons: list[str] = []
        if not type_valid:
            reasons.append("CORRESPONDENCE_TYPE_INVALID")
        if claim.relation is CorrespondenceRelation.SAME_SEQUENCE:
            left, right = entities[claim.left_id], entities[claim.right_id]
            if left.sequence_sha256 != right.sequence_sha256:
                reasons.append("SEQUENCE_HASH_CONFLICT")
            if left.alphabet is not right.alphabet or left.length != right.length:
                reasons.append("SEQUENCE_CONTEXT_CONFLICT")
        observed_states = sorted(
            {observations[item].result_state.value for item in claim.observation_ids}
        )
        if ResultState.FAILED.value in observed_states:
            reasons.append("RETRIEVAL_FAILED")
        if ResultState.AMBIGUOUS.value in observed_states:
            reasons.append("CORRESPONDENCE_AMBIGUOUS")
        if ResultState.NEGATIVE.value in observed_states:
            reasons.append("NEGATIVE_NOT_ABSENCE")
        if ResultState.NO_RESULT.value in observed_states:
            reasons.append("NO_RESULT")
        if ResultState.NOT_ASSESSED.value in observed_states:
            reasons.append("NOT_ASSESSED")
        if claim.declared_state is CorrespondenceState.CONFLICT:
            reasons.append("CORRESPONDENCE_CONFLICT")
        actual_conflict = any(
            reason.endswith("CONFLICT") or reason == "CORRESPONDENCE_TYPE_INVALID"
            for reason in reasons
        )
        if actual_conflict:
            conflicts.add(claim.id)
        if actual_conflict:
            technical_state = "CONFLICT"
            admissible_state = "CONFLICT"
        elif observed_states and set(observed_states) != {ResultState.POSITIVE.value}:
            technical_state = "UNRESOLVED"
            admissible_state = "UNRESOLVED"
        else:
            technical_state = claim.declared_state.value
            admissible_state = "UNRESOLVED"
        results.append(
            {
                "admissible_state": admissible_state,
                "claim_id": claim.id,
                "conflict_codes": sorted(set(reasons)),
                "external_correspondence_authority_established": False,
                "left_id": claim.left_id,
                "observation_states": observed_states,
                "relation": claim.relation.value,
                "right_id": claim.right_id,
                "technical_state": technical_state,
                "type_valid": type_valid,
            }
        )
    return results, conflicts


def _supersession_analysis(
    study: StudySpec, claim_rows: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], bool]:
    active_claim_ids = {
        row["claim_id"]
        for row in claim_rows
        if row["relation"] == CorrespondenceRelation.SUPERSEDES.value
        and row["technical_state"] in {"EXACT", "SUPPORTED"}
    }
    edges = [
        (item.left_id, item.right_id)
        for item in study.correspondence_claims
        if item.relation is CorrespondenceRelation.SUPERSEDES and item.id in active_claim_ids
    ]
    outgoing: dict[str, list[str]] = defaultdict(list)
    incoming: dict[str, list[str]] = defaultdict(list)
    for newer, older in edges:
        outgoing[newer].append(older)
        incoming[older].append(newer)
    forks = {
        older: sorted(set(newer_ids))
        for older, newer_ids in incoming.items()
        if len(set(newer_ids)) > 1
    }
    graph = {key: sorted(set(values)) for key, values in outgoing.items()}
    cycle = False
    state: dict[str, int] = {}
    for start in sorted(graph):
        if state.get(start) == 2:
            continue
        stack: list[tuple[str, bool]] = [(start, False)]
        while stack:
            node, leaving = stack.pop()
            if leaving:
                state[node] = 2
                continue
            if state.get(node) == 1:
                cycle = True
                break
            if state.get(node) == 2:
                continue
            state[node] = 1
            stack.append((node, True))
            stack.extend((child, False) for child in reversed(graph.get(node, [])))
        if cycle:
            break
    chains = [
        {"newer_object_id": left, "older_object_id": right} for left, right in sorted(set(edges))
    ]
    for older, newer_ids in sorted(forks.items()):
        chains.append(
            {
                "competing_newer_object_ids": newer_ids,
                "forked_older_object_id": older,
            }
        )
    return chains, cycle or bool(forks)


def _stock_matrix(study: StudySpec) -> list[dict[str, Any]]:
    materials = {item.id: item for item in study.materials}
    assemblies = {item.id: item for item in study.assemblies}
    stock_ids = {item.id for item in study.materials if item.kind is MaterialKind.STOCK}
    component_map = _material_component_map(study)
    stock_components = {component_map[item] for item in stock_ids}
    rows = []
    for locus in sorted(study.locus_models, key=lambda item: item.id):
        assembly = assemblies[locus.assembly_id]
        material = materials[assembly.material_id]
        is_stock = material.id in stock_ids
        in_stock_component = component_map[material.id] in stock_components
        rows.append(
            {
                "admissible_material_identity": "UNRESOLVED",
                "assembly_id": assembly.id,
                "assembly_release": assembly.release,
                "external_stock_authority_established": False,
                "locus_model_id": locus.id,
                "material_id": material.id,
                "material_kind": material.kind.value,
                "stock_declared": is_stock,
                "stock_origin_component_declared": in_stock_component,
                "technical_phasing_state": assembly.phasing_state.value,
            }
        )
    return rows


def _hold_reasons(
    study: StudySpec,
    receipt_mismatch: set[str],
    sequence_conflicts: set[str],
    collapsed_representations: set[str],
    claim_conflicts: set[str],
    supersession_conflict: bool,
) -> list[str]:
    holds = {"EXTERNAL_RECEIPT_AUTHORITY_NOT_ESTABLISHED"}
    if study.bound_memory_head_sha256 is None:
        holds.add("BOUND_MEMORY_HEAD_MISSING")
    stocks = {item.id for item in study.materials if item.kind is MaterialKind.STOCK}
    if not stocks:
        holds.add("MATERIAL_IDENTITY_MISSING")
    component_map = _material_component_map(study)
    stock_components = {component_map[item] for item in stocks}
    components_with_declared_origin = {
        component_map[item.id] for item in study.materials if item.biological_origin_id is not None
    }
    if stock_components - components_with_declared_origin:
        holds.add("MATERIAL_IDENTITY_MISSING")
    stock_assemblies = [
        item for item in study.assemblies if component_map[item.material_id] in stock_components
    ]
    if not stock_assemblies:
        holds.add("STOCK_EVIDENCE_MISSING")
    # The v0.1 core validates caller assertions and byte bindings but has no
    # external authority adapter. A caller-declared PHASED/complete record can
    # therefore be a useful technical diagnostic, never an admissible closure.
    holds.add("HAPLOTYPE_UNRESOLVED")
    if any(
        item.completeness_state is not CompletenessState.DECLARED_COMPLETE
        for item in stock_assemblies
    ):
        holds.add("ASSEMBLY_COMPLETENESS_UNRESOLVED")
        holds.add("COPY_UNRESOLVED")
    holds.add("COPY_UNRESOLVED")
    holds.add("ISOFORM_UNRESOLVED")
    locus_by_id = {item.id: item for item in study.locus_models}
    sequence_by_id = {item.id: item for item in study.sequence_objects}
    if any(
        item.model_version != locus_by_id[item.locus_model_id].model_version
        or any(
            sequence_by_id[sequence_id].assembly_id is not None
            and sequence_by_id[sequence_id].assembly_id
            != locus_by_id[item.locus_model_id].assembly_id
            for sequence_id in (
                item.transcript_sequence_id,
                *(() if item.cds_sequence_id is None else (item.cds_sequence_id,)),
                *(() if item.protein_sequence_id is None else (item.protein_sequence_id,)),
            )
        )
        for item in study.transcript_models
    ):
        holds.add("TRANSCRIPT_SCOPE_CROSSWALK_EXTERNAL_AUTHORITY_UNRESOLVED")
    if receipt_mismatch:
        holds.add("RECEIPT_SUBJECT_MISMATCH")
    if sequence_conflicts:
        holds.add("SEQUENCE_HASH_CONFLICT")
    if collapsed_representations:
        holds.add("COPY_COLLAPSED")
    if claim_conflicts:
        holds.add("CORRESPONDENCE_CONFLICT")
    if supersession_conflict:
        holds.add("MODEL_VERSION_CONFLICT")
    states = {item.result_state for item in study.observations}
    if ResultState.NEGATIVE in states:
        holds.add("NEGATIVE_NOT_ABSENCE")
    if ResultState.NO_RESULT in states:
        holds.add("NO_RESULT")
    if ResultState.NOT_ASSESSED in states:
        holds.add("NOT_ASSESSED")
    if ResultState.FAILED in states:
        holds.add("RETRIEVAL_FAILED")
    if ResultState.AMBIGUOUS in states:
        holds.add("PARALOG_AMBIGUOUS")
    return sorted(holds)


def _proposed_event_id(event_type: str, payload: Mapping[str, Any]) -> str:
    return f"ple_{sha256_json({'event_type': event_type, 'payload': dict(payload)})[:24]}"


def _phytoresolve_proposal(
    study: StudySpec, study_lock: dict[str, Any], reconciliation: dict[str, Any]
) -> dict[str, Any]:
    head = study.bound_memory_head_sha256
    status = "PROPOSAL_ONLY" if head is not None else "BLOCKED_UNBOUND_MEMORY_HEAD"
    events: list[dict[str, Any]] = []
    if head is not None:
        payload = {
            "bound_memory_head_sha256": head,
            "hold_reasons": reconciliation["hold_reasons"],
            "producer_status": reconciliation["producer_status"],
            "scientific_gate_closure": False,
            "study_id": study.study_id,
            "study_lock_sha256": sha256_json(study_lock),
            "target_selected": False,
        }
        event_type = "locus_reconciliation_compiled"
        events.append(
            {
                "event_type": event_type,
                "payload": payload,
                "proposed_event_id": _proposed_event_id(event_type, payload),
            }
        )
    return {
        "append_authority": "PHYTORESOLVE_ONLY",
        "bound_memory_head_sha256": head,
        "events": events,
        "producer_wrote_persistent_state": False,
        "proposal_status": status,
        "schema_version": "phytolocus.phytoresolve-proposed-events.v0.1",
        "study_id": study.study_id,
        "target_selection": dict(TARGET_SELECTION),
    }


def _pec_candidate(
    study: StudySpec, lock: dict[str, Any], reconciliation: dict[str, Any]
) -> dict[str, Any]:
    gates = [
        "artifact_manifest",
        "causal_interpretation",
        "contradiction_review",
        "copy_and_haplotype_lock",
        "identity_lock",
        "input_provenance",
        "isoform_lock",
        "reproducibility",
        "rights_and_licenses",
        "target_selection",
        "wet_lab_readiness",
    ]
    return {
        "candidate_evidence_only": True,
        "claim_ceiling": "descriptive",
        "gate_candidates": [
            {
                "disposition": "HOLD",
                "gate_id": gate,
                "rationale": "PhytoLocus is a non-authoritative evidence producer.",
            }
            for gate in gates
        ],
        "non_public": True,
        "producer_status": reconciliation["producer_status"],
        "schema_version": "phytolocus.pec-candidate-evidence.v0.1",
        "scientific_gate_closure": False,
        "study_id": study.study_id,
        "study_lock_sha256": sha256_json(lock),
        "target_selection": dict(TARGET_SELECTION),
    }


def compile_study(value: StudySpec | Mapping[str, Any]) -> CompiledStudy:
    """Compile a strict study into four deterministic, non-authoritative documents."""

    if isinstance(value, StudySpec):
        try:
            source = value.to_dict()
        except (AttributeError, TypeError, ValueError) as exc:
            raise ModelError(
                "direct StudySpec instance failed canonical revalidation; target selection "
                "and every nested field must retain their validated model types"
            ) from exc
    else:
        source = value
    study = StudySpec.from_dict(source)
    normalized = study.to_dict()
    lock = {
        "input_sha256": sha256_json(normalized),
        "normalized_study": normalized,
        "schema_version": "phytolocus.study-lock.v0.1",
        "study_id": study.study_id,
        "target_selection": dict(TARGET_SELECTION),
        "tool_version": TOOL_VERSION,
    }
    receipt_rows, receipt_mismatch = _receipt_analysis(study)
    sequence_classes, sequence_conflicts = _sequence_analysis(study)
    (
        coordinate_components,
        collapsed_representations,
        interval_sequence_conflicts,
    ) = _coordinate_components(study)
    sequence_conflicts.update(interval_sequence_conflicts)
    claims, claim_conflicts = _claim_analysis(study)
    supersession, supersession_conflict = _supersession_analysis(study, claims)
    holds = _hold_reasons(
        study,
        receipt_mismatch,
        sequence_conflicts,
        collapsed_representations,
        claim_conflicts,
        supersession_conflict,
    )
    material_components = _material_component_map(study)
    stock_components = {
        material_components[item.id] for item in study.materials if item.kind is MaterialKind.STOCK
    }
    assemblies = {item.id: item for item in study.assemblies}
    independently_addressable_locus_ids = sorted(
        item.id
        for item in study.locus_models
        if item.id not in collapsed_representations
        and material_components[assemblies[item.assembly_id].material_id] in stock_components
    )
    non_stock_locus_ids = sorted(
        item.id
        for item in study.locus_models
        if material_components[assemblies[item.assembly_id].material_id] not in stock_components
    )
    ambiguous_representation_groups = [item["locus_model_ids"] for item in coordinate_components]
    reconciliation = {
        "admissibility": {
            "all_material_identities": "UNRESOLVED",
            "all_receipt_authority": "NOT_ESTABLISHED",
            "copy_count_state": "UNRESOLVED",
            "function_or_causality_assessed": False,
            "guide_release_allowed": False,
            "target_selection_allowed": False,
        },
        "coordinate_representation_components": coordinate_components,
        "correspondence_claims": claims,
        "downstream_handoffs": {
            "phytoparalog": {
                "ambiguous_representation_groups": ambiguous_representation_groups,
                "competitive_background_state": "INCOMPLETE_UNLESS_EXTERNALLY_ADJUDICATED",
                "locus_model_ids": independently_addressable_locus_ids,
                "withheld_non_stock_locus_ids": non_stock_locus_ids,
                "target_selection": dict(TARGET_SELECTION),
            },
            "phytoperturb": {
                "ambiguous_representation_groups": ambiguous_representation_groups,
                "candidate_target_ids": independently_addressable_locus_ids,
                "denominator_state": "UNFROZEN_ACTUAL_STOCK_IDENTITY_REQUIRED",
                "target_selection": dict(TARGET_SELECTION),
                "withheld_non_stock_locus_ids": non_stock_locus_ids,
            },
        },
        "hold_reasons": holds,
        "material_origin_analysis": _material_analysis(study),
        "producer_status": "HOLD" if holds else "COMPILED_FOR_INDEPENDENT_REVIEW",
        "receipt_bindings": receipt_rows,
        "schema_version": "phytolocus.reconciliation.v0.1",
        "sequence_digest_equivalence_classes": sequence_classes,
        "stock_locus_candidate_matrix": _stock_matrix(study),
        "study_id": study.study_id,
        "supersession_edges": supersession,
        "target_selection": dict(TARGET_SELECTION),
        "tool_version": TOOL_VERSION,
    }
    return CompiledStudy(
        lock,
        reconciliation,
        _pec_candidate(study, lock, reconciliation),
        _phytoresolve_proposal(study, lock, reconciliation),
    )


__all__ = [
    "TOOL_VERSION",
    "CompiledStudy",
    "canonical_json_bytes",
    "compile_study",
    "sha256_bytes",
    "sha256_json",
    "subject_document",
    "subject_sha256",
]
