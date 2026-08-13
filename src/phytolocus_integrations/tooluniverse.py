"""ToolUniverse 1.4.1 transport adapter and source-contract diagnostics."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

TOOLUNIVERSE_PIN = "1.4.1"
MAX_PROVENANCE_INTEGER = 9_223_372_036_854_775_807
MAX_SOURCE_JSON_BYTES = 100 * 1024 * 1024
MAX_SOURCE_JSON_NESTING = 128
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_UTC_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z\Z")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_AUDIT_FIELDS = {
    "complete",
    "coverage_state",
    "next_cursor_present",
    "reported_total",
    "requested_limit",
    "returned_count",
    "truncated_without_cursor",
}
_CALL_RECEIPT_FIELDS = {
    "arguments_sha256",
    "collection_audit",
    "response_sha256",
    "retrieved_at",
    "source_name",
    "source_release",
    "tool_name",
    "tooluniverse_version",
    "transport_success_only",
    "verification_authority",
}


def _valid_utc_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or _UTC_TIMESTAMP.fullmatch(value) is None:
        return False
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return True


def _text(value: Any, *, label: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
        or "\x00" in value
    ):
        raise SourceContractError(f"{label} must be a safe nonempty trimmed string")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise SourceContractError(f"{label} must be a Unicode scalar string") from exc
    return value


# These are existing ToolUniverse source tools, not PhytoLocus-owned authorities.
AUTHORITATIVE_SOURCE_TOOLS = {
    "assembly": (
        "NCBIDatasets_get_genome_assembly",
        "NCBIDatasets_list_genomes_by_taxon",
        "NCBIDatasets_get_sequence_reports",
    ),
    "annotation": (
        "NCBIDatasets_get_gene",
        "Ensembl_get_assembly_info",
        "Ensembl_lookup_gene_by_symbol",
        "Ensembl_get_gene_overlapping_features",
    ),
    "expression": (
        "GEO_search_rnaseq_datasets",
        "GxA_list_experiments",
    ),
    "long_reads": (
        "SRA_search_experiments",
        "NCBI_SRA_get_run_info",
        "NCBI_SRA_locate_run_files",
        "ENAPortal_search_runs_fastq",
    ),
    "orthology": ("ensembl_get_homology", "OMA_get_orthologs"),
    "variants": ("EVA_list_studies", "EVA_get_variants_by_region"),
}


class SourceContractError(ValueError):
    """A transport result cannot support the claimed source contract."""


def _canonical_bytes(value: Any) -> bytes:
    try:
        payload = (
            json.dumps(
                value,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
    except (RecursionError, TypeError, ValueError, UnicodeEncodeError) as exc:
        raise SourceContractError("source payload is not canonical JSON") from exc
    if len(payload) > MAX_SOURCE_JSON_BYTES:
        raise SourceContractError("source payload exceeds the canonical byte limit")
    depth = 0
    in_string = False
    escaped = False
    for byte in payload:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 0x5C:
                escaped = True
            elif byte == 0x22:
                in_string = False
            continue
        if byte == 0x22:
            in_string = True
        elif byte in {0x5B, 0x7B}:
            depth += 1
            if depth > MAX_SOURCE_JSON_NESTING:
                raise SourceContractError("source payload exceeds the canonical nesting limit")
        elif byte in {0x5D, 0x7D}:
            depth -= 1
    return payload


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _owned_object(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SourceContractError(f"{label} must be an object")
    try:
        payload = _canonical_bytes(dict(value))
        owned = json.loads(payload)
    except (RecursionError, TypeError, ValueError) as exc:
        raise SourceContractError(f"{label} is not a canonical JSON object") from exc
    if type(owned) is not dict:
        raise SourceContractError(f"{label} must be an object")
    return owned


def audit_collection(
    *,
    returned_count: int,
    reported_total: int | None,
    next_cursor: str | None,
    requested_limit: int | None,
) -> dict[str, Any]:
    """Classify list coverage without laundering a truncated first page as complete."""

    for field, value in (("returned_count", returned_count), ("reported_total", reported_total)):
        if value is not None and (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
            or value > MAX_PROVENANCE_INTEGER
        ):
            raise SourceContractError(
                f"{field} must be an integer in [0, {MAX_PROVENANCE_INTEGER}] or null"
            )
    if requested_limit is not None and (
        not isinstance(requested_limit, int)
        or isinstance(requested_limit, bool)
        or requested_limit < 1
        or requested_limit > MAX_PROVENANCE_INTEGER
    ):
        raise SourceContractError("requested_limit must be a positive integer or null")
    if next_cursor is not None and (not isinstance(next_cursor, str) or not next_cursor):
        raise SourceContractError("next_cursor must be a nonempty string or null")

    complete = (
        reported_total is not None and returned_count == reported_total and next_cursor is None
    )
    truncated = (
        reported_total is not None and returned_count < reported_total and next_cursor is None
    )
    if reported_total is not None and returned_count > reported_total:
        raise SourceContractError("returned_count cannot exceed reported_total")
    if complete:
        state = "COMPLETE_REPORTED_DENOMINATOR"
    elif truncated:
        state = "TRUNCATED_WITHOUT_CURSOR"
    elif next_cursor is not None:
        state = (
            "CURSOR_CONTRADICTS_REPORTED_DENOMINATOR"
            if reported_total is not None and returned_count == reported_total
            else "PAGE_PARTIAL_CURSOR_AVAILABLE"
        )
    else:
        state = "COMPLETENESS_UNRESOLVED"
    return {
        "complete": complete,
        "coverage_state": state,
        "next_cursor_present": next_cursor is not None,
        "reported_total": reported_total,
        "requested_limit": requested_limit,
        "returned_count": returned_count,
        "truncated_without_cursor": truncated,
    }


def receipt_from_call(
    *,
    tool_name: str,
    arguments: Mapping[str, Any],
    response: Mapping[str, Any],
    source_name: str,
    source_release: str | None,
    retrieved_at: str,
    collection_audit: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a path-free receipt for a completed ToolUniverse source call.

    Caller declarations are committed but not independently authenticated.  Raw
    arguments and responses are intentionally omitted from the receipt.
    """

    owned_arguments = _owned_object(arguments, label="arguments")
    owned_response = _owned_object(response, label="response")

    tool_name = _text(tool_name, label="tool_name", maximum=128)
    source_name = _text(source_name, label="source_name", maximum=240)
    if not _valid_utc_timestamp(retrieved_at):
        raise SourceContractError("retrieved_at must be a UTC RFC 3339 timestamp")
    if source_release is not None:
        source_release = _text(source_release, label="source_release", maximum=128)
    if owned_response.get("status") != "success":
        raise SourceContractError("an unsuccessful source call cannot become evidence")

    validated_audit = None
    if collection_audit is not None:
        owned_audit = _owned_object(collection_audit, label="collection_audit")
        if set(owned_audit) != _AUDIT_FIELDS:
            raise SourceContractError("collection_audit violates the closed contract")
        cursor_present = owned_audit["next_cursor_present"]
        if type(cursor_present) is not bool:
            raise SourceContractError("collection_audit next_cursor_present must be boolean")
        sentinel_cursor = "declared-present" if cursor_present else None
        validated_audit = audit_collection(
            returned_count=owned_audit["returned_count"],
            reported_total=owned_audit["reported_total"],
            next_cursor=sentinel_cursor,
            requested_limit=owned_audit["requested_limit"],
        )
        if owned_audit != validated_audit:
            raise SourceContractError("collection_audit differs from deterministic recomputation")

    tool_version = _text(
        importlib.metadata.version("tooluniverse"),
        label="tooluniverse_version",
        maximum=64,
    )
    return {
        "arguments_sha256": _digest(owned_arguments),
        "collection_audit": validated_audit,
        "response_sha256": _digest(owned_response),
        "retrieved_at": retrieved_at,
        "source_name": source_name,
        "source_release": source_release,
        "tool_name": tool_name,
        "tooluniverse_version": tool_version,
        "transport_success_only": True,
        "verification_authority": "TRANSPORT_AND_BYTE_COMMITMENT_ONLY",
    }


