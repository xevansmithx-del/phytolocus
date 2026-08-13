from __future__ import annotations

import json
from pathlib import Path

import pytest

import phytolocus.capsule as capsule_module
from phytolocus import ValidationError, compile_capsule, validate_capsule
from phytolocus.capsule import _manifest
from phytolocus.engine import canonical_json_bytes, sha256_json
from phytolocus_tu.fixture import fixture_payload


def _rewrite_manifest(capsule: Path) -> str:
    documents = {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in capsule.glob("*.json")
        if path.name != "manifest.json"
    }
    manifest = _manifest(documents)
    (capsule / "manifest.json").write_bytes(canonical_json_bytes(manifest))
    return manifest["capsule_digest"]


def test_capsule_is_deterministic_and_full_recomputation_is_trusted(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first_report = compile_capsule(fixture_payload(), first)
    second_report = compile_capsule(fixture_payload(), second)
    assert first_report == second_report
    assert first_report["producer_status"] == "HOLD"
    assert first_report["validation_subject"] == "OWNED_BYTE_SNAPSHOT"
    assert first_report["snapshot_valid"] is True
    assert first_report["mutable_path_current_at_return_guaranteed"] is False
    assert first_report["hold_reasons"] == [
        "BOUND_MEMORY_HEAD_MISSING",
        "COPY_UNRESOLVED",
        "EXTERNAL_RECEIPT_AUTHORITY_NOT_ESTABLISHED",
        "HAPLOTYPE_UNRESOLVED",
        "ISOFORM_UNRESOLVED",
    ]
    assert {path.name: path.read_bytes() for path in first.iterdir()} == {
        path.name: path.read_bytes() for path in second.iterdir()
    }

    validation = validate_capsule(first, study=fixture_payload())
    assert validation["capsule_valid"] is True
    assert validation["validation_subject"] == "OWNED_BYTE_SNAPSHOT"
    assert validation["snapshot_valid"] is True
    assert validation["mutable_path_current_at_return_guaranteed"] is False
    assert validation["capsule_semantics_trusted"] is True
    assert validation["validation_mode"] == "STUDY_RECOMPUTED"
    assert validation["producer_status"] == "HOLD"
    assert validation["hold_reasons"] == first_report["hold_reasons"]


def test_external_digest_anchors_bytes_not_semantics(tmp_path: Path) -> None:
    capsule = tmp_path / "capsule"
    report = compile_capsule(fixture_payload(), capsule)
    validation = validate_capsule(capsule, expected_digest=report["capsule_digest"])
    assert validation["capsule_valid"] is True
    assert validation["capsule_semantics_trusted"] is False
    assert validation["validation_mode"] == "EXTERNAL_DIGEST_ANCHORED"
    assert "producer_status" not in validation
    assert "hold_reasons" not in validation


def test_internal_manifest_cannot_replace_embedded_study_recomputation(tmp_path: Path) -> None:
    capsule = tmp_path / "capsule"
    original = compile_capsule(fixture_payload(), capsule)
    reconciliation_path = capsule / "reconciliation.json"
    candidate_path = capsule / "pec_candidate_evidence.json"
    reconciliation = json.loads(reconciliation_path.read_text(encoding="utf-8"))
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    reconciliation["producer_status"] = "COMPILED_FOR_INDEPENDENT_REVIEW"
    reconciliation["hold_reasons"] = []
    candidate["producer_status"] = "COMPILED_FOR_INDEPENDENT_REVIEW"
    reconciliation_path.write_bytes(canonical_json_bytes(reconciliation))
    candidate_path.write_bytes(canonical_json_bytes(candidate))
    _rewrite_manifest(capsule)

    with pytest.raises(ValidationError, match="embedded study recomputation"):
        validate_capsule(capsule)
    with pytest.raises(ValidationError, match="embedded study recomputation"):
        validate_capsule(capsule, study=fixture_payload())
    with pytest.raises(ValidationError, match="external anchor"):
        validate_capsule(capsule, expected_digest=original["capsule_digest"])


def test_member_tamper_and_extra_file_fail_closed(tmp_path: Path) -> None:
    capsule = tmp_path / "capsule"
    compile_capsule(fixture_payload(), capsule)
    reconciliation = capsule / "reconciliation.json"
    reconciliation.write_bytes(reconciliation.read_bytes() + b" ")
    with pytest.raises(ValidationError, match="canonical JSON"):
        validate_capsule(capsule)

    clean = tmp_path / "clean"
    compile_capsule(fixture_payload(), clean)
    (clean / "unexpected.txt").write_text("unexpected", encoding="utf-8")
    with pytest.raises(ValidationError, match="contains extras"):
        validate_capsule(clean)


def test_capsule_destination_is_exclusive(tmp_path: Path) -> None:
    capsule = tmp_path / "capsule"
    capsule.mkdir()
    marker = capsule / "owned-by-caller"
    marker.write_text("preserve", encoding="utf-8")
    with pytest.raises(ValidationError, match="must not already exist"):
        compile_capsule(fixture_payload(), capsule)
    assert marker.read_text(encoding="utf-8") == "preserve"


def test_symlink_member_is_refused(tmp_path: Path) -> None:
    capsule = tmp_path / "capsule"
    compile_capsule(fixture_payload(), capsule)
    member = capsule / "reconciliation.json"
    target = tmp_path / "target.json"
    target.write_bytes(member.read_bytes())
    member.unlink()
    member.symlink_to(target)
    with pytest.raises(ValidationError, match="single-link regular file"):
        validate_capsule(capsule)


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("scientific_gate_closure", True, "cannot close scientific gates"),
        ("non_public", False, "cannot close scientific gates"),
    ],
)
def test_refreshed_external_anchor_cannot_elevate_pec_authority(
    tmp_path: Path,
    field: str,
    value: bool,
    error: str,
) -> None:
    capsule = tmp_path / "capsule"
    compile_capsule(fixture_payload(), capsule)
    candidate_path = capsule / "pec_candidate_evidence.json"
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    candidate[field] = value
    candidate_path.write_bytes(canonical_json_bytes(candidate))
    forged_anchor = _rewrite_manifest(capsule)

    with pytest.raises(ValidationError, match=error):
        validate_capsule(capsule, expected_digest=forged_anchor)


