"""Safe byte-level receipts for caller-controlled genomics artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_ARTIFACT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_KINDS = {
    "ASSEMBLY_FASTA",
    "ANNOTATION_GFF3",
    "ANNOTATION_GTF",
    "ALIGNMENT_BAM",
    "ALIGNMENT_CRAM",
    "GRAPH_GFA",
    "MAPPING_PAF",
    "TRANSCRIPTS_FASTA",
    "VARIANTS_BCF",
    "VARIANTS_VCF",
}
_COORDINATE_CONVENTIONS = {
    "NOT_APPLICABLE",
    "ONE_BASED_CLOSED",
    "ZERO_BASED_HALF_OPEN",
}
_RECEIPT_KINDS = {
    "ANNOTATION",
    "ASSEMBLY",
    "CORRESPONDENCE",
    "MATERIAL",
    "OBSERVATION",
    "SEQUENCE",
    "SOURCE",
}
_VERIFICATION_STATES = {"DECLARED_NOT_VERIFIED", "RECEIPT_VERIFIED"}
MAX_PROVENANCE_INTEGER = 9_223_372_036_854_775_807
_EVIDENCE_FIELDS = {
    "artifact_id",
    "artifact_kind",
    "artifact_sha256",
    "bytes",
    "coordinate_convention",
    "format_version",
    "path_disclosed",
    "reference_accession",
    "source_checksum_matches",
    "source_checksum_sha256",
    "verification_authority",
}
_ARTIFACT_RECEIPT_KINDS = {
    "ALIGNMENT_BAM": {"OBSERVATION", "SOURCE"},
    "ALIGNMENT_CRAM": {"OBSERVATION", "SOURCE"},
    "ANNOTATION_GFF3": {"ANNOTATION", "SOURCE"},
    "ANNOTATION_GTF": {"ANNOTATION", "SOURCE"},
    "ASSEMBLY_FASTA": {"ASSEMBLY", "SEQUENCE", "SOURCE"},
    "GRAPH_GFA": {"ASSEMBLY", "SEQUENCE", "SOURCE"},
    "MAPPING_PAF": {"OBSERVATION", "SOURCE"},
    "TRANSCRIPTS_FASTA": {"SEQUENCE", "SOURCE"},
    "VARIANTS_BCF": {"OBSERVATION", "SOURCE"},
    "VARIANTS_VCF": {"OBSERVATION", "SOURCE"},
}


class ArtifactReceiptError(ValueError):
    """The requested byte receipt is unsafe or underspecified."""


def _safe_path(value: str | os.PathLike[str]) -> Path:
    try:
        raw = os.fspath(value)
    except TypeError as exc:
        raise ArtifactReceiptError("artifact path is invalid") from exc
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        raise ArtifactReceiptError("artifact path is invalid")
    try:
        raw.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ArtifactReceiptError("artifact path is invalid") from exc
    return Path(raw)


def _text(value: Any, *, label: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
        or "\x00" in value
    ):
        raise ArtifactReceiptError(f"{label} must be a safe nonempty trimmed string")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ArtifactReceiptError(f"{label} must be a Unicode scalar string") from exc
    return value


def _canonical_bytes(value: Any) -> bytes:
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
        raise ArtifactReceiptError("artifact provenance is not canonical JSON") from exc


def _digest_json(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _owned_object(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ArtifactReceiptError(f"{label} must be an object")
    try:
        owned = json.loads(_canonical_bytes(dict(value)))
    except (RecursionError, TypeError, ValueError) as exc:
        raise ArtifactReceiptError(f"{label} is not a canonical JSON object") from exc
    if type(owned) is not dict:
        raise ArtifactReceiptError(f"{label} must be an object")
    return owned


def _sha256_regular_file(path: Path, *, max_bytes: int) -> tuple[str, int]:
    try:
        link_info = path.lstat()
    except OSError as exc:
        raise ArtifactReceiptError("artifact could not be inspected") from exc
    if not stat.S_ISREG(link_info.st_mode):
        raise ArtifactReceiptError("artifact must be a regular file")
    if link_info.st_nlink != 1:
        raise ArtifactReceiptError("artifact must be a single-link regular file")
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
        )
    except OSError as exc:
        raise ArtifactReceiptError("artifact could not be opened as a regular file") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ArtifactReceiptError("artifact must be a single-link regular file")
        if (info.st_dev, info.st_ino) != (link_info.st_dev, link_info.st_ino):
            raise ArtifactReceiptError("artifact changed before it could be opened")
        if info.st_size < 1:
            raise ArtifactReceiptError("artifact must contain at least one byte")
        if info.st_size > max_bytes:
            raise ArtifactReceiptError("artifact exceeds the configured byte limit")
        digest = hashlib.sha256()
        total = 0
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            total += len(block)
            if total > max_bytes:
                raise ArtifactReceiptError("artifact changed beyond the configured byte limit")
            digest.update(block)
        final_info = os.fstat(descriptor)
        stable_fields = (
            "st_dev",
            "st_ino",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
            "st_nlink",
        )
        if any(getattr(final_info, field) != getattr(info, field) for field in stable_fields):
            raise ArtifactReceiptError("artifact changed while it was being hashed")
        try:
            current = path.lstat()
        except OSError as exc:
            raise ArtifactReceiptError("artifact changed while it was being hashed") from exc
        if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
            raise ArtifactReceiptError("artifact identity changed while it was being hashed")
        return digest.hexdigest(), total
    finally:
        os.close(descriptor)


def artifact_receipt(
    path: str | os.PathLike[str],
    *,
    artifact_id: str,
    kind: str,
    format_version: str,
    coordinate_convention: str,
    reference_accession: str | None = None,
    source_checksum_sha256: str | None = None,
    max_bytes: int = 2**40,
) -> dict[str, Any]:
    """Bind bytes to explicit genomics semantics without publishing a local path.

    The receipt authenticates only the bytes read in this process.  It does not
    authenticate the biological sample, stock, reference choice, file contents,
    or any declaration supplied by the caller.
    """

    if not isinstance(artifact_id, str) or _ARTIFACT_ID.fullmatch(artifact_id) is None:
        raise ArtifactReceiptError("artifact_id must use the closed portable identifier syntax")
    if not isinstance(kind, str) or kind not in _KINDS:
        raise ArtifactReceiptError("artifact kind is unsupported")
    format_version = _text(format_version, label="format_version", maximum=64)
    if (
        not isinstance(coordinate_convention, str)
        or coordinate_convention not in _COORDINATE_CONVENTIONS
    ):
        raise ArtifactReceiptError("coordinate_convention is unsupported")
    if kind in {
        "ALIGNMENT_BAM",
        "ALIGNMENT_CRAM",
        "ANNOTATION_GFF3",
        "ANNOTATION_GTF",
        "MAPPING_PAF",
        "VARIANTS_BCF",
        "VARIANTS_VCF",
    }:
        if coordinate_convention == "NOT_APPLICABLE":
            raise ArtifactReceiptError(
                "coordinate-bearing artifacts require a coordinate convention"
            )
        if reference_accession is None:
            raise ArtifactReceiptError("coordinate-bearing artifacts require reference_accession")
        reference_accession = _text(reference_accession, label="reference_accession", maximum=128)
    elif reference_accession is not None:
        reference_accession = _text(reference_accession, label="reference_accession", maximum=128)
    if (
        not isinstance(max_bytes, int)
        or isinstance(max_bytes, bool)
        or max_bytes < 1
        or max_bytes > MAX_PROVENANCE_INTEGER
    ):
        raise ArtifactReceiptError(f"max_bytes must be an integer in [1, {MAX_PROVENANCE_INTEGER}]")
    if source_checksum_sha256 is not None and (
        not isinstance(source_checksum_sha256, str)
        or _SHA256.fullmatch(source_checksum_sha256) is None
    ):
        raise ArtifactReceiptError("source_checksum_sha256 must be a lowercase SHA-256 digest")

    digest, byte_count = _sha256_regular_file(_safe_path(path), max_bytes=max_bytes)
    source_checksum_matches = None
    if source_checksum_sha256 is not None:
        source_checksum_matches = digest == source_checksum_sha256
        if not source_checksum_matches:
            raise ArtifactReceiptError("artifact does not match source_checksum_sha256")
    return {
        "artifact_id": artifact_id,
        "artifact_kind": kind,
        "artifact_sha256": digest,
        "bytes": byte_count,
        "coordinate_convention": coordinate_convention,
        "format_version": format_version,
        "path_disclosed": False,
        "reference_accession": reference_accession,
        "source_checksum_matches": source_checksum_matches,
        "source_checksum_sha256": source_checksum_sha256,
        "verification_authority": "LOCAL_BYTES_ONLY",
    }


def study_receipt_from_artifact(
    evidence: Mapping[str, Any],
    *,
    receipt_id: str,
    receipt_kind: str,
    subject_sha256: str,
    issuer: str,
    verification_state: str = "DECLARED_NOT_VERIFIED",
) -> dict[str, Any]:
    """Project byte evidence into a core receipt with inline provenance.

    ``RECEIPT_VERIFIED`` means the receipt-to-byte binding was independently
    verified; it still does not authenticate biological identity or meaning.
    The graph-object subject digest remains independent of the artifact bytes.
    """

    if not isinstance(receipt_id, str) or _ARTIFACT_ID.fullmatch(receipt_id) is None:
        raise ArtifactReceiptError("receipt_id must use the closed portable identifier syntax")
    if not isinstance(receipt_kind, str) or receipt_kind not in _RECEIPT_KINDS:
        raise ArtifactReceiptError("receipt_kind is unsupported")
    if not isinstance(subject_sha256, str) or _SHA256.fullmatch(subject_sha256) is None:
        raise ArtifactReceiptError("subject_sha256 must be a lowercase SHA-256 digest")
    issuer = _text(issuer, label="issuer", maximum=240)
    if not isinstance(verification_state, str) or verification_state not in _VERIFICATION_STATES:
        raise ArtifactReceiptError("verification_state is unsupported")
    evidence = _owned_object(evidence, label="evidence")
    if set(evidence) != _EVIDENCE_FIELDS:
        raise ArtifactReceiptError("evidence violates the closed artifact contract")
    content_sha256 = evidence.get("artifact_sha256")
    if not isinstance(content_sha256, str) or _SHA256.fullmatch(content_sha256) is None:
        raise ArtifactReceiptError("evidence has no valid artifact_sha256")
    if evidence.get("verification_authority") != "LOCAL_BYTES_ONLY":
        raise ArtifactReceiptError("evidence authority is not a local byte receipt")
    if evidence.get("path_disclosed") is not False:
        raise ArtifactReceiptError("artifact evidence cannot disclose a path")
    artifact_kind = evidence.get("artifact_kind")
    if not isinstance(artifact_kind, str) or artifact_kind not in _KINDS:
        raise ArtifactReceiptError("evidence artifact kind is unsupported")
    artifact_id = evidence.get("artifact_id")
    if not isinstance(artifact_id, str) or _ARTIFACT_ID.fullmatch(artifact_id) is None:
        raise ArtifactReceiptError("evidence artifact id is invalid")
    if receipt_kind not in _ARTIFACT_RECEIPT_KINDS[artifact_kind]:
        raise ArtifactReceiptError("artifact kind cannot support the requested receipt kind")
    byte_count = evidence.get("bytes")
    if type(byte_count) is not int or byte_count < 1 or byte_count > MAX_PROVENANCE_INTEGER:
        raise ArtifactReceiptError("evidence byte count is invalid")
    format_version = evidence.get("format_version")
    coordinate_convention = evidence.get("coordinate_convention")
    reference_accession = evidence.get("reference_accession")
    source_checksum_sha256 = evidence.get("source_checksum_sha256")
    format_version = _text(format_version, label="evidence format version", maximum=64)
    if (
        not isinstance(coordinate_convention, str)
        or coordinate_convention not in _COORDINATE_CONVENTIONS
    ):
        raise ArtifactReceiptError("evidence coordinate convention is unsupported")
    if artifact_kind in {
        "ALIGNMENT_BAM",
        "ALIGNMENT_CRAM",
        "ANNOTATION_GFF3",
        "ANNOTATION_GTF",
        "MAPPING_PAF",
        "VARIANTS_BCF",
        "VARIANTS_VCF",
    } and (coordinate_convention == "NOT_APPLICABLE" or reference_accession is None):
        raise ArtifactReceiptError("coordinate-bearing evidence requires convention and reference")
    if reference_accession is not None:
        reference_accession = _text(
            reference_accession, label="evidence reference accession", maximum=128
        )
    if source_checksum_sha256 is not None and (
        not isinstance(source_checksum_sha256, str)
        or _SHA256.fullmatch(source_checksum_sha256) is None
    ):
        raise ArtifactReceiptError("evidence source checksum is invalid")
    source_checksum_matches = evidence.get("source_checksum_matches")
    if source_checksum_matches is not None and source_checksum_matches is not True:
        raise ArtifactReceiptError("evidence source checksum is not verified")
    if (source_checksum_sha256 is None) != (source_checksum_matches is None):
        raise ArtifactReceiptError("evidence source checksum fields are inconsistent")
    if source_checksum_sha256 is not None and source_checksum_sha256 != content_sha256:
        raise ArtifactReceiptError("evidence source checksum does not match artifact bytes")
    provenance = {
        "arguments_sha256": None,
        "artifact_kind": artifact_kind,
        "byte_count": byte_count,
        "capture_method": "LOCAL_ARTIFACT",
        "captured_at": None,
        "collection_audit": None,
        "content_sha256": content_sha256,
        "coordinate_convention": coordinate_convention,
        "format_version": format_version,
        "reference_accession": reference_accession,
        "schema_version": "phytolocus.receipt-provenance.v0.1",
        "source_checksum_sha256": source_checksum_sha256,
        "source_record": artifact_id,
        "source_release": None,
        "tool_name": None,
        "tool_version": None,
    }
    return {
        "artifact_sha256": _digest_json(provenance),
        "id": receipt_id,
        "issuer": issuer,
        "kind": receipt_kind,
        "provenance": provenance,
        "subject_sha256": subject_sha256,
        "verification_state": verification_state,
    }
