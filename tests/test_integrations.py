from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from phytolocus import Study, subject_sha256
from phytolocus_integrations.artifacts import (
    ArtifactReceiptError,
    artifact_receipt,
    study_receipt_from_artifact,
)
from phytolocus_integrations.tooluniverse import (
    TOOLUNIVERSE_PIN,
    SourceContractError,
    ToolUniverseAdapter,
    audit_collection,
    receipt_from_call,
    study_receipt_from_call,
)
from phytolocus_tu.fixture import fixture_payload


def test_collection_audit_never_calls_a_truncated_first_page_complete() -> None:
    audit = audit_collection(
        returned_count=100,
        reported_total=371,
        next_cursor=None,
        requested_limit=100,
    )
    assert audit == {
        "complete": False,
        "coverage_state": "TRUNCATED_WITHOUT_CURSOR",
        "next_cursor_present": False,
        "reported_total": 371,
        "requested_limit": 100,
        "returned_count": 100,
        "truncated_without_cursor": True,
    }


def test_collection_audit_distinguishes_cursor_page_and_complete_denominator() -> None:
    partial = audit_collection(
        returned_count=100,
        reported_total=371,
        next_cursor="opaque-page-token",
        requested_limit=100,
    )
    complete = audit_collection(
        returned_count=7,
        reported_total=7,
        next_cursor=None,
        requested_limit=100,
    )
    assert partial["coverage_state"] == "PAGE_PARTIAL_CURSOR_AVAILABLE"
    assert partial["truncated_without_cursor"] is False
    assert complete["coverage_state"] == "COMPLETE_REPORTED_DENOMINATOR"
    assert complete["complete"] is True


def test_cursor_precludes_complete_even_when_page_equals_reported_total() -> None:
    audit = audit_collection(
        returned_count=7,
        reported_total=7,
        next_cursor="upstream-says-more",
        requested_limit=100,
    )
    assert audit["complete"] is False
    assert audit["coverage_state"] == "CURSOR_CONTRADICTS_REPORTED_DENOMINATOR"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("returned_count", -1),
        ("reported_total", -1),
        ("requested_limit", 0),
        ("returned_count", 10**30),
        ("reported_total", 10**30),
        ("requested_limit", 10**30),
        ("returned_count", True),
        ("reported_total", False),
        ("requested_limit", True),
    ],
)
def test_collection_audit_rejects_invalid_counts(field: str, value: object) -> None:
    values = {
        "returned_count": 1,
        "reported_total": 1,
        "requested_limit": 1,
    }
    values[field] = value
    with pytest.raises(SourceContractError):
        audit_collection(next_cursor=None, **values)  # type: ignore[arg-type]


