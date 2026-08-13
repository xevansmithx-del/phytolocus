"""Deterministic, directory-descriptor-bound PhytoLocus evidence capsules."""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .engine import (
    TOOL_VERSION,
    canonical_json_bytes,
    compile_study,
    sha256_bytes,
    sha256_json,
)
from .io import MAX_JSON_BYTES, parse_json_bytes, validate_path_argument
from .models import ModelError, StudySpec

DOCUMENT_SCHEMAS = {
    "pec_candidate_evidence.json": "phytolocus.pec-candidate-evidence.v0.1",
    "phytoresolve_proposed_events.json": "phytolocus.phytoresolve-proposed-events.v0.1",
    "reconciliation.json": "phytolocus.reconciliation.v0.1",
    "study_lock.json": "phytolocus.study-lock.v0.1",
}
CAPSULE_FILES = frozenset((*DOCUMENT_SCHEMAS, "manifest.json"))
TARGET_SELECTION = {"allowed": False, "status": "PROHIBITED"}
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_OPEN_DIR = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
_OPEN_MEMBER = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NONBLOCK", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
_STABLE_FIELDS = (
    "st_dev",
    "st_ino",
    "st_size",
    "st_mtime_ns",
    "st_ctime_ns",
    "st_nlink",
)
MemberSignature = tuple[int, ...]
MemberSnapshot = tuple[MemberSignature, str]
_HAS_DESCRIPTOR_SUPPORT = {
    os.mkdir,
    os.open,
    os.stat,
} <= os.supports_dir_fd and os.listdir in os.supports_fd


def _require_posix_descriptor_support() -> None:
    if os.name != "posix" or not _HAS_DESCRIPTOR_SUPPORT:
        raise ModelError("capsule filesystem operations require POSIX descriptor-relative support")


def _open_directory(path: Path, *, label: str) -> int:
    try:
        before = path.lstat()
    except OSError as exc:
        raise ModelError(f"{label} cannot be inspected") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
        raise ModelError(f"{label} must be a real directory")
    try:
        descriptor = os.open(path, _OPEN_DIR)
    except OSError as exc:
        raise ModelError(f"{label} cannot be opened safely") from exc
    after = os.fstat(descriptor)
    if not stat.S_ISDIR(after.st_mode) or (before.st_dev, before.st_ino) != (
        after.st_dev,
        after.st_ino,
    ):
        os.close(descriptor)
        raise ModelError(f"{label} identity changed before opening")
    return descriptor


def _list_names(directory_fd: int) -> set[str]:
    try:
        return set(os.listdir(directory_fd))
    except OSError as exc:
        raise ModelError("capsule directory cannot be listed safely") from exc


def _write_exclusive_at(directory_fd: int, name: str, payload: bytes) -> None:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(name, flags, 0o644, dir_fd=directory_fd)
    except OSError as exc:
        raise ModelError("capsule member could not be created exclusively") from exc
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written < 1:
                raise ModelError("capsule member write did not make progress")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _signature(info: os.stat_result) -> MemberSignature:
    return tuple(getattr(info, field) for field in _STABLE_FIELDS)