def test_refreshed_external_anchor_cannot_pass_a_pec_gate(tmp_path: Path) -> None:
    capsule = tmp_path / "capsule"
    compile_capsule(fixture_payload(), capsule)
    candidate_path = capsule / "pec_candidate_evidence.json"
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    candidate["gate_candidates"][0]["disposition"] = "PASS"
    candidate_path.write_bytes(canonical_json_bytes(candidate))
    forged_anchor = _rewrite_manifest(capsule)

    with pytest.raises(ValidationError, match="must remain HOLD"):
        validate_capsule(capsule, expected_digest=forged_anchor)


def test_refreshed_external_anchor_cannot_elevate_proposal_authority(tmp_path: Path) -> None:
    study = fixture_payload()
    study["bound_memory_head_sha256"] = "a" * 64
    capsule = tmp_path / "capsule"
    compile_capsule(study, capsule)
    proposal_path = capsule / "phytoresolve_proposed_events.json"
    proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
    event = proposal["events"][0]
    event["payload"]["scientific_gate_closure"] = True
    event["proposed_event_id"] = (
        "ple_" + sha256_json({"event_type": event["event_type"], "payload": event["payload"]})[:24]
    )
    proposal_path.write_bytes(canonical_json_bytes(proposal))
    forged_anchor = _rewrite_manifest(capsule)

    with pytest.raises(ValidationError, match="cannot close gates"):
        validate_capsule(capsule, expected_digest=forged_anchor)


@pytest.mark.parametrize(
    ("section", "index", "field"),
    [
        ("stock_locus_candidate_matrix", 0, "external_stock_authority_established"),
        ("receipt_bindings", 0, "external_authority_established"),
        ("correspondence_claims", 0, "external_correspondence_authority_established"),
    ],
)
def test_refreshed_manifest_cannot_elevate_nested_reconciliation_authority(
    tmp_path: Path,
    section: str,
    index: int,
    field: str,
) -> None:
    capsule = tmp_path / "capsule"
    compile_capsule(fixture_payload(), capsule)
    path = capsule / "reconciliation.json"
    reconciliation = json.loads(path.read_text(encoding="utf-8"))
    reconciliation[section][index][field] = True
    path.write_bytes(canonical_json_bytes(reconciliation))
    forged_anchor = _rewrite_manifest(capsule)

    with pytest.raises(ValidationError, match="embedded study recomputation"):
        validate_capsule(capsule, expected_digest=forged_anchor)