def test_artifact_receipt_binds_bytes_without_disclosing_path(tmp_path: Path) -> None:
    artifact = tmp_path / "private-looking-name.gff3"
    artifact.write_text("##gff-version 3\n", encoding="utf-8")
    receipt = artifact_receipt(
        artifact,
        artifact_id="synthetic-annotation",
        kind="ANNOTATION_GFF3",
        format_version="3",
        coordinate_convention="ONE_BASED_CLOSED",
        reference_accession="GCA_000000000.1",
    )
    assert receipt["artifact_sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert receipt["bytes"] == artifact.stat().st_size
    assert receipt["path_disclosed"] is False
    assert str(tmp_path) not in json.dumps(receipt)
    assert artifact.name not in json.dumps(receipt)
    assert receipt["verification_authority"] == "LOCAL_BYTES_ONLY"
    core_receipt = study_receipt_from_artifact(
        receipt,
        receipt_id="receipt-synthetic-annotation",
        receipt_kind="ANNOTATION",
        subject_sha256="a" * 64,
        issuer="synthetic fixture generator",
    )
    assert set(core_receipt) == {
        "artifact_sha256",
        "id",
        "issuer",
        "kind",
        "provenance",
        "subject_sha256",
        "verification_state",
    }
    assert core_receipt["kind"] == "ANNOTATION"
    assert core_receipt["subject_sha256"] == "a" * 64
    assert core_receipt["artifact_sha256"] != receipt["artifact_sha256"]
    assert core_receipt["provenance"] == {
        "arguments_sha256": None,
        "artifact_kind": "ANNOTATION_GFF3",
        "byte_count": receipt["bytes"],
        "capture_method": "LOCAL_ARTIFACT",
        "captured_at": None,
        "collection_audit": None,
        "content_sha256": receipt["artifact_sha256"],
        "coordinate_convention": "ONE_BASED_CLOSED",
        "format_version": "3",
        "reference_accession": "GCA_000000000.1",
        "schema_version": "phytolocus.receipt-provenance.v0.1",
        "source_checksum_sha256": None,
        "source_record": "synthetic-annotation",
        "source_release": None,
        "tool_name": None,
        "tool_version": None,
    }
    assert (
        core_receipt["artifact_sha256"]
        == hashlib.sha256(
            (
                json.dumps(core_receipt["provenance"], separators=(",", ":"), sort_keys=True) + "\n"
            ).encode()
        ).hexdigest()
    )


def test_artifact_kind_cannot_be_projected_to_unrelated_receipt_kind(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "synthetic.gff3"
    artifact.write_text("##gff-version 3\n", encoding="utf-8")
    evidence = artifact_receipt(
        artifact,
        artifact_id="synthetic-annotation",
        kind="ANNOTATION_GFF3",
        format_version="3",
        coordinate_convention="ONE_BASED_CLOSED",
        reference_accession="GCA_000000000.1",
    )
    with pytest.raises(ArtifactReceiptError, match="cannot support"):
        study_receipt_from_artifact(
            evidence,
            receipt_id="receipt-synthetic-annotation",
            receipt_kind="MATERIAL",
            subject_sha256="a" * 64,
            issuer="synthetic fixture generator",
        )


def test_local_artifact_projection_round_trips_through_core_model(tmp_path: Path) -> None:
    study = fixture_payload()
    assembly = Study.from_dict(study).assemblies[0]
    artifact = tmp_path / "synthetic.fa"
    artifact.write_text(">synthetic\nACGT\n", encoding="utf-8")
    evidence = artifact_receipt(
        artifact,
        artifact_id="synthetic-assembly-bytes",
        kind="ASSEMBLY_FASTA",
        format_version="FASTA",
        coordinate_convention="NOT_APPLICABLE",
    )
    projected = study_receipt_from_artifact(
        evidence,
        receipt_id="receipt-assembly",
        receipt_kind="ASSEMBLY",
        subject_sha256=subject_sha256(assembly),
        issuer="synthetic fixture generator",
    )
    study["receipts"] = [
        projected if receipt["id"] == "receipt-assembly" else receipt
        for receipt in study["receipts"]
    ]

    normalized = Study.from_dict(study).to_dict()
    receipt = next(item for item in normalized["receipts"] if item["id"] == "receipt-assembly")
    assert receipt["provenance"] == projected["provenance"]
    assert receipt["subject_sha256"] == subject_sha256(assembly)


def test_artifact_receipt_refuses_coordinate_file_without_reference(tmp_path: Path) -> None:
    artifact = tmp_path / "synthetic.vcf"
    artifact.write_text("##fileformat=VCFv4.3\n", encoding="utf-8")
    with pytest.raises(ArtifactReceiptError, match="reference_accession"):
        artifact_receipt(
            artifact,
            artifact_id="synthetic-variants",
            kind="VARIANTS_VCF",
            format_version="4.3",
            coordinate_convention="ONE_BASED_CLOSED",
        )


@pytest.mark.parametrize("kind", ["ALIGNMENT_BAM", "ALIGNMENT_CRAM"])
@pytest.mark.parametrize(
    ("coordinate_convention", "reference_accession"),
    [("NOT_APPLICABLE", "GCA_000000000.1"), ("ONE_BASED_CLOSED", None)],
)
def test_alignment_requires_coordinates_and_reference(
    tmp_path: Path,
    kind: str,
    coordinate_convention: str,
    reference_accession: str | None,
) -> None:
    artifact = tmp_path / "synthetic.alignment"
    artifact.write_bytes(b"synthetic-placeholder")
    with pytest.raises(ArtifactReceiptError, match=r"coordinate|reference"):
        artifact_receipt(
            artifact,
            artifact_id="synthetic-alignment",
            kind=kind,
            format_version="1",
            coordinate_convention=coordinate_convention,
            reference_accession=reference_accession,
        )


def test_artifact_receipt_rejects_wrong_external_checksum(tmp_path: Path) -> None:
    artifact = tmp_path / "synthetic.fa"
    artifact.write_text(">synthetic\nACGT\n", encoding="utf-8")
    with pytest.raises(ArtifactReceiptError, match="does not match"):
        artifact_receipt(
            artifact,
            artifact_id="synthetic-assembly",
            kind="ASSEMBLY_FASTA",
            format_version="FASTA",
            coordinate_convention="NOT_APPLICABLE",
            source_checksum_sha256="0" * 64,
        )


def test_artifact_receipt_refuses_empty_artifact(tmp_path: Path) -> None:
    artifact = tmp_path / "synthetic.fa"
    artifact.touch()
    with pytest.raises(ArtifactReceiptError, match="at least one byte"):
        artifact_receipt(
            artifact,
            artifact_id="synthetic-empty",
            kind="ASSEMBLY_FASTA",
            format_version="FASTA",
            coordinate_convention="NOT_APPLICABLE",
        )


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO creation is unavailable")
def test_artifact_receipt_refuses_fifo(tmp_path: Path) -> None:
    artifact = tmp_path / "pipe"
    os.mkfifo(artifact)
    with pytest.raises(ArtifactReceiptError, match="regular file"):
        artifact_receipt(
            artifact,
            artifact_id="synthetic-stream",
            kind="ASSEMBLY_FASTA",
            format_version="FASTA",
            coordinate_convention="NOT_APPLICABLE",
        )


def test_artifact_receipt_refuses_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target.fa"
    target.write_text(">synthetic\nACGT\n", encoding="utf-8")
    artifact = tmp_path / "link.fa"
    artifact.symlink_to(target)
    with pytest.raises(ArtifactReceiptError, match="regular file"):
        artifact_receipt(
            artifact,
            artifact_id="synthetic-link",
            kind="ASSEMBLY_FASTA",
            format_version="FASTA",
            coordinate_convention="NOT_APPLICABLE",
        )


def test_artifact_receipt_refuses_hard_link(tmp_path: Path) -> None:
    target = tmp_path / "target.fa"
    target.write_text(">synthetic\nACGT\n", encoding="utf-8")
    artifact = tmp_path / "hard-link.fa"
    os.link(target, artifact)
    with pytest.raises(ArtifactReceiptError, match="single-link"):
        artifact_receipt(
            artifact,
            artifact_id="synthetic-hard-link",
            kind="ASSEMBLY_FASTA",
            format_version="FASTA",
            coordinate_convention="NOT_APPLICABLE",
        )


@pytest.mark.parametrize("invalid_path", ["bad\x00path", "bad\ud800path"])
def test_artifact_receipt_rejects_invalid_filesystem_strings(
    invalid_path: str,
) -> None:
    with pytest.raises(ArtifactReceiptError, match="artifact path is invalid") as captured:
        artifact_receipt(
            invalid_path,
            artifact_id="synthetic-invalid-path",
            kind="ASSEMBLY_FASTA",
            format_version="FASTA",
            coordinate_convention="NOT_APPLICABLE",
        )
    assert invalid_path not in str(captured.value)


def test_artifact_receipt_rejects_pathlike_with_nonstring_result() -> None:
    class InvalidPath:
        def __fspath__(self) -> int:
            return 42

    with pytest.raises(ArtifactReceiptError, match="artifact path is invalid"):
        artifact_receipt(
            InvalidPath(),  # type: ignore[arg-type]
            artifact_id="synthetic-invalid-pathlike",
            kind="ASSEMBLY_FASTA",
            format_version="FASTA",
            coordinate_convention="NOT_APPLICABLE",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [("kind", []), ("coordinate_convention", [])],
)
def test_artifact_receipt_rejects_malformed_runtime_types(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    artifact = tmp_path / "synthetic.fa"
    artifact.write_text(">synthetic\nACGT\n", encoding="utf-8")
    arguments: dict[str, object] = {
        "artifact_id": "synthetic-malformed-type",
        "kind": "ASSEMBLY_FASTA",
        "format_version": "FASTA",
        "coordinate_convention": "NOT_APPLICABLE",
    }
    arguments[field] = value
    with pytest.raises(ArtifactReceiptError):
        artifact_receipt(artifact, **arguments)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value"),
    [("receipt_kind", []), ("verification_state", [])],
)
def test_artifact_projection_rejects_malformed_runtime_types(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    artifact = tmp_path / "synthetic.fa"
    artifact.write_text(">synthetic\nACGT\n", encoding="utf-8")
    evidence = artifact_receipt(
        artifact,
        artifact_id="synthetic-malformed-projection",
        kind="ASSEMBLY_FASTA",
        format_version="FASTA",
        coordinate_convention="NOT_APPLICABLE",
    )
    arguments: dict[str, object] = {
        "receipt_id": "receipt-malformed-projection",
        "receipt_kind": "ASSEMBLY",
        "subject_sha256": "a" * 64,
        "issuer": "synthetic fixture generator",
        "verification_state": "DECLARED_NOT_VERIFIED",
    }
    arguments[field] = value
    with pytest.raises(ArtifactReceiptError):
        study_receipt_from_artifact(evidence, **arguments)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("format_version", "bad\x00format"),
        ("format_version", "bad\ud800format"),
        ("reference_accession", "bad\x00reference"),
    ],
)
def test_artifact_receipt_rejects_noncanonical_text(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    artifact = tmp_path / "synthetic.fa"
    artifact.write_text(">synthetic\nACGT\n", encoding="utf-8")
    arguments: dict[str, object] = {
        "artifact_id": "synthetic-noncanonical-text",
        "kind": "ASSEMBLY_FASTA",
        "format_version": "FASTA",
        "coordinate_convention": "NOT_APPLICABLE",
    }
    arguments[field] = value
    with pytest.raises(ArtifactReceiptError):
        artifact_receipt(artifact, **arguments)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("bytes", 0),
        ("bytes", 10**30),
        ("artifact_id", []),
        ("reference_accession", 123),
    ],
)
def test_artifact_projection_rejects_internally_invalid_evidence(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    artifact = tmp_path / "synthetic.fa"
    artifact.write_text(">synthetic\nACGT\n", encoding="utf-8")
    evidence = artifact_receipt(
        artifact,
        artifact_id="synthetic-invalid-evidence",
        kind="ASSEMBLY_FASTA",
        format_version="FASTA",
        coordinate_convention="NOT_APPLICABLE",
    )
    evidence[field] = value
    with pytest.raises(ArtifactReceiptError):
        study_receipt_from_artifact(
            evidence,
            receipt_id="receipt-invalid-evidence",
            receipt_kind="ASSEMBLY",
            subject_sha256="a" * 64,
            issuer="synthetic fixture generator",
        )


def test_artifact_projection_recomputes_checksum_consistency(tmp_path: Path) -> None:
    artifact = tmp_path / "synthetic.fa"
    artifact.write_text(">synthetic\nACGT\n", encoding="utf-8")
    evidence = artifact_receipt(
        artifact,
        artifact_id="synthetic-checksum-evidence",
        kind="ASSEMBLY_FASTA",
        format_version="FASTA",
        coordinate_convention="NOT_APPLICABLE",
    )
    evidence["source_checksum_sha256"] = "b" * 64
    evidence["source_checksum_matches"] = True
    with pytest.raises(ArtifactReceiptError, match="does not match"):
        study_receipt_from_artifact(
            evidence,
            receipt_id="receipt-checksum-evidence",
            receipt_kind="ASSEMBLY",
            subject_sha256="a" * 64,
            issuer="synthetic fixture generator",
        )


def test_artifact_projection_validates_one_owned_evidence_snapshot(tmp_path: Path) -> None:
    artifact = tmp_path / "synthetic.fa"
    artifact.write_text(">synthetic\nACGT\n", encoding="utf-8")
    valid = artifact_receipt(
        artifact,
        artifact_id="synthetic-owned-snapshot",
        kind="ASSEMBLY_FASTA",
        format_version="FASTA",
        coordinate_convention="NOT_APPLICABLE",
    )

    class ContradictoryEvidence(dict[str, object]):
        def get(self, key: str, default: object = None) -> object:
            if key == "verification_authority":
                return "LOCAL_BYTES_ONLY"
            return super().get(key, default)

    contradictory = ContradictoryEvidence(valid)
    contradictory["verification_authority"] = "UNTRUSTED"
    with pytest.raises(ArtifactReceiptError, match="authority"):
        study_receipt_from_artifact(
            contradictory,
            receipt_id="receipt-owned-snapshot",
            receipt_kind="ASSEMBLY",
            subject_sha256="a" * 64,
            issuer="synthetic fixture generator",
        )


def test_artifact_receipt_rejects_maximum_above_core_provenance_bound(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "synthetic.fa"
    artifact.write_text(">synthetic\nACGT\n", encoding="utf-8")
    with pytest.raises(ArtifactReceiptError, match="max_bytes"):
        artifact_receipt(
            artifact,
            artifact_id="synthetic-unbounded-limit",
            kind="ASSEMBLY_FASTA",
            format_version="FASTA",
            coordinate_convention="NOT_APPLICABLE",
            max_bytes=10**30,
        )


def test_tooluniverse_facade_uses_exact_registered_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "phytolocus_integrations.tooluniverse.importlib.metadata.version",
        lambda package: TOOLUNIVERSE_PIN,
    )

    class Tools:
        @staticmethod
        def NCBIDatasets_get_genome_assembly(**arguments: object) -> dict[str, object]:
            return {"status": "success", "arguments": arguments}

    adapter = ToolUniverseAdapter(SimpleNamespace(tools=Tools()))
    assert adapter.available(["NCBIDatasets_get_genome_assembly", "Imaginary_missing_tool"]) == {
        "NCBIDatasets_get_genome_assembly": True,
        "Imaginary_missing_tool": False,
    }
    result = adapter.call("NCBIDatasets_get_genome_assembly", accession="GCF_000001735.4")
    assert result["status"] == "success"
    with pytest.raises(SourceContractError, match="unavailable"):
        adapter.call("Imaginary_missing_tool")


@pytest.mark.parametrize(
    "names",
    [None, 123, "abc", [123], ["bad\x00tool"], ["bad\ud800tool"], ["same", "same"]],
)
def test_tooluniverse_facade_rejects_malformed_name_collections(
    monkeypatch: pytest.MonkeyPatch,
    names: object,
) -> None:
    monkeypatch.setattr(
        "phytolocus_integrations.tooluniverse.importlib.metadata.version",
        lambda package: TOOLUNIVERSE_PIN,
    )
    adapter = ToolUniverseAdapter(SimpleNamespace(tools=SimpleNamespace()))
    with pytest.raises(SourceContractError):
        adapter.available(names)  # type: ignore[arg-type]


@pytest.mark.parametrize("name", ["bad\x00tool", "bad\ud800tool"])
def test_tooluniverse_facade_rejects_malformed_call_names(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> None:
    monkeypatch.setattr(
        "phytolocus_integrations.tooluniverse.importlib.metadata.version",
        lambda package: TOOLUNIVERSE_PIN,
    )
    adapter = ToolUniverseAdapter(SimpleNamespace(tools=SimpleNamespace()))
    with pytest.raises(SourceContractError):
        adapter.call(name)


def test_tooluniverse_receipt_commits_but_does_not_echo_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "phytolocus_integrations.tooluniverse.importlib.metadata.version",
        lambda package: TOOLUNIVERSE_PIN,
    )
    arguments = {"accession": "GCF_000001735.4"}
    response = {"status": "success", "data": {"assembly_name": "TAIR10.1"}}
    receipt = receipt_from_call(
        tool_name="NCBIDatasets_get_genome_assembly",
        arguments=arguments,
        response=response,
        source_name="NCBI Datasets",
        source_release="v2",
        retrieved_at="2026-08-13T00:00:00Z",
    )
    assert receipt["tooluniverse_version"] == TOOLUNIVERSE_PIN
    assert (
        receipt["arguments_sha256"]
        == hashlib.sha256(
            (json.dumps(arguments, separators=(",", ":"), sort_keys=True) + "\n").encode()
        ).hexdigest()
    )
    assert "arguments" not in receipt
    assert "response" not in receipt
    assert receipt["verification_authority"] == "TRANSPORT_AND_BYTE_COMMITMENT_ONLY"
    core_receipt = study_receipt_from_call(
        receipt,
        receipt_id="receipt-ncbi-assembly-call",
        subject_sha256="a" * 64,
    )
    assert core_receipt["id"] == "receipt-ncbi-assembly-call"
    assert core_receipt["issuer"] == "NCBI Datasets"
    assert core_receipt["kind"] == "SOURCE"
    assert core_receipt["subject_sha256"] == "a" * 64
    assert core_receipt["verification_state"] == "DECLARED_NOT_VERIFIED"
    assert core_receipt["provenance"] == {
        "arguments_sha256": receipt["arguments_sha256"],
        "artifact_kind": None,
        "byte_count": None,
        "capture_method": "TOOLUNIVERSE_CALL",
        "captured_at": "2026-08-13T00:00:00Z",
        "collection_audit": None,
        "content_sha256": receipt["response_sha256"],
        "coordinate_convention": None,
        "format_version": None,
        "reference_accession": None,
        "schema_version": "phytolocus.receipt-provenance.v0.1",
        "source_checksum_sha256": None,
        "source_record": "NCBI Datasets",
        "source_release": "v2",
        "tool_name": "NCBIDatasets_get_genome_assembly",
        "tool_version": TOOLUNIVERSE_PIN,
    }
    assert (
        core_receipt["artifact_sha256"]
        == hashlib.sha256(
            (
                json.dumps(core_receipt["provenance"], separators=(",", ":"), sort_keys=True) + "\n"
            ).encode()
        ).hexdigest()
    )


def test_unsuccessful_tool_call_cannot_be_promoted_to_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "phytolocus_integrations.tooluniverse.importlib.metadata.version",
        lambda package: TOOLUNIVERSE_PIN,
    )
    with pytest.raises(SourceContractError, match="unsuccessful"):
        receipt_from_call(
            tool_name="NCBIDatasets_get_genome_assembly",
            arguments={"accession": "missing"},
            response={"status": "error", "error": "not found"},
            source_name="NCBI Datasets",
            source_release="v2",
            retrieved_at="2026-08-13T00:00:00Z",
        )