def _read_member_snapshot(
    directory_fd: int,
    name: str,
    *,
    label: str,
) -> tuple[bytes, MemberSignature]:
    try:
        before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except OSError as exc:
        raise ModelError(f"{label} cannot be inspected safely") from exc
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise ModelError(f"{label} must be a single-link regular file")
    if before.st_size > MAX_JSON_BYTES:
        raise ModelError(f"{label} exceeds the JSON byte limit")
    try:
        descriptor = os.open(name, _OPEN_MEMBER, dir_fd=directory_fd)
    except OSError as exc:
        raise ModelError(f"{label} cannot be opened safely") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise ModelError(f"{label} must be a single-link regular file")
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise ModelError(f"{label} identity changed before reading")
        remaining = opened.st_size
        blocks: list[bytes] = []
        while remaining:
            block = os.read(descriptor, min(remaining, 1024 * 1024))
            if not block:
                raise ModelError(f"{label} changed while reading")
            blocks.append(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise ModelError(f"{label} grew while reading")
        after = os.fstat(descriptor)
        if _signature(opened) != _signature(after):
            raise ModelError(f"{label} changed while reading")
        current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if _signature(current) != _signature(after):
            raise ModelError(f"{label} identity changed while reading")
        return b"".join(blocks), _signature(after)
    finally:
        os.close(descriptor)


def _read_member_once(directory_fd: int, name: str, *, label: str) -> bytes:
    return _read_member_snapshot(directory_fd, name, label=label)[0]


def _read_canonical_snapshot(
    directory_fd: int,
    name: str,
    *,
    label: str,
) -> tuple[dict[str, Any], bytes, MemberSignature]:
    payload, signature = _read_member_snapshot(directory_fd, name, label=label)
    value = parse_json_bytes(payload, label=label)
    if type(value) is not dict:
        raise ModelError(f"{label} must be a JSON object")
    if canonical_json_bytes(value) != payload:
        raise ModelError(f"{label} is not in canonical JSON form")
    return value, payload, signature


def _read_canonical_document(
    directory_fd: int,
    name: str,
    *,
    label: str,
) -> tuple[dict[str, Any], bytes]:
    value, payload, _ = _read_canonical_snapshot(directory_fd, name, label=label)
    return value, payload


def _capture_expected_members(
    directory_fd: int,
    expected_payloads: Mapping[str, bytes],
) -> dict[str, MemberSnapshot]:
    if set(expected_payloads) != CAPSULE_FILES:
        raise ModelError("internal capsule payload set is incomplete")
    snapshots: dict[str, MemberSnapshot] = {}
    for name in sorted(CAPSULE_FILES):
        payload, signature = _read_member_snapshot(
            directory_fd,
            name,
            label="capsule member",
        )
        if payload != expected_payloads[name]:
            raise ModelError("capsule member changed after writing")
        snapshots[name] = (signature, sha256_bytes(payload))
    return snapshots


def _verify_members_unchanged(
    directory_fd: int,
    snapshots: Mapping[str, MemberSnapshot],
) -> None:
    if set(snapshots) != CAPSULE_FILES:
        raise ModelError("capsule validation snapshot is incomplete")
    verified: dict[str, MemberSnapshot] = {}
    for name in sorted(CAPSULE_FILES):
        payload, signature = _read_member_snapshot(
            directory_fd,
            name,
            label="capsule member",
        )
        verified[name] = (signature, sha256_bytes(payload))
        if verified[name] != snapshots[name]:
            raise ModelError("capsule member changed during the validation window")
    if _list_names(directory_fd) != CAPSULE_FILES:
        raise ModelError("capsule member set changed during the validation window")
    for name in sorted(CAPSULE_FILES):
        try:
            current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except OSError as exc:
            raise ModelError("capsule member changed during the final stat sweep") from exc
        if _signature(current) != verified[name][0]:
            raise ModelError("capsule member changed during the final stat sweep")


def _manifest(documents: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    files = []
    for name in sorted(documents):
        document = documents[name]
        payload = canonical_json_bytes(document)
        files.append(
            {
                "bytes": len(payload),
                "path": name,
                "schema_version": document["schema_version"],
                "sha256": sha256_bytes(payload),
            }
        )
    return {
        "capsule_digest": sha256_json(files),
        "files": files,
        "schema_version": "phytolocus.manifest.v0.1",
        "target_selection": dict(TARGET_SELECTION),
        "tool_version": TOOL_VERSION,
    }


def compile_capsule(
    value: StudySpec | Mapping[str, Any],
    output_dir: str | os.PathLike[str],
) -> dict[str, Any]:
    """Compile and exclusively create a descriptor-owned five-file capsule."""

    _require_posix_descriptor_support()
    compiled = compile_study(value)
    documents = compiled.documents()
    manifest = _manifest(documents)
    expected_payloads = {
        name: canonical_json_bytes(document) for name, document in documents.items()
    }
    expected_payloads["manifest.json"] = canonical_json_bytes(manifest)
    destination = Path(validate_path_argument(output_dir))
    if not destination.name or destination.name in {".", ".."}:
        raise ModelError("capsule destination name is invalid")
    parent_fd = _open_directory(destination.parent, label="capsule parent directory")
    capsule_fd: int | None = None
    try:
        try:
            os.mkdir(destination.name, 0o755, dir_fd=parent_fd)
        except OSError as exc:
            raise ModelError("capsule destination must not already exist") from exc
        try:
            capsule_fd = os.open(destination.name, _OPEN_DIR, dir_fd=parent_fd)
        except OSError as exc:
            raise ModelError("new capsule directory cannot be opened safely") from exc
        created = os.fstat(capsule_fd)
        linked = os.stat(destination.name, dir_fd=parent_fd, follow_symlinks=False)
        if (created.st_dev, created.st_ino) != (linked.st_dev, linked.st_ino):
            raise ModelError("new capsule directory identity changed before writing")
        for name in sorted(documents):
            _write_exclusive_at(capsule_fd, name, expected_payloads[name])
        _write_exclusive_at(capsule_fd, "manifest.json", expected_payloads["manifest.json"])
        if _list_names(capsule_fd) != CAPSULE_FILES:
            raise ModelError("new capsule member set changed during compilation")
        os.fsync(capsule_fd)
        os.fsync(parent_fd)
        snapshots = _capture_expected_members(capsule_fd, expected_payloads)
        _verify_members_unchanged(capsule_fd, snapshots)
        linked = os.stat(destination.name, dir_fd=parent_fd, follow_symlinks=False)
        if (created.st_dev, created.st_ino) != (linked.st_dev, linked.st_ino):
            raise ModelError("new capsule directory was replaced during compilation")
    finally:
        if capsule_fd is not None:
            os.close(capsule_fd)
        os.close(parent_fd)
    return {
        "capsule_digest": manifest["capsule_digest"],
        "file_count": len(CAPSULE_FILES),
        "hold_reasons": list(compiled.reconciliation["hold_reasons"]),
        "mutable_path_current_at_return_guaranteed": False,
        "producer_status": compiled.reconciliation["producer_status"],
        "snapshot_valid": True,
        "study_id": compiled.reconciliation["study_id"],
        "target_selection": dict(TARGET_SELECTION),
        "validation_subject": "OWNED_BYTE_SNAPSHOT",
    }


def _closed_object(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        raise ModelError(f"{label} violates the closed capsule schema")
    return value


def _closed_array(value: Any, *, label: str) -> list[Any]:
    if type(value) is not list:
        raise ModelError(f"{label} must be an array")
    return value


def _text(value: Any, *, label: str) -> str:
    if type(value) is not str or not value:
        raise ModelError(f"{label} must be a nonempty string")
    return value


def _string_array(value: Any, *, label: str) -> list[str]:
    result = _closed_array(value, label=label)
    if any(type(item) is not str or not item for item in result) or len(result) != len(set(result)):
        raise ModelError(f"{label} must contain unique nonempty strings")
    return result


def _validate_manifest(value: Any) -> dict[str, Any]:
    manifest = _closed_object(
        value,
        {"capsule_digest", "files", "schema_version", "target_selection", "tool_version"},
        "manifest",
    )
    if manifest["schema_version"] != "phytolocus.manifest.v0.1":
        raise ModelError("manifest schema version is unsupported")
    if manifest["tool_version"] != TOOL_VERSION:
        raise ModelError("manifest tool version is unsupported")
    if manifest["target_selection"] != TARGET_SELECTION:
        raise ModelError("manifest target selection contract is invalid")
    digest = manifest["capsule_digest"]
    if type(digest) is not str or _SHA256.fullmatch(digest) is None:
        raise ModelError("manifest capsule digest is invalid")
    files = manifest["files"]
    if type(files) is not list or len(files) != len(DOCUMENT_SCHEMAS):
        raise ModelError("manifest file table is incomplete")
    rows = []
    names = []
    for index, raw_row in enumerate(files):
        row = _closed_object(
            raw_row,
            {"bytes", "path", "schema_version", "sha256"},
            f"manifest.files[{index}]",
        )
        name = row["path"]
        if type(name) is not str or name not in DOCUMENT_SCHEMAS:
            raise ModelError("manifest contains an unsupported member path")
        if row["schema_version"] != DOCUMENT_SCHEMAS[name]:
            raise ModelError("manifest member schema version is inconsistent")
        if type(row["bytes"]) is not int or type(row["bytes"]) is bool or row["bytes"] < 1:
            raise ModelError("manifest member byte count is invalid")
        if type(row["sha256"]) is not str or _SHA256.fullmatch(row["sha256"]) is None:
            raise ModelError("manifest member digest is invalid")
        names.append(name)
        rows.append(dict(row))
    if names != sorted(DOCUMENT_SCHEMAS):
        raise ModelError("manifest member table must be complete, unique, and sorted")
    if sha256_json(rows) != digest:
        raise ModelError("manifest capsule digest does not match its member table")
    return manifest


def _validate_study_lock(value: Mapping[str, Any]) -> None:
    lock = _closed_object(
        value,
        {
            "input_sha256",
            "normalized_study",
            "schema_version",
            "study_id",
            "target_selection",
            "tool_version",
        },
        "study lock",
    )
    if type(lock["input_sha256"]) is not str or _SHA256.fullmatch(lock["input_sha256"]) is None:
        raise ModelError("study lock input digest is invalid")
    if type(lock["normalized_study"]) is not dict:
        raise ModelError("study lock normalized study must be an object")
    normalized = StudySpec.from_dict(lock["normalized_study"])
    if normalized.to_dict() != lock["normalized_study"]:
        raise ModelError("study lock normalized study is not in canonical model form")
    if normalized.study_id != lock["study_id"]:
        raise ModelError("study lock identity is inconsistent")
    if sha256_json(lock["normalized_study"]) != lock["input_sha256"]:
        raise ModelError("study lock input digest is inconsistent")
    if lock["tool_version"] != TOOL_VERSION:
        raise ModelError("study lock tool version is unsupported")


def _validate_reconciliation(value: Mapping[str, Any]) -> None:
    required = {
        "admissibility",
        "coordinate_representation_components",
        "correspondence_claims",
        "downstream_handoffs",
        "hold_reasons",
        "material_origin_analysis",
        "producer_status",
        "receipt_bindings",
        "schema_version",
        "sequence_digest_equivalence_classes",
        "stock_locus_candidate_matrix",
        "study_id",
        "supersession_edges",
        "target_selection",
        "tool_version",
    }
    reconciliation = _closed_object(value, required, "reconciliation")
    status = reconciliation["producer_status"]
    if status not in {"HOLD", "COMPILED_FOR_INDEPENDENT_REVIEW"}:
        raise ModelError("reconciliation producer status is invalid")
    holds = _string_array(reconciliation["hold_reasons"], label="reconciliation hold reasons")
    if (status == "HOLD") != bool(holds):
        raise ModelError("reconciliation status and hold reasons are inconsistent")
    admissibility = _closed_object(
        reconciliation["admissibility"],
        {
            "all_material_identities",
            "all_receipt_authority",
            "copy_count_state",
            "function_or_causality_assessed",
            "guide_release_allowed",
            "target_selection_allowed",
        },
        "reconciliation admissibility",
    )
    for field in (
        "function_or_causality_assessed",
        "guide_release_allowed",
        "target_selection_allowed",
    ):
        if admissibility.get(field) is not False:
            raise ModelError("reconciliation cannot grant scientific or target authority")
    if reconciliation["tool_version"] != TOOL_VERSION:
        raise ModelError("reconciliation tool version is unsupported")


def _validate_candidate(value: Mapping[str, Any]) -> None:
    candidate = _closed_object(
        value,
        {
            "candidate_evidence_only",
            "claim_ceiling",
            "gate_candidates",
            "non_public",
            "producer_status",
            "schema_version",
            "scientific_gate_closure",
            "study_id",
            "study_lock_sha256",
            "target_selection",
        },
        "PEC candidate evidence",
    )
    if candidate["candidate_evidence_only"] is not True:
        raise ModelError("PEC document must remain candidate evidence only")
    if candidate["claim_ceiling"] != "descriptive":
        raise ModelError("PEC claim ceiling cannot exceed descriptive")
    if candidate["non_public"] is not True or candidate["scientific_gate_closure"] is not False:
        raise ModelError("PEC document cannot close scientific gates or become public")
    if candidate["producer_status"] not in {"HOLD", "COMPILED_FOR_INDEPENDENT_REVIEW"}:
        raise ModelError("PEC producer status is invalid")
    gates = _closed_array(candidate["gate_candidates"], label="PEC gate candidates")
    expected_gates = {
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
    }
    observed_gates = set()
    for index, raw_gate in enumerate(gates):
        gate = _closed_object(
            raw_gate,
            {"disposition", "gate_id", "rationale"},
            f"PEC gate_candidates[{index}]",
        )
        if gate["disposition"] != "HOLD":
            raise ModelError("PEC candidate gate disposition must remain HOLD")
        observed_gates.add(_text(gate["gate_id"], label="PEC gate id"))
        _text(gate["rationale"], label="PEC gate rationale")
    if observed_gates != expected_gates or len(gates) != len(expected_gates):
        raise ModelError("PEC candidate gate set is incomplete or duplicated")
    digest = candidate["study_lock_sha256"]
    if type(digest) is not str or _SHA256.fullmatch(digest) is None:
        raise ModelError("PEC study-lock digest is invalid")


def _validate_proposal(value: Mapping[str, Any]) -> None:
    proposal = _closed_object(
        value,
        {
            "append_authority",
            "bound_memory_head_sha256",
            "events",
            "producer_wrote_persistent_state",
            "proposal_status",
            "schema_version",
            "study_id",
            "target_selection",
        },
        "PhytoResolve proposal",
    )
    if proposal["append_authority"] != "PHYTORESOLVE_ONLY":
        raise ModelError("proposal append authority is invalid")
    if proposal["producer_wrote_persistent_state"] is not False:
        raise ModelError("PhytoLocus proposal cannot claim a persistent write")
    head = proposal["bound_memory_head_sha256"]
    if head is not None and (type(head) is not str or _SHA256.fullmatch(head) is None):
        raise ModelError("proposal memory head is invalid")
    events = _closed_array(proposal["events"], label="proposal events")
    status = proposal["proposal_status"]
    if status == "BLOCKED_UNBOUND_MEMORY_HEAD":
        if head is not None or events:
            raise ModelError("unbound memory proposal must contain no events")
    elif status == "PROPOSAL_ONLY":
        if head is None or not events:
            raise ModelError("bound memory proposal must contain proposed events")
    else:
        raise ModelError("proposal status is invalid")
    seen_ids = set()
    for index, raw_event in enumerate(events):
        event = _closed_object(
            raw_event,
            {"event_type", "payload", "proposed_event_id"},
            f"proposal.events[{index}]",
        )
        if event["event_type"] != "locus_reconciliation_compiled":
            raise ModelError("proposal event type is unsupported")
        payload = _closed_object(
            event["payload"],
            {
                "bound_memory_head_sha256",
                "hold_reasons",
                "producer_status",
                "scientific_gate_closure",
                "study_id",
                "study_lock_sha256",
                "target_selected",
            },
            f"proposal.events[{index}].payload",
        )
        if payload["bound_memory_head_sha256"] != head:
            raise ModelError("proposal event memory head is inconsistent")
        if (
            payload["scientific_gate_closure"] is not False
            or payload["target_selected"] is not False
        ):
            raise ModelError("proposal event cannot close gates or select a target")
        _string_array(payload["hold_reasons"], label="proposal event hold reasons")
        if payload["producer_status"] not in {"HOLD", "COMPILED_FOR_INDEPENDENT_REVIEW"}:
            raise ModelError("proposal event producer status is invalid")
        event_id = _text(event["proposed_event_id"], label="proposal event id")
        expected_id = (
            f"ple_{sha256_json({'event_type': event['event_type'], 'payload': payload})[:24]}"
        )
        if event_id != expected_id:
            raise ModelError("proposal event identifier is inconsistent")
        if event_id in seen_ids:
            raise ModelError("proposal event identifiers must be unique")
        seen_ids.add(event_id)


def _validate_document_links(documents: Mapping[str, Mapping[str, Any]]) -> None:
    study_lock = documents["study_lock.json"]
    reconciliation = documents["reconciliation.json"]
    candidate = documents["pec_candidate_evidence.json"]
    proposal = documents["phytoresolve_proposed_events.json"]
    for name, document in documents.items():
        if document.get("schema_version") != DOCUMENT_SCHEMAS[name]:
            raise ModelError("capsule document schema version is inconsistent")
        if document.get("target_selection") != TARGET_SELECTION:
            raise ModelError("capsule document target selection contract is invalid")
    _validate_study_lock(study_lock)
    _validate_reconciliation(reconciliation)
    _validate_candidate(candidate)
    _validate_proposal(proposal)
    study_ids = {
        document.get("study_id") for document in (study_lock, reconciliation, candidate, proposal)
    }
    if len(study_ids) != 1 or not all(type(value) is str and value for value in study_ids):
        raise ModelError("capsule documents disagree on study identity")
    if candidate["study_lock_sha256"] != sha256_json(study_lock):
        raise ModelError("candidate evidence is not bound to the study lock")
    if candidate["producer_status"] != reconciliation["producer_status"]:
        raise ModelError("candidate evidence disagrees with reconciliation status")
    locked_memory_head = study_lock["normalized_study"]["bound_memory_head_sha256"]
    if proposal["bound_memory_head_sha256"] != locked_memory_head:
        raise ModelError("proposal memory head disagrees with the study lock")
    for event in proposal["events"]:
        payload = event["payload"]
        if payload["study_id"] != reconciliation["study_id"]:
            raise ModelError("proposal event study identity is inconsistent")
        if payload["study_lock_sha256"] != sha256_json(study_lock):
            raise ModelError("proposal event is not bound to the study lock")
        if payload["producer_status"] != reconciliation["producer_status"]:
            raise ModelError("proposal event producer status is inconsistent")
        if payload["hold_reasons"] != reconciliation["hold_reasons"]:
            raise ModelError("proposal event hold reasons are inconsistent")
    expected_documents = compile_study(study_lock["normalized_study"]).documents()
    for name in sorted(DOCUMENT_SCHEMAS):
        if canonical_json_bytes(documents[name]) != canonical_json_bytes(expected_documents[name]):
            raise ModelError("capsule document differs from embedded study recomputation")


def validate_capsule(
    capsule_dir: str | os.PathLike[str],
    *,
    study: StudySpec | Mapping[str, Any] | None = None,
    expected_digest: str | None = None,
) -> dict[str, Any]:
    """Validate one owned byte snapshot, nested authority limits, and optional semantics."""

    _require_posix_descriptor_support()
    if expected_digest is not None and (
        type(expected_digest) is not str or _SHA256.fullmatch(expected_digest) is None
    ):
        raise ModelError("expected digest must be a lowercase SHA-256 string")
    directory_fd = _open_directory(Path(validate_path_argument(capsule_dir)), label="capsule input")
    semantics_trusted = False
    try:
        first_listing = _list_names(directory_fd)
        if first_listing != CAPSULE_FILES:
            raise ModelError("capsule member set is incomplete or contains extras")
        directory_before = os.fstat(directory_fd)
        manifest, manifest_payload, manifest_signature = _read_canonical_snapshot(
            directory_fd,
            "manifest.json",
            label="manifest",
        )
        snapshots: dict[str, MemberSnapshot] = {
            "manifest.json": (manifest_signature, sha256_bytes(manifest_payload))
        }
        manifest = _validate_manifest(manifest)
        if expected_digest is not None and manifest["capsule_digest"] != expected_digest:
            raise ModelError("capsule digest does not match the external anchor")
        rows = {row["path"]: row for row in manifest["files"]}
        documents: dict[str, dict[str, Any]] = {}
        for name in sorted(DOCUMENT_SCHEMAS):
            document, payload, signature = _read_canonical_snapshot(
                directory_fd,
                name,
                label="capsule member",
            )
            snapshots[name] = (signature, sha256_bytes(payload))
            row = rows[name]
            if len(payload) != row["bytes"] or sha256_bytes(payload) != row["sha256"]:
                raise ModelError("capsule member does not match the manifest")
            documents[name] = document
        _validate_document_links(documents)
        if study is not None:
            expected_documents = compile_study(study).documents()
            for name in sorted(DOCUMENT_SCHEMAS):
                if canonical_json_bytes(expected_documents[name]) != canonical_json_bytes(
                    documents[name]
                ):
                    raise ModelError("capsule differs from full study recomputation")
            semantics_trusted = True
        if _list_names(directory_fd) != first_listing:
            raise ModelError("capsule directory changed during validation")
        _verify_members_unchanged(directory_fd, snapshots)
        directory_after = os.fstat(directory_fd)
        if (directory_before.st_dev, directory_before.st_ino) != (
            directory_after.st_dev,
            directory_after.st_ino,
        ):
            raise ModelError("capsule directory identity changed during validation")
    finally:
        os.close(directory_fd)

    if study is not None and expected_digest is not None:
        mode = "STUDY_RECOMPUTED_AND_EXTERNAL_DIGEST_ANCHORED"
        authority = "STUDY_RECOMPUTED_SEMANTICS_AND_EXTERNALLY_ANCHORED_INTEGRITY"
    elif study is not None:
        mode = "STUDY_RECOMPUTED"
        authority = "STUDY_RECOMPUTED_STRUCTURE_AND_SEMANTICS"
    elif expected_digest is not None:
        mode = "EXTERNAL_DIGEST_ANCHORED"
        authority = "EXTERNALLY_ANCHORED_INTEGRITY_ONLY"
    else:
        mode = "INTEGRITY_ONLY"
        authority = "SELF_ASSERTED_INTERNAL_CONSISTENCY_ONLY"
    result: dict[str, Any] = {
        "capsule_digest": manifest["capsule_digest"],
        "capsule_semantics_trusted": semantics_trusted,
        "capsule_valid": True,
        "file_count": len(CAPSULE_FILES),
        "mutable_path_current_at_return_guaranteed": False,
        "snapshot_valid": True,
        "target_selection": dict(TARGET_SELECTION),
        "validation_authority": authority,
        "validation_mode": mode,
        "validation_subject": "OWNED_BYTE_SNAPSHOT",
    }
    if semantics_trusted:
        reconciliation = documents["reconciliation.json"]
        result.update(
            {
                "hold_reasons": list(reconciliation["hold_reasons"]),
                "producer_status": reconciliation["producer_status"],
                "study_id": reconciliation["study_id"],
            }
        )
    return result


__all__ = [
    "CAPSULE_FILES",
    "DOCUMENT_SCHEMAS",
    "compile_capsule",
    "validate_capsule",
]