def test_refreshed_manifest_cannot_elevate_nested_target_selection(tmp_path: Path) -> None:
    capsule = tmp_path / "capsule"
    compile_capsule(fixture_payload(), capsule)
    path = capsule / "reconciliation.json"
    reconciliation = json.loads(path.read_text(encoding="utf-8"))
    reconciliation["downstream_handoffs"]["phytoperturb"]["target_selection"] = {
        "allowed": True,
        "status": "SELECTED",
    }
    path.write_bytes(canonical_json_bytes(reconciliation))
    forged_anchor = _rewrite_manifest(capsule)

    with pytest.raises(ValidationError, match="embedded study recomputation"):
        validate_capsule(capsule, expected_digest=forged_anchor)


def test_study_lock_requires_canonical_normalized_arrays(tmp_path: Path) -> None:
    capsule = tmp_path / "capsule"
    compile_capsule(fixture_payload(), capsule)
    path = capsule / "study_lock.json"
    lock = json.loads(path.read_text(encoding="utf-8"))
    lock["normalized_study"]["receipts"].reverse()
    lock["input_sha256"] = sha256_json(lock["normalized_study"])
    path.write_bytes(canonical_json_bytes(lock))
    _rewrite_manifest(capsule)

    with pytest.raises(ValidationError, match="canonical model form"):
        validate_capsule(capsule)


def test_proposal_memory_head_must_match_study_lock(tmp_path: Path) -> None:
    study = fixture_payload()
    study["bound_memory_head_sha256"] = "a" * 64
    capsule = tmp_path / "capsule"
    compile_capsule(study, capsule)
    path = capsule / "phytoresolve_proposed_events.json"
    proposal = json.loads(path.read_text(encoding="utf-8"))
    proposal["bound_memory_head_sha256"] = "b" * 64
    proposal["events"][0]["payload"]["bound_memory_head_sha256"] = "b" * 64
    event = proposal["events"][0]
    event["proposed_event_id"] = (
        "ple_" + sha256_json({"event_type": event["event_type"], "payload": event["payload"]})[:24]
    )
    path.write_bytes(canonical_json_bytes(proposal))
    _rewrite_manifest(capsule)

    with pytest.raises(ValidationError, match="memory head disagrees"):
        validate_capsule(capsule)


def test_capsule_filesystem_api_fails_closed_without_posix_dirfd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(capsule_module.os, "name", "nt")
    with pytest.raises(ValidationError, match="POSIX descriptor-relative"):
        compile_capsule(fixture_payload(), tmp_path / "capsule")


def test_compile_owns_parent_and_capsule_descriptors_during_symlink_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "owned-parent"
    parent.mkdir()
    moved_parent = tmp_path / "moved-parent"
    decoy = tmp_path / "decoy-parent"
    decoy.mkdir()
    original_write = capsule_module._write_exclusive_at
    swapped = False

    def swap_then_write(directory_fd: int, name: str, payload: bytes) -> None:
        nonlocal swapped
        if not swapped:
            parent.rename(moved_parent)
            parent.symlink_to(decoy, target_is_directory=True)
            swapped = True
        original_write(directory_fd, name, payload)

    monkeypatch.setattr(capsule_module, "_write_exclusive_at", swap_then_write)
    compile_capsule(fixture_payload(), parent / "capsule")

    assert not (decoy / "capsule").exists()
    assert validate_capsule(moved_parent / "capsule", study=fixture_payload())["capsule_valid"]


def test_validation_owns_capsule_descriptor_during_symlink_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capsule = tmp_path / "capsule"
    moved_capsule = tmp_path / "moved-capsule"
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    compile_capsule(fixture_payload(), capsule)
    original_read = capsule_module._read_canonical_document
    swapped = False

    def swap_then_read(
        directory_fd: int,
        name: str,
        *,
        label: str,
    ) -> tuple[dict[str, object], bytes]:
        nonlocal swapped
        if not swapped:
            capsule.rename(moved_capsule)
            capsule.symlink_to(decoy, target_is_directory=True)
            swapped = True
        return original_read(directory_fd, name, label=label)

    monkeypatch.setattr(capsule_module, "_read_canonical_document", swap_then_read)
    result = validate_capsule(capsule, study=fixture_payload())
    assert result["capsule_valid"] is True
    assert list(decoy.iterdir()) == []