def test_source_receipt_validates_and_hashes_one_owned_response_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "phytolocus_integrations.tooluniverse.importlib.metadata.version",
        lambda package: TOOLUNIVERSE_PIN,
    )

    class ContradictoryResponse(dict[str, object]):
        def get(self, key: str, default: object = None) -> object:
            if key == "status":
                return "success"
            return super().get(key, default)

    response = ContradictoryResponse({"status": "error", "data": {"reason": "failed transport"}})
    with pytest.raises(SourceContractError, match="unsuccessful"):
        receipt_from_call(
            tool_name="NCBIDatasets_get_genome_assembly",
            arguments={"accession": "GCF_000001735.4"},
            response=response,
            source_name="NCBI Datasets",
            source_release="v2",
            retrieved_at="2026-08-13T00:00:00Z",
        )


def test_missing_status_cannot_be_promoted_to_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "phytolocus_integrations.tooluniverse.importlib.metadata.version",
        lambda package: TOOLUNIVERSE_PIN,
    )
    with pytest.raises(SourceContractError, match="unsuccessful"):
        receipt_from_call(
            tool_name="NCBIDatasets_get_genome_assembly",
            arguments={"accession": "GCF_000001735.4"},
            response={"data": {"accession": "GCF_000001735.4"}},
            source_name="NCBI Datasets",
            source_release="v2",
            retrieved_at="2026-08-13T00:00:00Z",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [("arguments", None), ("response", [])],
)
def test_source_receipt_rejects_malformed_payload_types(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    monkeypatch.setattr(
        "phytolocus_integrations.tooluniverse.importlib.metadata.version",
        lambda package: TOOLUNIVERSE_PIN,
    )
    arguments: dict[str, object] = {
        "tool_name": "NCBIDatasets_get_genome_assembly",
        "arguments": {"accession": "GCF_000001735.4"},
        "response": {"status": "success", "data": {}},
        "source_name": "NCBI Datasets",
        "source_release": "v2",
        "retrieved_at": "2026-08-13T00:00:00Z",
    }
    arguments[field] = value
    with pytest.raises(SourceContractError):
        receipt_from_call(**arguments)  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["arguments", "response"])
