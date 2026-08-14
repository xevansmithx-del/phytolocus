from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from phytolocus import ValidationError, compile_capsule, compile_study, validate_capsule
from phytolocus.cli import main
from phytolocus.io import load_json
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


@pytest.mark.skipif(os.name != "nt", reason="Windows descriptor/path stat contract")
def test_windows_loader_owns_crlf_bytes_and_detects_path_races(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    study = tmp_path / "study.json"
    payload = json.dumps(fixture_payload(), indent=2).replace("\n", "\r\n").encode("utf-8")
    study.write_bytes(payload)
    assert load_json(study) == fixture_payload()

    real_lstat = os.lstat
    calls = 0

    def changed_lstat(path: str | os.PathLike[str]) -> os.stat_result | SimpleNamespace:
        nonlocal calls
        details = real_lstat(path)
        calls += 1
        if calls != 2:
            return details
        return SimpleNamespace(
            st_ctime_ns=details.st_ctime_ns + 1,
            st_dev=details.st_dev,
            st_ino=details.st_ino,
            st_mtime_ns=details.st_mtime_ns,
            st_nlink=details.st_nlink,
            st_size=details.st_size,
        )

    monkeypatch.setattr(os, "lstat", changed_lstat)
    with pytest.raises(ValidationError, match="identity changed while reading"):
        load_json(study)


@pytest.mark.skipif(os.name != "nt", reason="Windows filesystem refusal contract")
def test_windows_loader_rejects_nonregular_and_multilink_inputs(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="single-link regular file"):
        load_json(tmp_path)

    study = tmp_path / "study.json"
    alias = tmp_path / "study-alias.json"
    study.write_text(json.dumps(fixture_payload()), encoding="utf-8")
    os.link(study, alias)
    with pytest.raises(ValidationError, match="single-link regular file"):
        load_json(study)


@pytest.mark.skipif(os.name != "nt", reason="Windows symbolic-link refusal contract")
def test_windows_loader_rejects_symbolic_link_input(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    link = tmp_path / "study.json"
    target.write_text(json.dumps(fixture_payload()), encoding="utf-8")
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"Windows symbolic-link creation unavailable: {exc}")
    with pytest.raises(ValidationError, match="symbolic-link"):
        load_json(link)


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