def test_compile_rejects_member_mutation_between_reread_and_final_sweep(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capsule = tmp_path / "capsule"
    original_verify = capsule_module._verify_members_unchanged

    def mutate_then_verify(
        directory_fd: int,
        snapshots: dict[str, capsule_module.MemberSnapshot],
    ) -> None:
        descriptor = capsule_module.os.open(
            "reconciliation.json",
            capsule_module.os.O_WRONLY | capsule_module.os.O_TRUNC,
            dir_fd=directory_fd,
        )
        try:
            capsule_module.os.write(descriptor, b"{}\n")
        finally:
            capsule_module.os.close(descriptor)
        original_verify(directory_fd, snapshots)

    monkeypatch.setattr(capsule_module, "_verify_members_unchanged", mutate_then_verify)
    with pytest.raises(ValidationError, match="changed during the validation window"):
        compile_capsule(fixture_payload(), capsule)


def test_validation_rejects_member_mutation_before_final_reread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capsule = tmp_path / "capsule"
    compile_capsule(fixture_payload(), capsule)
    original_verify = capsule_module._verify_members_unchanged

    def mutate_then_verify(
        directory_fd: int,
        snapshots: dict[str, capsule_module.MemberSnapshot],
    ) -> None:
        descriptor = capsule_module.os.open(
            "pec_candidate_evidence.json",
            capsule_module.os.O_WRONLY | capsule_module.os.O_TRUNC,
            dir_fd=directory_fd,
        )
        try:
            capsule_module.os.write(descriptor, b"{}\n")
        finally:
            capsule_module.os.close(descriptor)
        original_verify(directory_fd, snapshots)

    monkeypatch.setattr(capsule_module, "_verify_members_unchanged", mutate_then_verify)
    with pytest.raises(ValidationError, match="changed during the validation window"):
        validate_capsule(capsule)


def test_validation_rejects_member_mutation_during_final_stat_sweep(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capsule = tmp_path / "capsule"
    compile_capsule(fixture_payload(), capsule)
    original_stat = capsule_module.os.stat
    call_count = 0

    def mutate_during_stat(path: object, *args: object, **kwargs: object) -> object:
        nonlocal call_count
        if kwargs.get("dir_fd") is not None and path == "pec_candidate_evidence.json":
            call_count += 1
            if call_count == 5:
                descriptor = capsule_module.os.open(
                    "reconciliation.json",
                    capsule_module.os.O_WRONLY | capsule_module.os.O_TRUNC,
                    dir_fd=kwargs["dir_fd"],
                )
                try:
                    capsule_module.os.write(descriptor, b"{}\n")
                finally:
                    capsule_module.os.close(descriptor)
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(capsule_module.os, "stat", mutate_during_stat)
    with pytest.raises(ValidationError, match="final stat sweep"):
        validate_capsule(capsule)


def test_last_stat_mutation_is_reported_as_snapshot_only_and_fresh_validation_rejects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capsule = tmp_path / "capsule"
    compile_capsule(fixture_payload(), capsule)
    original_stat = capsule_module.os.stat
    pec_stat_count = 0

    def mutate_earliest_after_its_last_stat(
        path: object,
        *args: object,
        **kwargs: object,
    ) -> object:
        nonlocal pec_stat_count
        if kwargs.get("dir_fd") is not None and path == "pec_candidate_evidence.json":
            pec_stat_count += 1
            if pec_stat_count == 5:
                descriptor = capsule_module.os.open(
                    "manifest.json",
                    capsule_module.os.O_WRONLY | capsule_module.os.O_TRUNC,
                    dir_fd=kwargs["dir_fd"],
                )
                try:
                    capsule_module.os.write(descriptor, b"{}\n")
                finally:
                    capsule_module.os.close(descriptor)
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(
        capsule_module.os,
        "stat",
        mutate_earliest_after_its_last_stat,
    )
    result = validate_capsule(capsule)
    assert result["capsule_valid"] is True
    assert result["snapshot_valid"] is True
    assert result["validation_subject"] == "OWNED_BYTE_SNAPSHOT"
    assert result["mutable_path_current_at_return_guaranteed"] is False

    monkeypatch.setattr(capsule_module.os, "stat", original_stat)
    with pytest.raises(ValidationError):
        validate_capsule(capsule)