def test_source_receipt_types_deep_payload_failure(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    monkeypatch.setattr(
        "phytolocus_integrations.tooluniverse.importlib.metadata.version",
        lambda package: TOOLUNIVERSE_PIN,
    )
    nested: dict[str, object] = {}
    cursor = nested
    for _ in range(129):
        child: dict[str, object] = {}
        cursor["nested"] = child
        cursor = child
    arguments: dict[str, object] = {
        "tool_name": "NCBIDatasets_get_genome_assembly",
        "arguments": {"accession": "GCF_000001735.4"},
        "response": {"status": "success", "data": {}},
        "source_name": "NCBI Datasets",
        "source_release": "v2",
        "retrieved_at": "2026-08-13T00:00:00Z",
    }
    arguments[field] = nested
    if field == "response":
        nested["status"] = "success"
    with pytest.raises(SourceContractError, match="canonical JSON"):
        receipt_from_call(**arguments)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tool_name", "bad\x00tool"),
        ("tool_name", "bad\ud800tool"),
        ("source_name", "bad\x00source"),
        ("source_name", "bad\ud800source"),
        ("source_release", "bad\x00release"),
    ],
)
def test_source_receipt_rejects_noncanonical_text(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: str,
) -> None:
    monkeypatch.setattr(
        "phytolocus_integrations.tooluniverse.importlib.metadata.version",
        lambda package: TOOLUNIVERSE_PIN,
    )
    arguments = {
        "tool_name": "NCBIDatasets_get_genome_assembly",
        "arguments": {"accession": "GCF_000001735.4"},
        "response": {"status": "success", "data": {}},
        "source_name": "NCBI Datasets",
        "source_release": "v2",
        "retrieved_at": "2026-08-13T00:00:00Z",
    }
    arguments[field] = value
    with pytest.raises(SourceContractError):
        receipt_from_call(**arguments)