def study_receipt_from_call(
    call_receipt: Mapping[str, Any],
    *,
    receipt_id: str,
    subject_sha256: str,
    verification_state: str = "DECLARED_NOT_VERIFIED",
) -> dict[str, Any]:
    """Bind a complete source-call provenance envelope to one core subject.

    ``subject_sha256`` must be computed from the core object's closed subject
    projection, independently of call arguments. The artifact digest commits a
    canonical inline provenance envelope containing request and response
    digests, source release, retrieval time, runtime version, and collection
    coverage. It does not authenticate those declarations or biological truth.
    """

    if not isinstance(receipt_id, str) or _IDENTIFIER.fullmatch(receipt_id) is None:
        raise SourceContractError("receipt_id must use the closed portable identifier syntax")
    if not isinstance(verification_state, str) or verification_state not in {
        "DECLARED_NOT_VERIFIED",
        "RECEIPT_VERIFIED",
    }:
        raise SourceContractError("verification_state is unsupported")
    if not isinstance(subject_sha256, str) or _SHA256.fullmatch(subject_sha256) is None:
        raise SourceContractError("subject_sha256 must be a lowercase SHA-256 digest")
    owned_call = _owned_object(call_receipt, label="call receipt")
    if set(owned_call) != _CALL_RECEIPT_FIELDS:
        raise SourceContractError("call receipt violates the closed source-call contract")
    arguments_sha256 = owned_call.get("arguments_sha256")
    response_sha256 = owned_call.get("response_sha256")
    source_name = owned_call.get("source_name")
    if not isinstance(arguments_sha256, str) or _SHA256.fullmatch(arguments_sha256) is None:
        raise SourceContractError("call receipt has no valid arguments_sha256")
    if not isinstance(response_sha256, str) or _SHA256.fullmatch(response_sha256) is None:
        raise SourceContractError("call receipt has no valid response_sha256")
    source_name = _text(source_name, label="call receipt source_name", maximum=240)
    tool_name = owned_call.get("tool_name")
    tool_version = owned_call.get("tooluniverse_version")
    captured_at = owned_call.get("retrieved_at")
    source_release = owned_call.get("source_release")
    tool_name = _text(tool_name, label="call receipt tool_name", maximum=128)
    tool_version = _text(tool_version, label="call receipt tooluniverse_version", maximum=64)
    if not _valid_utc_timestamp(captured_at):
        raise SourceContractError("call receipt has no retrieved_at")
    if source_release is not None:
        source_release = _text(source_release, label="call receipt source_release", maximum=128)
    if owned_call.get("transport_success_only") is not True:
        raise SourceContractError("call receipt does not establish transport success")
    if owned_call.get("verification_authority") != "TRANSPORT_AND_BYTE_COMMITMENT_ONLY":
        raise SourceContractError("call receipt has an unsupported authority")
    collection = owned_call.get("collection_audit")
    if collection is not None:
        if not isinstance(collection, Mapping) or set(collection) != _AUDIT_FIELDS:
            raise SourceContractError("call receipt collection_audit is invalid")
        cursor = "declared-present" if collection.get("next_cursor_present") is True else None
        recomputed = audit_collection(
            returned_count=collection.get("returned_count"),
            reported_total=collection.get("reported_total"),
            next_cursor=cursor,
            requested_limit=collection.get("requested_limit"),
        )
        if collection != recomputed:
            raise SourceContractError("call receipt collection_audit fails recomputation")
        collection = recomputed
    provenance = {
        "arguments_sha256": arguments_sha256,
        "artifact_kind": None,
        "byte_count": None,
        "capture_method": "TOOLUNIVERSE_CALL",
        "captured_at": captured_at,
        "collection_audit": collection,
        "content_sha256": response_sha256,
        "coordinate_convention": None,
        "format_version": None,
        "reference_accession": None,
        "schema_version": "phytolocus.receipt-provenance.v0.1",
        "source_checksum_sha256": None,
        "source_record": source_name,
        "source_release": source_release,
        "tool_name": tool_name,
        "tool_version": tool_version,
    }
    return {
        "artifact_sha256": _digest(provenance),
        "id": receipt_id,
        "issuer": source_name,
        "kind": "SOURCE",
        "provenance": provenance,
        "subject_sha256": subject_sha256,
        "verification_state": verification_state,
    }


