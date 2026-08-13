"""Command-line interface for the closed PhytoLocus compiler."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from typing import Any

from .capsule import compile_capsule, validate_capsule
from .io import load_json, validate_path_argument
from .models import ModelError, StudySpec

TARGET_SELECTION = {"allowed": False, "status": "PROHIBITED"}
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_ERROR_DETAILS = {
    "CAPSULE_INVALID": (
        "PhytoLocus rejected the capsule under its closed validation contract.",
        "Recreate the capsule from reviewed input or verify the retained external digest.",
    ),
    "CAPSULE_UNAVAILABLE": (
        "PhytoLocus could not access the requested capsule safely.",
        "Check that the capsule exists and is readable without symbolic links or special files.",
    ),
    "CAPSULE_WRITE_FAILED": (
        "PhytoLocus could not create the requested capsule safely.",
        "Use a new destination inside a writable real directory, then retry.",
    ),
    "INTERNAL_ERROR": (
        "PhytoLocus could not complete the requested operation.",
        "Retry with the packaged synthetic example and report a minimal public reproduction.",
    ),
    "INVALID_ARGUMENTS": (
        "PhytoLocus rejected the command-line arguments under its closed contract.",
        "Run phytolocus --help and supply exactly one documented command and its arguments.",
    ),
    "STUDY_INVALID": (
        "PhytoLocus rejected the study under its closed input contract.",
        "Validate a complete phytolocus.study.v0.1 JSON document and retry.",
    ),
    "STUDY_UNAVAILABLE": (
        "PhytoLocus could not access the requested study safely.",
        "Check that the study exists and is a readable single-link regular JSON file.",
    ),
}


class _CLIArgumentError(Exception):
    """Internal signal for a redacted, machine-readable argparse failure."""


class _JSONArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise _CLIArgumentError


def _sha256_argument(value: str) -> str:
    if _SHA256.fullmatch(value) is None:
        raise argparse.ArgumentTypeError("expected a lowercase SHA-256 digest")
    return value


def _path_argument(value: str) -> str:
    try:
        return validate_path_argument(value)
    except ModelError as exc:
        raise argparse.ArgumentTypeError("expected a safe filesystem path") from exc


def _parser() -> argparse.ArgumentParser:
    parser = _JSONArgumentParser(
        prog="phytolocus",
        description="Compile fail-closed plant stock-to-locus evidence.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="Validate one strict study JSON file.")
    validate.add_argument("study", type=_path_argument)

    compile_command = subparsers.add_parser(
        "compile",
        help="Compile one strict study into a new five-file capsule.",
    )
    compile_command.add_argument("study", type=_path_argument)
    compile_command.add_argument("--out", required=True, dest="output_dir", type=_path_argument)

    validate_capsule_command = subparsers.add_parser(
        "validate-capsule",
        help="Validate capsule integrity and optionally recompute its study semantics.",
    )
    validate_capsule_command.add_argument("capsule_dir", type=_path_argument)
    validate_capsule_command.add_argument("--study", type=_path_argument)
    validate_capsule_command.add_argument("--expected-digest", type=_sha256_argument)
    return parser


def _print(value: Any, *, stream: Any | None = None) -> None:
    destination = sys.stdout if stream is None else stream
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), file=destination)


def _load_study(path: str) -> StudySpec:
    return StudySpec.from_dict(load_json(path))


def _caused_by_os_error(error: BaseException) -> bool:
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        if isinstance(current, OSError):
            return True
        seen.add(id(current))
        current = current.__cause__ if current.__cause__ is not None else current.__context__
    return False


def _fail(code: str) -> int:
    message, remediation = _ERROR_DETAILS[code]
    _print(
        {
            "error": message,
            "error_code": code,
            "remediation": remediation,
            "status": "error",
            "target_selected": False,
            "target_selection": dict(TARGET_SELECTION),
        },
        stream=sys.stderr,
    )
    return 2


def _load_study_for_cli(path: str) -> StudySpec | int:
    try:
        return _load_study(path)
    except ModelError as exc:
        return _fail("STUDY_UNAVAILABLE" if _caused_by_os_error(exc) else "STUDY_INVALID")
    except OSError:
        return _fail("STUDY_UNAVAILABLE")


def _dispatch(arguments: argparse.Namespace) -> int:
    if arguments.command == "validate":
        study = _load_study_for_cli(arguments.study)
        if isinstance(study, int):
            return study
        _print(
            {
                "assembly_count": len(study.assemblies),
                "locus_model_count": len(study.locus_models),
                "schema_version": study.schema_version,
                "study_id": study.study_id,
                "target_selection": dict(TARGET_SELECTION),
                "transcript_model_count": len(study.transcript_models),
                "validated": True,
            }
        )
        return 0
    if arguments.command == "compile":
        study = _load_study_for_cli(arguments.study)
        if isinstance(study, int):
            return study
        try:
            result = compile_capsule(study, arguments.output_dir)
        except (ModelError, OSError):
            return _fail("CAPSULE_WRITE_FAILED")
        _print(result)
        return 0
    if arguments.command == "validate-capsule":
        study = None
        if arguments.study is not None:
            loaded = _load_study_for_cli(arguments.study)
            if isinstance(loaded, int):
                return loaded
            study = loaded
        try:
            result = validate_capsule(
                arguments.capsule_dir,
                study=study,
                expected_digest=arguments.expected_digest,
            )
        except ModelError as exc:
            return _fail("CAPSULE_UNAVAILABLE" if _caused_by_os_error(exc) else "CAPSULE_INVALID")
        except OSError:
            return _fail("CAPSULE_UNAVAILABLE")
        _print(result)
        return 0
    return _fail("INVALID_ARGUMENTS")


def main(argv: Sequence[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
    except (_CLIArgumentError, argparse.ArgumentError):
        return _fail("INVALID_ARGUMENTS")
    try:
        return _dispatch(arguments)
    except Exception:
        return _fail("INTERNAL_ERROR")


__all__ = ["main"]