def test_source_projection_rejects_malformed_verification_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "phytolocus_integrations.tooluniverse.importlib.metadata.version",
        lambda package: TOOLUNIVERSE_PIN,
    )
    call_receipt = receipt_from_call(
        tool_name="NCBIDatasets_get_genome_assembly",
        arguments={"accession": "GCF_000001735.4"},
        response={"status": "success", "data": {}},
        source_name="NCBI Datasets",
        source_release="v2",
        retrieved_at="2026-08-13T00:00:00Z",
    )
    with pytest.raises(SourceContractError):
        study_receipt_from_call(
            call_receipt,
            receipt_id="receipt-malformed-state",
            subject_sha256="a" * 64,
            verification_state=[],  # type: ignore[arg-type]
        )


def test_source_projection_validates_one_owned_call_receipt_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "phytolocus_integrations.tooluniverse.importlib.metadata.version",
        lambda package: TOOLUNIVERSE_PIN,
    )
    valid = receipt_from_call(
        tool_name="NCBIDatasets_get_genome_assembly",
        arguments={"accession": "GCF_000001735.4"},
        response={"status": "success", "data": {}},
        source_name="NCBI Datasets",
        source_release="v2",
        retrieved_at="2026-08-13T00:00:00Z",
    )

    class ContradictoryCallReceipt(dict[str, object]):
        def get(self, key: str, default: object = None) -> object:
            if key == "transport_success_only":
                return True
            return super().get(key, default)

    contradictory = ContradictoryCallReceipt(valid)
    contradictory["transport_success_only"] = False
    with pytest.raises(SourceContractError, match="does not establish transport success"):
        study_receipt_from_call(
            contradictory,
            receipt_id="receipt-contradictory-snapshot",
            subject_sha256="a" * 64,
        )


