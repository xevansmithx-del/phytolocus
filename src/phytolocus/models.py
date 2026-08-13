"""Strict, dependency-free public models for PhytoLocus v0.1.

PhytoLocus consumes caller-supplied, content-addressed evidence.  These models
validate structure and internal bindings; they deliberately do not authenticate
receipt issuers or turn a technically compatible mapping into admissible
biological identity.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

MAX_COLLECTION_ITEMS = 10_000
MAX_STUDY_RECORDS = 50_000
MAX_STUDY_REFERENCES = 100_000


class ModelError(ValueError):
    """Raised when a public contract object is invalid."""


class ReceiptKind(str, Enum):
    SOURCE = "SOURCE"
    MATERIAL = "MATERIAL"
    ASSEMBLY = "ASSEMBLY"
    SEQUENCE = "SEQUENCE"
    ANNOTATION = "ANNOTATION"
    OBSERVATION = "OBSERVATION"
    CORRESPONDENCE = "CORRESPONDENCE"


_LOCAL_ARTIFACT_RECEIPT_KINDS = {
    "ALIGNMENT_BAM": {ReceiptKind.OBSERVATION, ReceiptKind.SOURCE},
    "ALIGNMENT_CRAM": {ReceiptKind.OBSERVATION, ReceiptKind.SOURCE},
    "ANNOTATION_GFF3": {ReceiptKind.ANNOTATION, ReceiptKind.SOURCE},
    "ANNOTATION_GTF": {ReceiptKind.ANNOTATION, ReceiptKind.SOURCE},
    "ASSEMBLY_FASTA": {ReceiptKind.ASSEMBLY, ReceiptKind.SEQUENCE, ReceiptKind.SOURCE},
    "GRAPH_GFA": {ReceiptKind.ASSEMBLY, ReceiptKind.SEQUENCE, ReceiptKind.SOURCE},
    "MAPPING_PAF": {ReceiptKind.OBSERVATION, ReceiptKind.SOURCE},
    "TRANSCRIPTS_FASTA": {ReceiptKind.SEQUENCE, ReceiptKind.SOURCE},
    "VARIANTS_BCF": {ReceiptKind.OBSERVATION, ReceiptKind.SOURCE},
    "VARIANTS_VCF": {ReceiptKind.OBSERVATION, ReceiptKind.SOURCE},
}


class VerificationState(str, Enum):
    RECEIPT_VERIFIED = "RECEIPT_VERIFIED"
    DECLARED_NOT_VERIFIED = "DECLARED_NOT_VERIFIED"


class MaterialKind(str, Enum):
    STOCK = "STOCK"
    SAMPLE = "SAMPLE"
    INDIVIDUAL = "INDIVIDUAL"
    HAPLOTYPE_REPRESENTATION = "HAPLOTYPE_REPRESENTATION"


class IndependenceState(str, Enum):
    SAME_ORIGIN = "SAME_ORIGIN"
    DISTINCT_DECLARED = "DISTINCT_DECLARED"
    UNRESOLVED = "UNRESOLVED"


class PhasingState(str, Enum):
    PHASED = "PHASED"
    UNPHASED = "UNPHASED"
    MIXED = "MIXED"
    UNKNOWN = "UNKNOWN"


class CompletenessState(str, Enum):
    DECLARED_COMPLETE = "DECLARED_COMPLETE"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"


class SequenceKind(str, Enum):
    GENOMIC = "GENOMIC"
    TRANSCRIPT = "TRANSCRIPT"
    CDS = "CDS"
    PROTEIN = "PROTEIN"


class Alphabet(str, Enum):
    DNA = "DNA"
    RNA = "RNA"
    PROTEIN = "PROTEIN"


class Strand(str, Enum):
    PLUS = "+"
    MINUS = "-"
    UNKNOWN = "UNKNOWN"


class ObservationKind(str, Enum):
    ALIGNMENT = "ALIGNMENT"
    COVERAGE = "COVERAGE"
    COPY_NUMBER = "COPY_NUMBER"
    PHASE = "PHASE"
    TRANSCRIPT_SUPPORT = "TRANSCRIPT_SUPPORT"
    MODEL_CROSSWALK = "MODEL_CROSSWALK"


class ResultState(str, Enum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    AMBIGUOUS = "AMBIGUOUS"
    NO_RESULT = "NO_RESULT"
    NOT_ASSESSED = "NOT_ASSESSED"
    FAILED = "FAILED"


class CorrespondenceRelation(str, Enum):
    SAME_SEQUENCE = "SAME_SEQUENCE"
    SAME_LOCUS = "SAME_LOCUS"
    ALLELE_OF = "ALLELE_OF"
    HAPLOTYPE_OF = "HAPLOTYPE_OF"
    TRANSCRIPT_OF = "TRANSCRIPT_OF"
    PROTEIN_OF = "PROTEIN_OF"
    SUPERSEDES = "SUPERSEDES"
    REPRESENTS = "REPRESENTS"


class CorrespondenceState(str, Enum):
    EXACT = "EXACT"
    SUPPORTED = "SUPPORTED"
    AMBIGUOUS = "AMBIGUOUS"
    CONFLICT = "CONFLICT"
    UNRESOLVED = "UNRESOLVED"


_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


def _reject_nonfinite(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ModelError("non-finite numbers are prohibited")
    if isinstance(value, Mapping):
        for nested in value.values():
            _reject_nonfinite(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _reject_nonfinite(nested)


def canonical_bytes(value: Any) -> bytes:
    """Return the sole canonical JSON encoding used by the core."""

    if hasattr(value, "to_dict"):
        value = value.to_dict()
    _reject_nonfinite(value)
    return (
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _object(value: Any, fields: Iterable[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ModelError(f"{label} must be an object")
    for key in value:
        if not isinstance(key, str):
            raise ModelError(f"{label} field names must be strings")
        try:
            key.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ModelError(f"{label} field name is not a Unicode scalar string") from exc
    expected = set(fields)
    observed = set(value)
    if expected != observed:
        missing = sorted(expected - observed)
        extra = sorted(observed - expected)
        raise ModelError(f"{label} fields mismatch; missing={missing}, extra={extra}")
    return value


def _array(value: Any, label: str, *, nonempty: bool = False) -> list[Any]:
    if not isinstance(value, list):
        raise ModelError(f"{label} must be an array")
    if len(value) > MAX_COLLECTION_ITEMS:
        raise ModelError(f"{label} exceeds the {MAX_COLLECTION_ITEMS}-item collection limit")
    if nonempty and not value:
        raise ModelError(f"{label} must not be empty")
    return value


def _string(value: Any, label: str, *, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ModelError(f"{label} must be a nonempty string up to {maximum} characters")
    if "\x00" in value:
        raise ModelError(f"{label} contains a NUL byte")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ModelError(f"{label} is not a Unicode scalar string") from exc
    return value


def _optional_string(value: Any, label: str, *, maximum: int = 512) -> str | None:
    if value is None:
        return None
    return _string(value, label, maximum=maximum)


def _identifier(value: Any, label: str) -> str:
    text = _string(value, label, maximum=128)
    if not _ID_RE.fullmatch(text):
        raise ModelError(f"{label} is not a valid opaque identifier")
    return text


def _ids(value: Any, label: str, *, nonempty: bool = False) -> tuple[str, ...]:
    result = tuple(
        _identifier(item, f"{label}[{index}]") for index, item in enumerate(_array(value, label))
    )
    if nonempty and not result:
        raise ModelError(f"{label} must not be empty")
    if len(set(result)) != len(result):
        raise ModelError(f"{label} contains duplicate identifiers")
    return result


def _sha(value: Any, label: str) -> str:
    text = _string(value, label, maximum=64)
    if not _SHA_RE.fullmatch(text):
        raise ModelError(f"{label} must be a lowercase SHA-256")
    return text


def _optional_sha(value: Any, label: str) -> str | None:
    if value is None:
        return None
    return _sha(value, label)


def _integer(
    value: Any,
    label: str,
    *,
    minimum: int = 0,
    maximum: int = 9_223_372_036_854_775_807,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum or value > maximum:
        raise ModelError(f"{label} must be an integer in [{minimum}, {maximum}]")
    return value


def _enum(value: Any, kind: type[Enum], label: str) -> Any:
    try:
        return kind(value)
    except (TypeError, ValueError) as exc:
        allowed = sorted(item.value for item in kind)
        raise ModelError(f"{label} must be one of {allowed}") from exc


def _unique(items: Iterable[Any], attribute: str, label: str) -> None:
    values = [getattr(item, attribute) for item in items]
    if len(values) != len(set(values)):
        raise ModelError(f"{label} identifiers must be unique")


@dataclass(frozen=True)
class TargetSelection:
    allowed: bool
    status: str

    @classmethod
    def from_dict(cls, raw: Any) -> TargetSelection:
        data = _object(raw, {"allowed", "status"}, "target_selection")
        if data["allowed"] is not False or data["status"] != "PROHIBITED":
            raise ModelError("target_selection must be {'allowed': false, 'status': 'PROHIBITED'}")
        return cls(False, "PROHIBITED")

    def to_dict(self) -> dict[str, Any]:
        return {"allowed": False, "status": "PROHIBITED"}


@dataclass(frozen=True)
class ReceiptProvenance:
    schema_version: str
    capture_method: str
    content_sha256: str
    artifact_kind: str | None
    byte_count: int | None
    arguments_sha256: str | None
    tool_name: str | None
    tool_version: str | None
    captured_at: str | None
    source_record: str | None
    source_release: str | None
    collection_audit: dict[str, Any] | None
    format_version: str | None
    coordinate_convention: str | None
    reference_accession: str | None
    source_checksum_sha256: str | None

    @classmethod
    def from_dict(cls, raw: Any) -> ReceiptProvenance:
        data = _object(
            raw,
            {
                "arguments_sha256",
                "artifact_kind",
                "byte_count",
                "capture_method",
                "captured_at",
                "collection_audit",
                "content_sha256",
                "coordinate_convention",
                "format_version",
                "reference_accession",
                "schema_version",
                "source_checksum_sha256",
                "source_record",
                "source_release",
                "tool_name",
                "tool_version",
            },
            "receipt.provenance",
        )
        if data["schema_version"] != "phytolocus.receipt-provenance.v0.1":
            raise ModelError("receipt provenance schema version is unsupported")
        method = data["capture_method"]
        if method not in {"DECLARATION", "LOCAL_ARTIFACT", "TOOLUNIVERSE_CALL"}:
            raise ModelError("receipt provenance capture_method is unsupported")
        artifact_kind = _optional_string(
            data["artifact_kind"], "receipt.provenance.artifact_kind", maximum=64
        )
        allowed_artifact_kinds = {
            "ALIGNMENT_BAM",
            "ALIGNMENT_CRAM",
            "ANNOTATION_GFF3",
            "ANNOTATION_GTF",
            "ASSEMBLY_FASTA",
            "GRAPH_GFA",
            "MAPPING_PAF",
            "TRANSCRIPTS_FASTA",
            "VARIANTS_BCF",
            "VARIANTS_VCF",
        }
        if artifact_kind is not None and artifact_kind not in allowed_artifact_kinds:
            raise ModelError("receipt provenance artifact_kind is unsupported")
        byte_count = (
            None
            if data["byte_count"] is None
            else _integer(data["byte_count"], "receipt.provenance.byte_count", minimum=1)
        )
        audit = data["collection_audit"]
        if audit is not None:
            audit = cls._collection_audit(audit)
        coordinate = _optional_string(
            data["coordinate_convention"],
            "receipt.provenance.coordinate_convention",
            maximum=64,
        )
        if coordinate not in {None, "NOT_APPLICABLE", "ONE_BASED_CLOSED", "ZERO_BASED_HALF_OPEN"}:
            raise ModelError("receipt provenance coordinate convention is unsupported")
        captured_at = _optional_string(
            data["captured_at"], "receipt.provenance.captured_at", maximum=64
        )
        if captured_at is not None:
            if (
                re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", captured_at)
                is None
            ):
                raise ModelError("receipt provenance captured_at must be a UTC RFC 3339 timestamp")
            try:
                datetime.fromisoformat(captured_at[:-1] + "+00:00")
            except ValueError as exc:
                raise ModelError(
                    "receipt provenance captured_at must be a UTC RFC 3339 timestamp"
                ) from exc
        result = cls(
            data["schema_version"],
            method,
            _sha(data["content_sha256"], "receipt.provenance.content_sha256"),
            artifact_kind,
            byte_count,
            _optional_sha(data["arguments_sha256"], "receipt.provenance.arguments_sha256"),
            _optional_string(data["tool_name"], "receipt.provenance.tool_name", maximum=128),
            _optional_string(data["tool_version"], "receipt.provenance.tool_version", maximum=64),
            captured_at,
            _optional_string(
                data["source_record"], "receipt.provenance.source_record", maximum=240
            ),
            _optional_string(
                data["source_release"], "receipt.provenance.source_release", maximum=128
            ),
            audit,
            _optional_string(
                data["format_version"], "receipt.provenance.format_version", maximum=64
            ),
            coordinate,
            _optional_string(
                data["reference_accession"],
                "receipt.provenance.reference_accession",
                maximum=128,
            ),
            _optional_sha(
                data["source_checksum_sha256"],
                "receipt.provenance.source_checksum_sha256",
            ),
        )
        result._validate_method_contract()
        return result

    def _validate_method_contract(self) -> None:
        if self.capture_method == "TOOLUNIVERSE_CALL":
            required = {
                "arguments_sha256": self.arguments_sha256,
                "captured_at": self.captured_at,
                "source_record": self.source_record,
                "tool_name": self.tool_name,
                "tool_version": self.tool_version,
            }
            if any(value is None for value in required.values()):
                raise ModelError(
                    "TOOLUNIVERSE_CALL provenance requires arguments, timestamp, source, "
                    "tool name, and tool version"
                )
            if any(
                value is not None
                for value in (
                    self.artifact_kind,
                    self.byte_count,
                    self.coordinate_convention,
                    self.format_version,
                    self.reference_accession,
                    self.source_checksum_sha256,
                )
            ):
                raise ModelError(
                    "TOOLUNIVERSE_CALL provenance cannot masquerade as a local artifact"
                )
        elif self.capture_method == "LOCAL_ARTIFACT":
            required = {
                "artifact_kind": self.artifact_kind,
                "byte_count": self.byte_count,
                "coordinate_convention": self.coordinate_convention,
                "format_version": self.format_version,
                "source_record": self.source_record,
            }
            if any(value is None for value in required.values()):
                raise ModelError(
                    "LOCAL_ARTIFACT provenance requires kind, bytes, format, coordinates, "
                    "and source record"
                )
            if any(
                value is not None
                for value in (
                    self.arguments_sha256,
                    self.captured_at,
                    self.collection_audit,
                    self.tool_name,
                    self.tool_version,
                )
            ):
                raise ModelError("LOCAL_ARTIFACT provenance cannot masquerade as a source call")
            coordinate_bearing = {
                "ALIGNMENT_BAM",
                "ALIGNMENT_CRAM",
                "ANNOTATION_GFF3",
                "ANNOTATION_GTF",
                "MAPPING_PAF",
                "VARIANTS_BCF",
                "VARIANTS_VCF",
            }
            if self.artifact_kind in coordinate_bearing and (
                self.coordinate_convention == "NOT_APPLICABLE" or self.reference_accession is None
            ):
                raise ModelError(
                    "coordinate-bearing LOCAL_ARTIFACT provenance requires a convention "
                    "and reference accession"
                )
            if (
                self.source_checksum_sha256 is not None
                and self.source_checksum_sha256 != self.content_sha256
            ):
                raise ModelError("LOCAL_ARTIFACT source checksum does not match the content digest")
        elif any(
            value is not None
            for value in (
                self.arguments_sha256,
                self.artifact_kind,
                self.byte_count,
                self.captured_at,
                self.collection_audit,
                self.coordinate_convention,
                self.format_version,
                self.reference_accession,
                self.source_checksum_sha256,
                self.source_record,
                self.source_release,
                self.tool_name,
                self.tool_version,
            )
        ):
            raise ModelError(
                "DECLARATION provenance cannot masquerade as a source call or local artifact"
            )

    @staticmethod
    def _collection_audit(raw: Any) -> dict[str, Any]:
        data = _object(
            raw,
            {
                "complete",
                "coverage_state",
                "next_cursor_present",
                "reported_total",
                "requested_limit",
                "returned_count",
                "truncated_without_cursor",
            },
            "receipt.provenance.collection_audit",
        )
        for field in ("complete", "next_cursor_present", "truncated_without_cursor"):
            if type(data[field]) is not bool:
                raise ModelError(f"receipt provenance collection audit {field} must be boolean")
        returned = _integer(
            data["returned_count"],
            "receipt.provenance.collection_audit.returned_count",
        )
        totals: dict[str, int | None] = {}
        for field in ("reported_total", "requested_limit"):
            value = data[field]
            totals[field] = (
                None
                if value is None
                else _integer(
                    value,
                    f"receipt.provenance.collection_audit.{field}",
                    minimum=0 if field == "reported_total" else 1,
                )
            )
        state = _string(
            data["coverage_state"],
            "receipt.provenance.collection_audit.coverage_state",
            maximum=64,
        )
        allowed = {
            "COMPLETE_REPORTED_DENOMINATOR",
            "TRUNCATED_WITHOUT_CURSOR",
            "PAGE_PARTIAL_CURSOR_AVAILABLE",
            "CURSOR_CONTRADICTS_REPORTED_DENOMINATOR",
            "COMPLETENESS_UNRESOLVED",
        }
        if state not in allowed:
            raise ModelError("receipt provenance collection coverage state is unsupported")
        complete_expected = (
            totals["reported_total"] is not None
            and returned == totals["reported_total"]
            and not data["next_cursor_present"]
        )
        truncated_expected = (
            totals["reported_total"] is not None
            and returned < totals["reported_total"]
            and not data["next_cursor_present"]
        )
        if totals["reported_total"] is not None and returned > totals["reported_total"]:
            raise ModelError("receipt provenance collection returned_count exceeds reported_total")
        if (
            data["complete"] != complete_expected
            or data["truncated_without_cursor"] != truncated_expected
        ):
            raise ModelError("receipt provenance collection audit is internally inconsistent")
        expected_state = (
            "COMPLETE_REPORTED_DENOMINATOR"
            if complete_expected
            else "TRUNCATED_WITHOUT_CURSOR"
            if truncated_expected
            else "CURSOR_CONTRADICTS_REPORTED_DENOMINATOR"
            if (
                data["next_cursor_present"]
                and totals["reported_total"] is not None
                and returned == totals["reported_total"]
            )
            else "PAGE_PARTIAL_CURSOR_AVAILABLE"
            if data["next_cursor_present"]
            else "COMPLETENESS_UNRESOLVED"
        )
        if state != expected_state:
            raise ModelError("receipt provenance collection coverage state is inconsistent")
        return {
            "complete": data["complete"],
            "coverage_state": state,
            "next_cursor_present": data["next_cursor_present"],
            "reported_total": totals["reported_total"],
            "requested_limit": totals["requested_limit"],
            "returned_count": returned,
            "truncated_without_cursor": data["truncated_without_cursor"],
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "arguments_sha256": self.arguments_sha256,
            "artifact_kind": self.artifact_kind,
            "byte_count": self.byte_count,
            "capture_method": self.capture_method,
            "captured_at": self.captured_at,
            "collection_audit": self.collection_audit,
            "content_sha256": self.content_sha256,
            "coordinate_convention": self.coordinate_convention,
            "format_version": self.format_version,
            "reference_accession": self.reference_accession,
            "schema_version": self.schema_version,
            "source_checksum_sha256": self.source_checksum_sha256,
            "source_record": self.source_record,
            "source_release": self.source_release,
            "tool_name": self.tool_name,
            "tool_version": self.tool_version,
        }


@dataclass(frozen=True)
class Receipt:
    id: str
    kind: ReceiptKind
    subject_sha256: str
    artifact_sha256: str
    issuer: str
    verification_state: VerificationState
    provenance: ReceiptProvenance | None

    @classmethod
    def from_dict(cls, raw: Any) -> Receipt:
        if type(raw) is not dict:
            raise ModelError("receipt must be an object")
        allowed = {
            "id",
            "kind",
            "subject_sha256",
            "artifact_sha256",
            "issuer",
            "verification_state",
            "provenance",
        }
        if not set(raw) <= allowed or not (allowed - {"provenance"}) <= set(raw):
            raise ModelError("receipt fields mismatch")
        data = dict(raw)
        provenance_raw = data.get("provenance")
        provenance = None if provenance_raw is None else ReceiptProvenance.from_dict(provenance_raw)
        result = cls(
            _identifier(data["id"], "receipt.id"),
            _enum(data["kind"], ReceiptKind, "receipt.kind"),
            _sha(data["subject_sha256"], "receipt.subject_sha256"),
            _sha(data["artifact_sha256"], "receipt.artifact_sha256"),
            _string(data["issuer"], "receipt.issuer", maximum=240),
            _enum(data["verification_state"], VerificationState, "receipt.verification_state"),
            provenance,
        )
        if provenance is not None and result.artifact_sha256 != sha256_json(provenance):
            raise ModelError("receipt artifact digest does not bind its provenance envelope")
        if (
            provenance is not None
            and provenance.capture_method == "TOOLUNIVERSE_CALL"
            and result.kind is not ReceiptKind.SOURCE
        ):
            raise ModelError("TOOLUNIVERSE_CALL provenance requires a SOURCE receipt")
        if provenance is not None and provenance.capture_method == "LOCAL_ARTIFACT":
            allowed = _LOCAL_ARTIFACT_RECEIPT_KINDS[provenance.artifact_kind]
            if result.kind not in allowed:
                raise ModelError(
                    "LOCAL_ARTIFACT provenance kind cannot support the requested receipt kind"
                )
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "subject_sha256": self.subject_sha256,
            "artifact_sha256": self.artifact_sha256,
            "issuer": self.issuer,
            "provenance": None if self.provenance is None else self.provenance.to_dict(),
            "verification_state": self.verification_state.value,
        }


@dataclass(frozen=True)
class Material:
    id: str
    kind: MaterialKind
    biological_origin_id: str | None
    parent_material_id: str | None
    independence_state: IndependenceState
    receipt_ids: tuple[str, ...]

    @classmethod
    def from_dict(cls, raw: Any) -> Material:
        data = _object(
            raw,
            {
                "id",
                "kind",
                "biological_origin_id",
                "parent_material_id",
                "independence_state",
                "receipt_ids",
            },
            "material",
        )
        origin = _optional_string(
            data["biological_origin_id"], "material.biological_origin_id", maximum=128
        )
        if origin is not None and not _ID_RE.fullmatch(origin):
            raise ModelError("material.biological_origin_id is not a valid opaque identifier")
        parent = data["parent_material_id"]
        return cls(
            _identifier(data["id"], "material.id"),
            _enum(data["kind"], MaterialKind, "material.kind"),
            origin,
            None if parent is None else _identifier(parent, "material.parent_material_id"),
            _enum(data["independence_state"], IndependenceState, "material.independence_state"),
            _ids(data["receipt_ids"], "material.receipt_ids"),
        )

    def subject_dict(self) -> dict[str, Any]:
        return {
            "biological_origin_id": self.biological_origin_id,
            "id": self.id,
            "independence_state": self.independence_state.value,
            "kind": self.kind.value,
            "parent_material_id": self.parent_material_id,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.subject_dict(), "receipt_ids": sorted(self.receipt_ids)}


@dataclass(frozen=True)
class Assembly:
    id: str
    material_id: str
    release: str
    phasing_state: PhasingState
    completeness_state: CompletenessState
    receipt_ids: tuple[str, ...]

    @classmethod
    def from_dict(cls, raw: Any) -> Assembly:
        data = _object(
            raw,
            {"id", "material_id", "release", "phasing_state", "completeness_state", "receipt_ids"},
            "assembly",
        )
        return cls(
            _identifier(data["id"], "assembly.id"),
            _identifier(data["material_id"], "assembly.material_id"),
            _string(data["release"], "assembly.release", maximum=128),
            _enum(data["phasing_state"], PhasingState, "assembly.phasing_state"),
            _enum(data["completeness_state"], CompletenessState, "assembly.completeness_state"),
            _ids(data["receipt_ids"], "assembly.receipt_ids"),
        )

    def subject_dict(self) -> dict[str, Any]:
        return {
            "completeness_state": self.completeness_state.value,
            "id": self.id,
            "material_id": self.material_id,
            "phasing_state": self.phasing_state.value,
            "release": self.release,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.subject_dict(), "receipt_ids": sorted(self.receipt_ids)}


@dataclass(frozen=True)
class SequenceObject:
    id: str
    kind: SequenceKind
    sequence_sha256: str
    length: int
    alphabet: Alphabet
    assembly_id: str | None
    material_id: str | None
    source_record: str
    source_version: str
    receipt_ids: tuple[str, ...]

    @classmethod
    def from_dict(cls, raw: Any) -> SequenceObject:
        data = _object(
            raw,
            {
                "id",
                "kind",
                "sequence_sha256",
                "length",
                "alphabet",
                "assembly_id",
                "material_id",
                "source_record",
                "source_version",
                "receipt_ids",
            },
            "sequence_object",
        )
        assembly_id = data["assembly_id"]
        material_id = data["material_id"]
        result = cls(
            _identifier(data["id"], "sequence_object.id"),
            _enum(data["kind"], SequenceKind, "sequence_object.kind"),
            _sha(data["sequence_sha256"], "sequence_object.sequence_sha256"),
            _integer(data["length"], "sequence_object.length", minimum=1),
            _enum(data["alphabet"], Alphabet, "sequence_object.alphabet"),
            None
            if assembly_id is None
            else _identifier(assembly_id, "sequence_object.assembly_id"),
            None
            if material_id is None
            else _identifier(material_id, "sequence_object.material_id"),
            _string(data["source_record"], "sequence_object.source_record", maximum=240),
            _string(data["source_version"], "sequence_object.source_version", maximum=128),
            _ids(data["receipt_ids"], "sequence_object.receipt_ids"),
        )
        expected = Alphabet.PROTEIN if result.kind is SequenceKind.PROTEIN else None
        if expected is not None and result.alphabet is not expected:
            raise ModelError("protein sequence objects require PROTEIN alphabet")
        if (
            result.kind in {SequenceKind.GENOMIC, SequenceKind.CDS}
            and result.alphabet is not Alphabet.DNA
        ):
            raise ModelError("GENOMIC and CDS sequence objects require DNA alphabet")
        if result.kind is SequenceKind.TRANSCRIPT and result.alphabet not in {
            Alphabet.DNA,
            Alphabet.RNA,
        }:
            raise ModelError("TRANSCRIPT sequence objects require DNA or RNA alphabet")
        return result

    def subject_dict(self) -> dict[str, Any]:
        return {
            "alphabet": self.alphabet.value,
            "assembly_id": self.assembly_id,
            "id": self.id,
            "kind": self.kind.value,
            "length": self.length,
            "material_id": self.material_id,
            "sequence_sha256": self.sequence_sha256,
            "source_record": self.source_record,
            "source_version": self.source_version,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.subject_dict(), "receipt_ids": sorted(self.receipt_ids)}


@dataclass(frozen=True)
class LocusModel:
    id: str
    assembly_id: str
    contig: str
    start: int
    end: int
    strand: Strand
    model_version: str
    sequence_object_ids: tuple[str, ...]
    receipt_ids: tuple[str, ...]

    @classmethod
    def from_dict(cls, raw: Any) -> LocusModel:
        data = _object(
            raw,
            {
                "id",
                "assembly_id",
                "contig",
                "start",
                "end",
                "strand",
                "model_version",
                "sequence_object_ids",
                "receipt_ids",
            },
            "locus_model",
        )
        start = _integer(data["start"], "locus_model.start", minimum=1)
        end = _integer(data["end"], "locus_model.end", minimum=1)
        if end < start:
            raise ModelError("LOCUS_COORDINATE_INVALID: locus_model.end is before start")
        return cls(
            _identifier(data["id"], "locus_model.id"),
            _identifier(data["assembly_id"], "locus_model.assembly_id"),
            _string(data["contig"], "locus_model.contig", maximum=240),
            start,
            end,
            _enum(data["strand"], Strand, "locus_model.strand"),
            _string(data["model_version"], "locus_model.model_version", maximum=128),
            _ids(
                data["sequence_object_ids"],
                "locus_model.sequence_object_ids",
                nonempty=True,
            ),
            _ids(data["receipt_ids"], "locus_model.receipt_ids"),
        )

    def subject_dict(self) -> dict[str, Any]:
        return {
            "assembly_id": self.assembly_id,
            "contig": self.contig,
            "end": self.end,
            "id": self.id,
            "model_version": self.model_version,
            "sequence_object_ids": sorted(self.sequence_object_ids),
            "start": self.start,
            "strand": self.strand.value,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.subject_dict(), "receipt_ids": sorted(self.receipt_ids)}


@dataclass(frozen=True)
class TranscriptModel:
    id: str
    locus_model_id: str
    transcript_sequence_id: str
    cds_sequence_id: str | None
    protein_sequence_id: str | None
    isoform_label: str
    model_version: str
    receipt_ids: tuple[str, ...]

    @classmethod
    def from_dict(cls, raw: Any) -> TranscriptModel:
        data = _object(
            raw,
            {
                "id",
                "locus_model_id",
                "transcript_sequence_id",
                "cds_sequence_id",
                "protein_sequence_id",
                "isoform_label",
                "model_version",
                "receipt_ids",
            },
            "transcript_model",
        )
        cds = data["cds_sequence_id"]
        protein = data["protein_sequence_id"]
        return cls(
            _identifier(data["id"], "transcript_model.id"),
            _identifier(data["locus_model_id"], "transcript_model.locus_model_id"),
            _identifier(data["transcript_sequence_id"], "transcript_model.transcript_sequence_id"),
            None if cds is None else _identifier(cds, "transcript_model.cds_sequence_id"),
            None
            if protein is None
            else _identifier(protein, "transcript_model.protein_sequence_id"),
            _string(data["isoform_label"], "transcript_model.isoform_label", maximum=128),
            _string(data["model_version"], "transcript_model.model_version", maximum=128),
            _ids(data["receipt_ids"], "transcript_model.receipt_ids"),
        )

    def subject_dict(self) -> dict[str, Any]:
        return {
            "cds_sequence_id": self.cds_sequence_id,
            "id": self.id,
            "isoform_label": self.isoform_label,
            "locus_model_id": self.locus_model_id,
            "model_version": self.model_version,
            "protein_sequence_id": self.protein_sequence_id,
            "transcript_sequence_id": self.transcript_sequence_id,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.subject_dict(), "receipt_ids": sorted(self.receipt_ids)}


@dataclass(frozen=True)
class Observation:
    id: str
    kind: ObservationKind
    subject_id: str
    object_id: str | None
    result_state: ResultState
    evidence_sha256: str
    receipt_ids: tuple[str, ...]

    @classmethod
    def from_dict(cls, raw: Any) -> Observation:
        data = _object(
            raw,
            {
                "id",
                "kind",
                "subject_id",
                "object_id",
                "result_state",
                "evidence_sha256",
                "receipt_ids",
            },
            "observation",
        )
        object_id = data["object_id"]
        return cls(
            _identifier(data["id"], "observation.id"),
            _enum(data["kind"], ObservationKind, "observation.kind"),
            _identifier(data["subject_id"], "observation.subject_id"),
            None if object_id is None else _identifier(object_id, "observation.object_id"),
            _enum(data["result_state"], ResultState, "observation.result_state"),
            _sha(data["evidence_sha256"], "observation.evidence_sha256"),
            _ids(data["receipt_ids"], "observation.receipt_ids"),
        )

    def subject_dict(self) -> dict[str, Any]:
        return {
            "evidence_sha256": self.evidence_sha256,
            "id": self.id,
            "kind": self.kind.value,
            "object_id": self.object_id,
            "result_state": self.result_state.value,
            "subject_id": self.subject_id,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.subject_dict(), "receipt_ids": sorted(self.receipt_ids)}


@dataclass(frozen=True)
class CorrespondenceClaim:
    id: str
    left_id: str
    right_id: str
    relation: CorrespondenceRelation
    declared_state: CorrespondenceState
    observation_ids: tuple[str, ...]
    receipt_ids: tuple[str, ...]

    @classmethod
    def from_dict(cls, raw: Any) -> CorrespondenceClaim:
        data = _object(
            raw,
            {
                "id",
                "left_id",
                "right_id",
                "relation",
                "declared_state",
                "observation_ids",
                "receipt_ids",
            },
            "correspondence_claim",
        )
        left = _identifier(data["left_id"], "correspondence_claim.left_id")
        right = _identifier(data["right_id"], "correspondence_claim.right_id")
        if left == right:
            raise ModelError("correspondence claim endpoints must be distinct")
        return cls(
            _identifier(data["id"], "correspondence_claim.id"),
            left,
            right,
            _enum(data["relation"], CorrespondenceRelation, "correspondence_claim.relation"),
            _enum(
                data["declared_state"], CorrespondenceState, "correspondence_claim.declared_state"
            ),
            _ids(data["observation_ids"], "correspondence_claim.observation_ids", nonempty=False),
            _ids(data["receipt_ids"], "correspondence_claim.receipt_ids"),
        )

    def subject_dict(self) -> dict[str, Any]:
        return {
            "declared_state": self.declared_state.value,
            "id": self.id,
            "left_id": self.left_id,
            "observation_ids": sorted(self.observation_ids),
            "relation": self.relation.value,
            "right_id": self.right_id,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.subject_dict(), "receipt_ids": sorted(self.receipt_ids)}


ReceiptSubject = (
    Material
    | Assembly
    | SequenceObject
    | LocusModel
    | TranscriptModel
    | Observation
    | CorrespondenceClaim
)


def receipt_subject_sha256(value: ReceiptSubject) -> str:
    """Digest the closed subject projection to which a receipt must bind."""

    return sha256_json(value.subject_dict())


@dataclass(frozen=True)
class StudySpec:
    schema_version: str
    study_id: str
    species: str
    bound_memory_head_sha256: str | None
    receipts: tuple[Receipt, ...]
    materials: tuple[Material, ...]
    assemblies: tuple[Assembly, ...]
    sequence_objects: tuple[SequenceObject, ...]
    locus_models: tuple[LocusModel, ...]
    transcript_models: tuple[TranscriptModel, ...]
    observations: tuple[Observation, ...]
    correspondence_claims: tuple[CorrespondenceClaim, ...]
    target_selection: TargetSelection

    @classmethod
    def from_dict(cls, raw: Any) -> StudySpec:
        data = _object(
            raw,
            {
                "schema_version",
                "study_id",
                "species",
                "bound_memory_head_sha256",
                "receipts",
                "materials",
                "assemblies",
                "sequence_objects",
                "locus_models",
                "transcript_models",
                "observations",
                "correspondence_claims",
                "target_selection",
            },
            "study",
        )
        if data["schema_version"] != "phytolocus.study.v0.1":
            raise ModelError("unsupported study.schema_version")
        cls._validate_raw_reference_budget(data)
        result = cls(
            data["schema_version"],
            _identifier(data["study_id"], "study.study_id"),
            _string(data["species"], "study.species", maximum=240),
            _optional_sha(data["bound_memory_head_sha256"], "study.bound_memory_head_sha256"),
            tuple(Receipt.from_dict(item) for item in _array(data["receipts"], "study.receipts")),
            tuple(
                Material.from_dict(item) for item in _array(data["materials"], "study.materials")
            ),
            tuple(
                Assembly.from_dict(item) for item in _array(data["assemblies"], "study.assemblies")
            ),
            tuple(
                SequenceObject.from_dict(item)
                for item in _array(data["sequence_objects"], "study.sequence_objects")
            ),
            tuple(
                LocusModel.from_dict(item)
                for item in _array(data["locus_models"], "study.locus_models")
            ),
            tuple(
                TranscriptModel.from_dict(item)
                for item in _array(data["transcript_models"], "study.transcript_models")
            ),
            tuple(
                Observation.from_dict(item)
                for item in _array(data["observations"], "study.observations")
            ),
            tuple(
                CorrespondenceClaim.from_dict(item)
                for item in _array(data["correspondence_claims"], "study.correspondence_claims")
            ),
            TargetSelection.from_dict(data["target_selection"]),
        )
        result.validate()
        return result

    def validate(self) -> None:
        record_count = sum(
            len(items)
            for items in (
                self.receipts,
                self.materials,
                self.assemblies,
                self.sequence_objects,
                self.locus_models,
                self.transcript_models,
                self.observations,
                self.correspondence_claims,
            )
        )
        if record_count > MAX_STUDY_RECORDS:
            raise ModelError(
                f"study exceeds the {MAX_STUDY_RECORDS}-record aggregate collection limit"
            )
        reference_count = (
            sum(
                len(item.receipt_ids)
                for items in (
                    self.materials,
                    self.assemblies,
                    self.sequence_objects,
                    self.locus_models,
                    self.transcript_models,
                    self.observations,
                    self.correspondence_claims,
                )
                for item in items
            )
            + sum(len(item.sequence_object_ids) for item in self.locus_models)
            + sum(len(item.observation_ids) for item in self.correspondence_claims)
        )
        if reference_count > MAX_STUDY_REFERENCES:
            raise ModelError(f"study exceeds the {MAX_STUDY_REFERENCES}-reference aggregate limit")
        for items, label in (
            (self.receipts, "receipt"),
            (self.materials, "material"),
            (self.assemblies, "assembly"),
            (self.sequence_objects, "sequence_object"),
            (self.locus_models, "locus_model"),
            (self.transcript_models, "transcript_model"),
            (self.observations, "observation"),
            (self.correspondence_claims, "correspondence_claim"),
        ):
            _unique(items, "id", label)

        graph_objects: tuple[Any, ...] = (
            *self.materials,
            *self.assemblies,
            *self.sequence_objects,
            *self.locus_models,
            *self.transcript_models,
        )
        graph_ids = [item.id for item in graph_objects]
        if len(graph_ids) != len(set(graph_ids)):
            raise ModelError("graph object identifiers must be globally unique")
        if set(graph_ids) & {item.id for item in self.observations}:
            raise ModelError("observation identifiers must not collide with graph objects")
        if set(graph_ids) & {item.id for item in self.correspondence_claims}:
            raise ModelError("claim identifiers must not collide with graph objects")

        receipt_map = {item.id: item for item in self.receipts}
        material_map = {item.id: item for item in self.materials}
        assembly_map = {item.id: item for item in self.assemblies}
        sequence_map = {item.id: item for item in self.sequence_objects}
        locus_map = {item.id: item for item in self.locus_models}
        transcript_map = {item.id: item for item in self.transcript_models}
        observation_map = {item.id: item for item in self.observations}
        object_map = {item.id: item for item in graph_objects}

        for material in self.materials:
            if (
                material.parent_material_id is not None
                and material.parent_material_id not in material_map
            ):
                raise ModelError(f"material {material.id} has an unresolved parent")
            if material.parent_material_id == material.id:
                raise ModelError("MATERIAL_ORIGIN_CYCLE: material cannot parent itself")
            if material.parent_material_id is not None:
                parent = material_map[material.parent_material_id]
                if (
                    material.independence_state is IndependenceState.SAME_ORIGIN
                    and material.biological_origin_id is not None
                    and parent.biological_origin_id is not None
                    and material.biological_origin_id != parent.biological_origin_id
                ):
                    raise ModelError(
                        "MATERIAL_ORIGIN_CONFLICT: SAME_ORIGIN child and parent have "
                        "different biological_origin_id values"
                    )
        self._validate_material_cycles(material_map)
        material_components = self._material_components(material_map)
        component_labels: dict[str, set[str]] = {}
        for material in self.materials:
            if material.biological_origin_id is not None:
                component_labels.setdefault(material_components[material.id], set()).add(
                    material.biological_origin_id
                )
        if any(len(labels) > 1 for labels in component_labels.values()):
            raise ModelError(
                "MATERIAL_ORIGIN_CONFLICT: one SAME_ORIGIN component has conflicting "
                "biological_origin_id values"
            )
        distinct_by_component: dict[str, list[str]] = {}
        for material in self.materials:
            if material.independence_state is IndependenceState.DISTINCT_DECLARED:
                distinct_by_component.setdefault(material_components[material.id], []).append(
                    material.id
                )
        if any(len(material_ids) > 1 for material_ids in distinct_by_component.values()):
            raise ModelError(
                "MATERIAL_ORIGIN_CONFLICT: one biological-origin component cannot contain "
                "multiple DISTINCT_DECLARED materials"
            )

        receipt_bound_transcript_crosswalks: set[tuple[str, str]] = set()
        for claim in self.correspondence_claims:
            if (
                claim.relation is not CorrespondenceRelation.TRANSCRIPT_OF
                or not claim.receipt_ids
                or claim.declared_state
                not in {CorrespondenceState.EXACT, CorrespondenceState.SUPPORTED}
            ):
                continue
            for observation_id in claim.observation_ids:
                observation = observation_map.get(observation_id)
                if (
                    observation is not None
                    and observation.subject_id == claim.left_id
                    and observation.object_id == claim.right_id
                    and observation.kind is ObservationKind.MODEL_CROSSWALK
                    and observation.result_state is ResultState.POSITIVE
                    and observation.receipt_ids
                ):
                    receipt_bound_transcript_crosswalks.add((claim.left_id, claim.right_id))

        for assembly in self.assemblies:
            if assembly.material_id not in material_map:
                raise ModelError(f"ASSEMBLY_MATERIAL_UNBOUND: {assembly.id}")

        for sequence in self.sequence_objects:
            if sequence.assembly_id is None and sequence.material_id is None:
                raise ModelError(f"SEQUENCE_CONTEXT_UNBOUND: {sequence.id}")
            if sequence.assembly_id is not None and sequence.assembly_id not in assembly_map:
                raise ModelError(f"sequence {sequence.id} has an unresolved assembly")
            if sequence.material_id is not None and sequence.material_id not in material_map:
                raise ModelError(f"sequence {sequence.id} has an unresolved material")
            if (
                sequence.assembly_id is not None
                and sequence.material_id is not None
                and assembly_map[sequence.assembly_id].material_id != sequence.material_id
            ):
                raise ModelError(f"sequence {sequence.id} crosses assembly and material identities")

        for locus in self.locus_models:
            if locus.assembly_id not in assembly_map:
                raise ModelError(f"locus {locus.id} has an unresolved assembly")
            span = locus.end - locus.start + 1
            full_span_identities: set[tuple[str, int, str]] = set()
            for sequence_id in locus.sequence_object_ids:
                sequence = sequence_map.get(sequence_id)
                if sequence is None:
                    raise ModelError(f"locus {locus.id} has an unresolved sequence")
                if sequence.kind is not SequenceKind.GENOMIC:
                    raise ModelError(f"MODEL_SEQUENCE_KIND_MISMATCH: locus {locus.id}")
                if sequence.assembly_id != locus.assembly_id:
                    raise ModelError(f"locus {locus.id} crosses assembly sequence identity")
                record_scopes = {
                    sequence.source_record,
                    f"{sequence.source_record}.{sequence.source_version}",
                }
                if locus.contig not in record_scopes:
                    raise ModelError(
                        f"LOCUS_RECORD_SCOPE_MISMATCH: {locus.id} contig is not the "
                        "referenced genomic sequence record"
                    )
                if sequence.length == span:
                    full_span_identities.add(
                        (sequence.sequence_sha256, sequence.length, sequence.alphabet.value)
                    )
            if len(full_span_identities) > 1:
                raise ModelError(
                    f"SEQUENCE_HASH_CONFLICT: locus {locus.id} has incompatible full-span "
                    "genomic sequence identities"
                )

        for transcript in self.transcript_models:
            if transcript.locus_model_id not in locus_map:
                raise ModelError(f"TRANSCRIPT_LOCUS_UNBOUND: {transcript.id}")
            locus = locus_map[transcript.locus_model_id]
            scope_crosswalk_bound = (
                transcript.id,
                transcript.locus_model_id,
            ) in receipt_bound_transcript_crosswalks
            transcript_sequence = self._require_sequence_kind(
                sequence_map,
                transcript.transcript_sequence_id,
                SequenceKind.TRANSCRIPT,
                transcript.id,
            )
            self._require_model_sequence_context(
                transcript_sequence,
                locus,
                assembly_map,
                material_components,
                transcript.id,
                scope_crosswalk_bound=scope_crosswalk_bound,
            )
            if transcript.cds_sequence_id is not None:
                cds_sequence = self._require_sequence_kind(
                    sequence_map, transcript.cds_sequence_id, SequenceKind.CDS, transcript.id
                )
                self._require_model_sequence_context(
                    cds_sequence,
                    locus,
                    assembly_map,
                    material_components,
                    transcript.id,
                    scope_crosswalk_bound=scope_crosswalk_bound,
                )
            if transcript.protein_sequence_id is not None:
                protein_sequence = self._require_sequence_kind(
                    sequence_map,
                    transcript.protein_sequence_id,
                    SequenceKind.PROTEIN,
                    transcript.id,
                )
                self._require_model_sequence_context(
                    protein_sequence,
                    locus,
                    assembly_map,
                    material_components,
                    transcript.id,
                    scope_crosswalk_bound=scope_crosswalk_bound,
                )
        for observation in self.observations:
            if observation.subject_id not in object_map:
                raise ModelError(f"observation {observation.id} has an unresolved subject")
            if observation.object_id is not None and observation.object_id not in object_map:
                raise ModelError(f"observation {observation.id} has an unresolved object")

        for claim in self.correspondence_claims:
            if claim.left_id not in object_map or claim.right_id not in object_map:
                raise ModelError(f"claim {claim.id} has an unresolved endpoint")
            for observation_id in claim.observation_ids:
                if observation_id not in observation_map:
                    raise ModelError(f"claim {claim.id} has an unresolved observation")
            self._validate_claim_types(
                claim,
                object_map,
                sequence_map,
                locus_map,
                transcript_map,
                material_map,
                assembly_map,
                material_components,
            )
            self._validate_claim_observations(claim, observation_map)

        used_receipts: set[str] = set()
        for item in (
            *self.materials,
            *self.assemblies,
            *self.sequence_objects,
            *self.locus_models,
            *self.transcript_models,
            *self.observations,
            *self.correspondence_claims,
        ):
            expected_kind = self._expected_receipt_kind(item)
            expected_subject = receipt_subject_sha256(item)
            for receipt_id in item.receipt_ids:
                receipt = receipt_map.get(receipt_id)
                if receipt is None:
                    raise ModelError(f"{item.id} has an unresolved receipt")
                if receipt.kind not in {ReceiptKind.SOURCE, expected_kind}:
                    raise ModelError(f"receipt {receipt.id} has the wrong kind for {item.id}")
                if receipt.subject_sha256 != expected_subject:
                    raise ModelError(f"receipt {receipt.id} subject digest does not bind {item.id}")
                used_receipts.add(receipt_id)
        unbound = sorted(set(receipt_map) - used_receipts)
        if unbound:
            raise ModelError(f"unbound receipts are prohibited: {unbound}")

    @staticmethod
    def _validate_raw_reference_budget(data: Mapping[str, Any]) -> None:
        reference_fields = {
            "materials": ("receipt_ids",),
            "assemblies": ("receipt_ids",),
            "sequence_objects": ("receipt_ids",),
            "locus_models": ("receipt_ids", "sequence_object_ids"),
            "transcript_models": ("receipt_ids",),
            "observations": ("receipt_ids",),
            "correspondence_claims": ("observation_ids", "receipt_ids"),
        }
        total = 0
        for collection, fields in reference_fields.items():
            for item in _array(data[collection], f"study.{collection}"):
                if type(item) is not dict:
                    continue
                for field in fields:
                    values = item.get(field)
                    if isinstance(values, list):
                        total += len(values)
                        if total > MAX_STUDY_REFERENCES:
                            raise ModelError(
                                f"study exceeds the {MAX_STUDY_REFERENCES}-reference "
                                "aggregate limit"
                            )

    @staticmethod
    def _validate_material_cycles(material_map: Mapping[str, Material]) -> None:
        for start in material_map:
            seen: set[str] = set()
            current: str | None = start
            while current is not None:
                if current in seen:
                    raise ModelError(
                        "MATERIAL_ORIGIN_CYCLE: parent cycle detected in material relation"
                    )
                seen.add(current)
                current = material_map[current].parent_material_id

    @staticmethod
    def _material_components(material_map: Mapping[str, Material]) -> dict[str, str]:
        parent = {material_id: material_id for material_id in material_map}

        def find(material_id: str) -> str:
            while parent[material_id] != material_id:
                parent[material_id] = parent[parent[material_id]]
                material_id = parent[material_id]
            return material_id

        def union(left: str, right: str) -> None:
            left_root, right_root = find(left), find(right)
            if left_root == right_root:
                return
            low, high = sorted((left_root, right_root))
            parent[high] = low

        by_origin: dict[str, str] = {}
        for material in material_map.values():
            if material.biological_origin_id is not None:
                prior = by_origin.setdefault(material.biological_origin_id, material.id)
                union(prior, material.id)
            if (
                material.parent_material_id is not None
                and material.independence_state is IndependenceState.SAME_ORIGIN
            ):
                union(material.id, material.parent_material_id)
        return {material_id: find(material_id) for material_id in material_map}

    @staticmethod
    def _require_sequence_kind(
        sequence_map: Mapping[str, SequenceObject],
        sequence_id: str,
        expected: SequenceKind,
        model_id: str,
    ) -> SequenceObject:
        sequence = sequence_map.get(sequence_id)
        if sequence is None or sequence.kind is not expected:
            raise ModelError(f"MODEL_SEQUENCE_KIND_MISMATCH: {model_id} requires {expected.value}")
        return sequence

    @staticmethod
    def _require_model_sequence_context(
        sequence: SequenceObject,
        locus: LocusModel,
        assembly_map: Mapping[str, Assembly],
        material_components: Mapping[str, str],
        model_id: str,
        *,
        scope_crosswalk_bound: bool,
    ) -> None:
        locus_material_id = assembly_map[locus.assembly_id].material_id
        sequence_material_ids = []
        if sequence.assembly_id is not None:
            sequence_material_ids.append(assembly_map[sequence.assembly_id].material_id)
        if sequence.material_id is not None:
            sequence_material_ids.append(sequence.material_id)
        if any(
            material_components[item] != material_components[locus_material_id]
            for item in sequence_material_ids
        ):
            raise ModelError(
                f"TRANSCRIPT_CONTEXT_CONFLICT: {model_id} sequence crosses locus "
                "biological-origin component"
            )
        if (
            sequence.assembly_id is not None
            and sequence.assembly_id != locus.assembly_id
            and not scope_crosswalk_bound
        ):
            raise ModelError(
                f"TRANSCRIPT_ASSEMBLY_SCOPE_UNRESOLVED: {model_id} sequence declares "
                "a different assembly without a receipt-bound positive crosswalk"
            )

    @staticmethod
    def _validate_claim_types(
        claim: CorrespondenceClaim,
        object_map: Mapping[str, Any],
        sequence_map: Mapping[str, SequenceObject],
        locus_map: Mapping[str, LocusModel],
        transcript_map: Mapping[str, TranscriptModel],
        material_map: Mapping[str, Material],
        assembly_map: Mapping[str, Assembly],
        material_components: Mapping[str, str],
    ) -> None:
        left = object_map[claim.left_id]
        right = object_map[claim.right_id]
        relation = claim.relation
        valid = True
        if relation is CorrespondenceRelation.SAME_SEQUENCE:
            valid = claim.left_id in sequence_map and claim.right_id in sequence_map
        elif relation in {CorrespondenceRelation.SAME_LOCUS, CorrespondenceRelation.ALLELE_OF}:
            valid = claim.left_id in locus_map and claim.right_id in locus_map
        elif relation is CorrespondenceRelation.HAPLOTYPE_OF:
            valid = (
                (
                    isinstance(left, LocusModel)
                    or (isinstance(left, SequenceObject) and left.kind is SequenceKind.GENOMIC)
                    or (
                        isinstance(left, Material)
                        and left.kind is MaterialKind.HAPLOTYPE_REPRESENTATION
                    )
                )
                and isinstance(right, Material)
                and right.kind in {MaterialKind.STOCK, MaterialKind.SAMPLE, MaterialKind.INDIVIDUAL}
            )
            if valid:
                if isinstance(left, Material):
                    left_material_id = left.id
                elif isinstance(left, LocusModel):
                    left_material_id = assembly_map[left.assembly_id].material_id
                else:
                    left_material_id = left.material_id
                    if left_material_id is None and left.assembly_id is not None:
                        left_material_id = assembly_map[left.assembly_id].material_id
                valid = (
                    left_material_id is not None
                    and material_components[left_material_id] == material_components[right.id]
                )
        elif relation is CorrespondenceRelation.TRANSCRIPT_OF:
            valid = (
                claim.left_id in transcript_map
                and claim.right_id in locus_map
                and transcript_map[claim.left_id].locus_model_id == claim.right_id
            )
        elif relation is CorrespondenceRelation.PROTEIN_OF:
            valid = (
                isinstance(left, SequenceObject)
                and left.kind is SequenceKind.PROTEIN
                and claim.right_id in transcript_map
                and transcript_map[claim.right_id].protein_sequence_id == claim.left_id
            )
        elif relation is CorrespondenceRelation.SUPERSEDES:
            valid = type(left) is type(right)
        elif relation is CorrespondenceRelation.REPRESENTS:
            valid = type(left) is type(right)
            if valid and isinstance(left, SequenceObject):
                valid = left.kind is right.kind and left.alphabet is right.alphabet
        if not valid:
            raise ModelError(f"CORRESPONDENCE_SCOPE_OR_TYPE_INVALID: {claim.id}")

    @staticmethod
    def _validate_claim_observations(
        claim: CorrespondenceClaim,
        observation_map: Mapping[str, Observation],
    ) -> None:
        allowed_kinds = {
            CorrespondenceRelation.SAME_SEQUENCE: {
                ObservationKind.ALIGNMENT,
                ObservationKind.MODEL_CROSSWALK,
            },
            CorrespondenceRelation.SAME_LOCUS: {
                ObservationKind.ALIGNMENT,
                ObservationKind.MODEL_CROSSWALK,
            },
            CorrespondenceRelation.ALLELE_OF: {
                ObservationKind.ALIGNMENT,
                ObservationKind.MODEL_CROSSWALK,
                ObservationKind.PHASE,
            },
            CorrespondenceRelation.HAPLOTYPE_OF: {
                ObservationKind.ALIGNMENT,
                ObservationKind.MODEL_CROSSWALK,
                ObservationKind.PHASE,
            },
            CorrespondenceRelation.TRANSCRIPT_OF: {
                ObservationKind.MODEL_CROSSWALK,
                ObservationKind.TRANSCRIPT_SUPPORT,
            },
            CorrespondenceRelation.PROTEIN_OF: {
                ObservationKind.MODEL_CROSSWALK,
                ObservationKind.TRANSCRIPT_SUPPORT,
            },
            CorrespondenceRelation.SUPERSEDES: {ObservationKind.MODEL_CROSSWALK},
            CorrespondenceRelation.REPRESENTS: {
                ObservationKind.ALIGNMENT,
                ObservationKind.MODEL_CROSSWALK,
            },
        }
        for observation_id in claim.observation_ids:
            observation = observation_map[observation_id]
            if (
                observation.subject_id != claim.left_id
                or observation.object_id != claim.right_id
                or observation.kind not in allowed_kinds[claim.relation]
            ):
                raise ModelError(
                    f"CORRESPONDENCE_OBSERVATION_SCOPE_INVALID: {claim.id} is not "
                    "supported by an endpoint- and relation-bound observation"
                )

    @staticmethod
    def _expected_receipt_kind(item: ReceiptSubject) -> ReceiptKind:
        if isinstance(item, Material):
            return ReceiptKind.MATERIAL
        if isinstance(item, Assembly):
            return ReceiptKind.ASSEMBLY
        if isinstance(item, SequenceObject):
            return ReceiptKind.SEQUENCE
        if isinstance(item, (LocusModel, TranscriptModel)):
            return ReceiptKind.ANNOTATION
        if isinstance(item, Observation):
            return ReceiptKind.OBSERVATION
        return ReceiptKind.CORRESPONDENCE

    def to_dict(self) -> dict[str, Any]:
        return {
            "assemblies": [
                item.to_dict() for item in sorted(self.assemblies, key=lambda item: item.id)
            ],
            "bound_memory_head_sha256": self.bound_memory_head_sha256,
            "correspondence_claims": [
                item.to_dict()
                for item in sorted(self.correspondence_claims, key=lambda item: item.id)
            ],
            "locus_models": [
                item.to_dict() for item in sorted(self.locus_models, key=lambda item: item.id)
            ],
            "materials": [
                item.to_dict() for item in sorted(self.materials, key=lambda item: item.id)
            ],
            "observations": [
                item.to_dict() for item in sorted(self.observations, key=lambda item: item.id)
            ],
            "receipts": [
                item.to_dict() for item in sorted(self.receipts, key=lambda item: item.id)
            ],
            "schema_version": self.schema_version,
            "sequence_objects": [
                item.to_dict() for item in sorted(self.sequence_objects, key=lambda item: item.id)
            ],
            "species": self.species,
            "study_id": self.study_id,
            "target_selection": self.target_selection.to_dict(),
            "transcript_models": [
                item.to_dict() for item in sorted(self.transcript_models, key=lambda item: item.id)
            ],
        }


# Public aliases keep the core API concise while preserving the explicit model name.
Study = StudySpec
ValidationError = ModelError
