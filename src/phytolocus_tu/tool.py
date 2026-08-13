"""ToolUniverse 1.4.1 adapter for the local PhytoLocus compiler."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tooluniverse.base_tool import BaseTool
from tooluniverse.tool_registry import register_tool

from phytolocus.io import validate_path_argument

from .fixture import FIXTURE_ID, fixture_payload

TARGET_SELECTION = {"allowed": False, "status": "PROHIBITED"}
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_OPERATIONS = {
    "preflight": {"fixture_id", "study"},
    "compile": {"fixture_id", "study"},
    "validate_capsule": {
        "capsule_dir",
        "expected_capsule_digest",
        "fixture_id",
        "study",
    },
}


def _success(data: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": "success",
        "data": dict(data),
        "target_selected": False,
        "target_selection": dict(TARGET_SELECTION),
    }


def _error(code: str, message: str, remediation: str) -> dict[str, Any]:
    return {
        "status": "error",
        "error": message,
        "error_code": code,
        "remediation": remediation,
        "target_selected": False,
        "target_selection": dict(TARGET_SELECTION),
    }


def _validate_arguments(arguments: dict[str, Any], operation: str) -> None:
    if any(not isinstance(key, str) for key in arguments):
        raise ValueError("argument field names must be strings")
    unknown = sorted(set(arguments) - _OPERATIONS[operation])
    if unknown:
        raise ValueError(f"unrecognized argument field(s): {', '.join(unknown)}")
    study = arguments.get("study")
    fixture_id = arguments.get("fixture_id")
    if study is not None and not isinstance(study, Mapping):
        raise ValueError("study must be an object when supplied")
    if fixture_id is not None and not isinstance(fixture_id, str):
        raise ValueError("fixture_id must be a string when supplied")
    if fixture_id is not None and fixture_id != FIXTURE_ID:
        raise ValueError("fixture_id is not supported")
    if study is not None and fixture_id is not None:
        raise ValueError("supply study or fixture_id, never both")
    if operation != "validate_capsule":
        if study is None and fixture_id is None:
            raise ValueError("study or fixture_id is required")
        return
    capsule_dir = arguments.get("capsule_dir")
    if capsule_dir is not None and not isinstance(capsule_dir, str):
        raise ValueError("capsule_dir must be a string when supplied")
    if capsule_dir is not None:
        validate_path_argument(capsule_dir)
    expected = arguments.get("expected_capsule_digest")
    if expected is not None and (
        not isinstance(expected, str) or _SHA256.fullmatch(expected) is None
    ):
        raise ValueError("expected_capsule_digest must be a lowercase SHA-256 string")
    if fixture_id is not None and expected is not None:
        raise ValueError("expected_capsule_digest is not accepted with fixture_id")
    packaged = fixture_id is not None
    external = capsule_dir is not None
    if packaged == external:
        raise ValueError("supply fixture_id or capsule_dir, never both")
    if external and study is None and expected is None:
        raise ValueError("external capsule validation requires study or expected_capsule_digest")


def _study_from_arguments(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    fixture_id = arguments.get("fixture_id")
    if fixture_id is not None:
        if fixture_id != FIXTURE_ID:
            raise ValueError(f"unsupported fixture_id; expected {FIXTURE_ID!r}")
        return fixture_payload()
    study = arguments.get("study")
    if not isinstance(study, Mapping):
        raise ValueError("study is required")
    return study


def _core() -> dict[str, Any]:
    from phytolocus import (
        Study,
        ValidationError,
        compile_capsule,
        compile_study,
        validate_capsule,
    )

    return {
        "Study": Study,
        "ValidationError": ValidationError,
        "compile_capsule": compile_capsule,
        "compile_study": compile_study,
        "validate_capsule": validate_capsule,
    }


def _preflight(arguments: Mapping[str, Any], core: Mapping[str, Any]) -> dict[str, Any]:
    study = core["Study"].from_dict(_study_from_arguments(arguments))
    return {
        "assembly_count": len(study.assemblies),
        "authority": "INPUT_VALIDATION_ONLY",
        "correspondence_claim_count": len(study.correspondence_claims),
        "locus_model_count": len(study.locus_models),
        "material_count": len(study.materials),
        "observation_count": len(study.observations),
        "receipt_count": len(study.receipts),
        "schema_version": study.schema_version,
        "sequence_object_count": len(study.sequence_objects),
        "study_id": study.study_id,
        "transcript_model_count": len(study.transcript_models),
        "validated": True,
    }


def _compile(arguments: Mapping[str, Any], core: Mapping[str, Any]) -> dict[str, Any]:
    compiled = core["compile_study"](_study_from_arguments(arguments))
    documents = compiled.documents()
    handoffs = compiled.reconciliation["downstream_handoffs"]
    ambiguous_groups = handoffs["phytoparalog"]["ambiguous_representation_groups"]
    return {
        "ambiguous_representation_groups": [list(group) for group in ambiguous_groups],
        "authority": "NON_AUTHORITATIVE_COMPILER_OUTPUT",
        "document_sha256": {
            name: hashlib.sha256(
                (
                    json.dumps(
                        document,
                        allow_nan=False,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                    + "\n"
                ).encode("utf-8")
            ).hexdigest()
            for name, document in sorted(documents.items())
        },
        "hold_reasons": list(compiled.reconciliation["hold_reasons"]),
        "producer_status": compiled.reconciliation["producer_status"],
        "schema_version": compiled.reconciliation["schema_version"],
        "study_id": compiled.reconciliation["study_id"],
    }


def _validate_capsule(
    arguments: Mapping[str, Any],
    core: Mapping[str, Any],
    *,
    validated_study: Any | None = None,
) -> dict[str, Any]:
    if arguments.get("fixture_id") is not None:
        study = _study_from_arguments(arguments)
        with tempfile.TemporaryDirectory(prefix="phytolocus-tu-") as temporary:
            capsule = Path(temporary) / "capsule"
            core["compile_capsule"](study, capsule)
            return core["validate_capsule"](capsule, study=study)
    return core["validate_capsule"](
        arguments["capsule_dir"],
        study=validated_study,
        expected_digest=arguments.get("expected_capsule_digest"),
    )


def _caused_by_os_error(error: BaseException) -> bool:
    """Return whether a redacted validation error originated in filesystem access."""

    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        if isinstance(current, OSError):
            return True
        seen.add(id(current))
        current = current.__cause__ if current.__cause__ is not None else current.__context__
    return False


@register_tool("PhytoLocusTool")
class PhytoLocusTool(BaseTool):
    """ToolUniverse surface over the local deterministic compiler."""

    def __init__(self, tool_config: dict[str, Any]) -> None:
        super().__init__(tool_config)
        self.operation = tool_config.get("phytolocus_operation")

    def validate_parameters(self, arguments: dict[str, Any]) -> None:
        """Defer validation so every refusal uses the public PhytoLocus envelope."""

        return None

    def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(arguments, dict):
            return _error(
                "INVALID_ARGUMENTS",
                "PhytoLocus rejected the supplied input under its closed contract.",
                "Supply one JSON object matching the selected tool parameter contract.",
            )
        try:
            if self.operation not in _OPERATIONS:
                return _error(
                    "ADAPTER_CONFIGURATION_INVALID",
                    "PhytoLocus adapter operation is not configured.",
                    "Reinstall the reviewed PhytoLocus package.",
                )
            _validate_arguments(arguments, self.operation)
        except ValueError:
            return _error(
                "INVALID_ARGUMENTS",
                "PhytoLocus rejected the supplied input under its closed contract.",
                "Validate a complete phytolocus.study.v0.1 input or use the synthetic fixture.",
            )

        try:
            core = _core()
        except Exception:
            return _error(
                "INTERNAL_ERROR",
                "PhytoLocus could not initialize the requested operation.",
                "Reinstall the reviewed PhytoLocus package and its pinned dependencies.",
            )

        validation_error = core["ValidationError"]
        try:
            if self.operation == "preflight":
                return _success(_preflight(arguments, core))
            if self.operation == "compile":
                return _success(_compile(arguments, core))
            validated_study = None
            if arguments.get("study") is not None:
                try:
                    validated_study = core["Study"].from_dict(arguments["study"])
                except validation_error:
                    return _error(
                        "INVALID_ARGUMENTS",
                        "PhytoLocus rejected the supplied input under its closed contract.",
                        (
                            "Validate a complete phytolocus.study.v0.1 input before using it "
                            "as a capsule recomputation anchor."
                        ),
                    )
            return _success(_validate_capsule(arguments, core, validated_study=validated_study))
        except validation_error as exc:
            if self.operation == "validate_capsule":
                if _caused_by_os_error(exc):
                    return _error(
                        "CAPSULE_UNAVAILABLE",
                        "PhytoLocus could not access the requested capsule safely.",
                        (
                            "Check that the capsule exists and is readable, then retry without "
                            "symbolic links or special files."
                        ),
                    )
                return _error(
                    "CAPSULE_INVALID",
                    "PhytoLocus rejected the capsule under its closed validation contract.",
                    (
                        "Recreate the capsule from reviewed input or verify it against the "
                        "retained external digest."
                    ),
                )
            return _error(
                "INVALID_ARGUMENTS",
                "PhytoLocus rejected the supplied input under its closed contract.",
                "Validate a complete phytolocus.study.v0.1 input or use the synthetic fixture.",
            )
        except OSError:
            if self.operation != "validate_capsule":
                return _error(
                    "INTERNAL_ERROR",
                    "PhytoLocus could not complete the requested operation.",
                    (
                        "Retry with the packaged synthetic fixture and report a minimal public "
                        "reproduction."
                    ),
                )
            return _error(
                "CAPSULE_UNAVAILABLE",
                "PhytoLocus could not access the requested capsule safely.",
                (
                    "Check that the capsule exists and is readable, then retry without symbolic "
                    "links or special files."
                ),
            )
        except Exception:
            return _error(
                "INTERNAL_ERROR",
                "PhytoLocus could not complete the requested operation.",
                (
                    "Retry with the packaged synthetic fixture and report a minimal public "
                    "reproduction."
                ),
            )


__all__ = ["PhytoLocusTool"]
