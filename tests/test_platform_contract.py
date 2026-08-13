from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from phytolocus import ValidationError, compile_capsule, compile_study, validate_capsule
from phytolocus.cli import main
from phytolocus_tu.fixture import fixture_payload


def test_in_memory_compiler_is_cross_platform_and_deterministic() -> None:
    first = compile_study(fixture_payload()).documents()
    second = compile_study(fixture_payload()).documents()
    assert first == second
    assert first["reconciliation.json"]["producer_status"] == "HOLD"


def test_validate_cli_is_cross_platform(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    study = tmp_path / "study.json"
    study.write_text(json.dumps(fixture_payload()), encoding="utf-8")
    assert main(["validate", str(study)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["validated"] is True
    assert result["target_selection"] == {"allowed": False, "status": "PROHIBITED"}


@pytest.mark.skipif(os.name != "nt", reason="Windows fail-closed capsule contract")
def test_capsule_api_refuses_windows_without_posix_descriptor_semantics(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="POSIX descriptor-relative"):
        compile_capsule(fixture_payload(), tmp_path / "capsule")
    capsule = tmp_path / "existing-capsule"
    capsule.mkdir()
    with pytest.raises(ValidationError, match="POSIX descriptor-relative"):
        validate_capsule(capsule)


@pytest.mark.skipif(os.name != "nt", reason="Windows fail-closed capsule CLI contract")
def test_capsule_cli_refuses_windows_with_structured_errors(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    study = tmp_path / "study.json"
    study.write_text(json.dumps(fixture_payload()), encoding="utf-8")
    assert main(["compile", str(study), "--out", str(tmp_path / "capsule")]) == 2
    compile_error = json.loads(capsys.readouterr().err)
    assert compile_error["error_code"] == "CAPSULE_WRITE_FAILED"
    assert compile_error["target_selected"] is False

    capsule = tmp_path / "existing-capsule"
    capsule.mkdir()
    assert main(["validate-capsule", str(capsule)]) == 2
    validate_error = json.loads(capsys.readouterr().err)
    assert validate_error["error_code"] == "CAPSULE_INVALID"
    assert validate_error["target_selected"] is False
