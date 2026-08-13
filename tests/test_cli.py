from __future__ import annotations

import json
from pathlib import Path

import pytest

from phytolocus.cli import main
from phytolocus_tu.fixture import fixture_payload

ERROR_FIELDS = {
    "error",
    "error_code",
    "remediation",
    "status",
    "target_selected",
    "target_selection",
}


def _study(path: Path) -> Path:
    path.write_text(json.dumps(fixture_payload()), encoding="utf-8")
    return path


def test_validate_compile_and_recompute_cli(
    tmp_path: Path,
    capsys: object,
) -> None:
    study = _study(tmp_path / "study.json")
    capsule = tmp_path / "capsule"
    assert main(["validate", str(study)]) == 0
    validated = json.loads(capsys.readouterr().out)
    assert validated["validated"] is True
    assert validated["study_id"] == "synthetic-locus-lock-v1"

    assert main(["compile", str(study), "--out", str(capsule)]) == 0
    compiled = json.loads(capsys.readouterr().out)
    assert compiled["producer_status"] == "HOLD"
    assert compiled["target_selection"]["allowed"] is False

    assert main(["validate-capsule", str(capsule), "--study", str(study)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["capsule_valid"] is True
    assert report["capsule_semantics_trusted"] is True
    assert report["snapshot_valid"] is True
    assert report["validation_subject"] == "OWNED_BYTE_SNAPSHOT"
    assert report["mutable_path_current_at_return_guaranteed"] is False


def test_cli_error_does_not_echo_input_path(tmp_path: Path, capsys: object) -> None:
    private_name = tmp_path / "caller-private-study.json"
    private_name.write_text("{}", encoding="utf-8")
    assert main(["validate", str(private_name)]) == 2
    error = capsys.readouterr().err
    assert "caller-private-study" not in error
    assert str(tmp_path) not in error
    parsed = json.loads(error)
    assert set(parsed) == ERROR_FIELDS
    assert parsed["status"] == "error"
    assert parsed["error_code"] == "STUDY_INVALID"
    assert parsed["target_selected"] is False
    assert parsed["target_selection"] == {"allowed": False, "status": "PROHIBITED"}


def test_argparse_errors_are_one_redacted_json_envelope(
    tmp_path: Path,
    capsys: object,
) -> None:
    private_value = tmp_path / "caller-private-argument"
    assert main(["--not-a-command", str(private_value)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert str(tmp_path) not in captured.err
    assert "caller-private-argument" not in captured.err
    parsed = json.loads(captured.err)
    assert set(parsed) == ERROR_FIELDS
    assert parsed["error_code"] == "INVALID_ARGUMENTS"


def test_invalid_digest_is_a_redacted_argument_error(
    tmp_path: Path,
    capsys: object,
) -> None:
    private_capsule = tmp_path / "caller-private-capsule"
    assert (
        main(
            [
                "validate-capsule",
                str(private_capsule),
                "--expected-digest",
                "NOT-A-DIGEST",
            ]
        )
        == 2
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert str(tmp_path) not in captured.err
    assert "NOT-A-DIGEST" not in captured.err
    assert json.loads(captured.err)["error_code"] == "INVALID_ARGUMENTS"


@pytest.mark.parametrize("invalid_path", ["bad\x00path", "bad\ud800path"])
def test_invalid_filesystem_strings_are_argument_errors(
    invalid_path: str,
    capsys: object,
) -> None:
    assert main(["validate", invalid_path]) == 2
    validate_error = capsys.readouterr()
    assert validate_error.out == ""
    assert json.loads(validate_error.err)["error_code"] == "INVALID_ARGUMENTS"

    assert main(["validate-capsule", invalid_path]) == 2
    capsule_error = capsys.readouterr()
    assert capsule_error.out == ""
    assert json.loads(capsule_error.err)["error_code"] == "INVALID_ARGUMENTS"


def test_cli_distinguishes_unavailable_and_invalid_capsules(
    tmp_path: Path,
    capsys: object,
) -> None:
    missing = tmp_path / "caller-private-missing-capsule"
    assert main(["validate-capsule", str(missing)]) == 2
    missing_error = capsys.readouterr()
    assert missing_error.out == ""
    assert json.loads(missing_error.err)["error_code"] == "CAPSULE_UNAVAILABLE"
    assert str(tmp_path) not in missing_error.err

    malformed = tmp_path / "caller-private-malformed-capsule"
    malformed.mkdir()
    assert main(["validate-capsule", str(malformed)]) == 2
    malformed_error = capsys.readouterr()
    assert malformed_error.out == ""
    assert json.loads(malformed_error.err)["error_code"] == "CAPSULE_INVALID"
    assert str(tmp_path) not in malformed_error.err


def test_cli_distinguishes_unavailable_study_and_failed_capsule_write(
    tmp_path: Path,
    capsys: object,
) -> None:
    missing = tmp_path / "caller-private-missing-study.json"
    assert main(["validate", str(missing)]) == 2
    unavailable_error = capsys.readouterr()
    assert unavailable_error.out == ""
    assert json.loads(unavailable_error.err)["error_code"] == "STUDY_UNAVAILABLE"
    assert str(tmp_path) not in unavailable_error.err

    study = _study(tmp_path / "study.json")
    existing = tmp_path / "caller-private-existing-output"
    existing.mkdir()
    assert main(["compile", str(study), "--out", str(existing)]) == 2
    write_error = capsys.readouterr()
    assert write_error.out == ""
    assert json.loads(write_error.err)["error_code"] == "CAPSULE_WRITE_FAILED"
    assert str(tmp_path) not in write_error.err