@pytest.mark.parametrize("bad_timestamp", ["today", "2026-99-99T99:99:99Z"])
def test_noncanonical_retrieval_time_cannot_be_promoted_to_receipt(
    monkeypatch: pytest.MonkeyPatch,
    bad_timestamp: str,
) -> None:
    monkeypatch.setattr(
        "phytolocus_integrations.tooluniverse.importlib.metadata.version",
        lambda package: TOOLUNIVERSE_PIN,
    )
    with pytest.raises(SourceContractError, match="UTC RFC 3339"):
        receipt_from_call(
            tool_name="NCBIDatasets_get_genome_assembly",
            arguments={"accession": "GCF_000001735.4"},
            response={"status": "success", "data": {}},
            source_name="NCBI Datasets",
            source_release="v2",
            retrieved_at=bad_timestamp,
        )


def test_forged_collection_audit_cannot_be_committed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "phytolocus_integrations.tooluniverse.importlib.metadata.version",
        lambda package: TOOLUNIVERSE_PIN,
    )
    forged = audit_collection(
        returned_count=3,
        reported_total=371,
        next_cursor=None,
        requested_limit=3,
    )
    forged["complete"] = True
    forged["coverage_state"] = "COMPLETE_REPORTED_DENOMINATOR"
    with pytest.raises(SourceContractError, match="recomputation"):
        receipt_from_call(
            tool_name="NCBIDatasets_list_genomes_by_taxon",
            arguments={"taxon": "3702", "limit": 3},
            response={"status": "success", "data": []},
            source_name="NCBI Datasets",
            source_release="v2",
            retrieved_at="2026-08-13T00:00:00Z",
            collection_audit=forged,
        )