class ToolUniverseAdapter:
    """Narrow, injected facade over an already loaded ToolUniverse instance."""

    def __init__(self, tool_universe: Any, *, require_pinned_version: bool = True) -> None:
        installed = importlib.metadata.version("tooluniverse")
        if require_pinned_version and installed != TOOLUNIVERSE_PIN:
            raise SourceContractError(
                f"ToolUniverse {TOOLUNIVERSE_PIN} is required; found {installed}"
            )
        tools = getattr(tool_universe, "tools", None)
        if tools is None:
            raise SourceContractError("ToolUniverse instance has no loaded tools facade")
        self._tools = tools
        self.version = installed

    def available(self, names: Sequence[str]) -> dict[str, bool]:
        """Report exact wrapper registration without importing optional biology stacks."""

        if isinstance(names, (str, bytes)) or not isinstance(names, Sequence):
            raise SourceContractError("tool names must be a sequence of unique safe strings")
        validated = [_text(name, label="tool name", maximum=128) for name in names]
        if len(validated) != len(set(validated)):
            raise SourceContractError("tool names must be unique")
        return {name: hasattr(self._tools, name) for name in validated}

    def call(self, name: str, /, **arguments: Any) -> Mapping[str, Any]:
        """Call one exact wrapper and preserve the upstream envelope."""

        name = _text(name, label="tool name", maximum=128)
        tool = getattr(self._tools, name, None)
        if tool is None or not callable(tool):
            raise SourceContractError("ToolUniverse wrapper is unavailable")
        result = tool(**arguments)
        if not isinstance(result, Mapping):
            raise SourceContractError("ToolUniverse wrapper returned a non-object payload")
        return result


def validate_external_digest(value: str) -> str:
    """Validate a lowercase SHA-256 anchor for public integration callers."""

    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise SourceContractError("expected digest must be a lowercase SHA-256 string")
    return value
