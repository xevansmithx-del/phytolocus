from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
BACKSLASH = chr(92)
SPEC = importlib.util.spec_from_file_location("privacy_scan", ROOT / "scripts" / "privacy_scan.py")
assert SPEC is not None and SPEC.loader is not None
PRIVACY_SCAN = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = PRIVACY_SCAN
SPEC.loader.exec_module(PRIVACY_SCAN)


def _write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _rules(path: Path) -> set[str]:
    return {item.rule for item in PRIVACY_SCAN.scan(path)}


def test_clean_tree_passes(tmp_path: Path) -> None:
    _write(tmp_path / "README.md", "A neutral synthetic fixture.\n")
    assert PRIVACY_SCAN.scan(tmp_path) == []


@pytest.mark.parametrize(
    "local_path",
    [
        "/" + "Users" + "/person/analysis/input.json",
        "/" + "home" + "/person/analysis/input.json",
        "/" + "root" + "/analysis/input.json",
        "/" + "workspace" + "/project/input.json",
        "/" + "tmp" + "/analysis/input.json",
        "/" + "pri" + "vate/analysis/input.json",
        "/" + "v" + "ar/folders/run/input.json",
        "/" + "da" + "ta/analysis/input.json",
        "/" + "scra" + "tch/analysis/input.json",
        "/" + "s" + "rv/analysis/input.json",
        "/" + "o" + "pt/analysis/input.json",
        "/" + "me" + "dia/analysis/input.json",
        "/" + "r" + "un/user/501/input.json",
        "/" + "Volumes" + "/da" + "ta/input.json",
        "/" + "mnt" + "/c/analysis/input.json",
        "C:" + BACKSLASH + "Users" + BACKSLASH + "person/input.json",
        "D:" + "/Temp/person/input.json",
        (BACKSLASH * 2) + "server" + BACKSLASH + "share/input.json",
        "fi" + "le:///" + "Users" + "/person/input.json",
        "~" + "/analysis/input.json",
        "$" + "HOME/analysis/input.json",
        "$" + "{HOME}/analysis/input.json",
        "%" + "USERPROFILE%" + BACKSLASH + "analysis" + BACKSLASH + "input.json",
    ],
)
def test_detects_local_paths_without_revealing_match(tmp_path: Path, local_path: str) -> None:
    _write(tmp_path / "note.txt", local_path)
    findings = PRIVACY_SCAN.scan(tmp_path)
    assert findings
    assert "person" not in " ".join(item.excerpt for item in findings)


@pytest.mark.parametrize(
    "encoded_path",
    [
        "%2F" + "home%2Fperson%2Finput.json",
        BACKSLASH + "u002f" + "home" + BACKSLASH + "u002fperson/input.json",
        "C%3A%5C" + "Users%5Cperson%5Cinput.json",
    ],
)
def test_detects_encoded_local_paths(tmp_path: Path, encoded_path: str) -> None:
    _write(tmp_path / "note.txt", encoded_path)
    assert PRIVACY_SCAN.scan(tmp_path)


def test_detects_json_escaped_windows_and_unc_paths(tmp_path: Path) -> None:
    value = {
        "drive": "C:" + BACKSLASH + "Users" + BACKSLASH + "person/secret.txt",
        "unc": (BACKSLASH * 2) + "server" + BACKSLASH + "share/secret.txt",
    }
    _write(tmp_path / "payload.json", json.dumps(value))
    rules = _rules(tmp_path)
    assert "windows-local-path" in rules
    assert "unc-local-path" in rules


@pytest.mark.parametrize(
    "label",
    [
        "confi" + "dential",
        "not for" + " distribution",
        "NOT_FOR_PUBLIC" + "_RELEASE",
        "internal" + " project",
        "INTERNAL" + "_ONLY",
        "private" + " project",
        "unpublished" + " data",
        "embargoed" + " dataset",
        "do not" + " share",
    ],
)
def test_detects_obvious_private_labels(tmp_path: Path, label: str) -> None:
    _write(tmp_path / "note.txt", label)
    assert "private-label" in _rules(tmp_path)


@pytest.mark.parametrize(
    "marker",
    [
        "raw" + " sequence",
        "RAW" + "_SEQUENCE",
        "unpublished" + " sequence",
        "private" + " sequence",
        "real biological" + " sequence",
    ],
)
def test_detects_unpublished_sequence_markers(tmp_path: Path, marker: str) -> None:
    _write(tmp_path / "note.txt", marker)
    assert "nonpublic-biological-marker" in _rules(tmp_path)


@pytest.mark.parametrize(
    "sequence",
    [
        ("AC" + "GT") * 14,
        ("ACGT" + "acgt") * 3 + "\n" + ("tgca" + "TGCA") * 3,
        ("AcDeFgHiKl" + "MnPqRsTvWy") + "\n" + ("AcDeFgHiKl" + "MnPqRsTvWy"),
        "ACDE" + "FGHIKL MNPQ" + "RSTVWY ACDE" + "FGHIKL MNPQ" + "RSTVWY",
        "acdef" + "ghikl\nmnpqr" + "stvwy\nacdef" + "ghikl\nmnpqr" + "stvwy",
        "1 ACGT" + "ACGTACGT 13 TGCA" + "TGCATGCA 25 ACGT" + "ACGTACGT 37 TGCA" + "TGCATGCA",
    ],
)
def test_detects_sequence_layout_matrix(tmp_path: Path, sequence: str) -> None:
    _write(tmp_path / "notes.txt", sequence)
    assert "raw-biological-sequence" in _rules(tmp_path)


def test_detects_json_escaped_wrapped_sequence_without_field_hint(tmp_path: Path) -> None:
    row = ("ACGT" + "acgt") * 3
    _write(tmp_path / "payload.json", json.dumps({"payload": row + "\n" + row}))
    assert "raw-biological-sequence" in _rules(tmp_path)


def test_rejects_short_sequence_in_ordinary_json_field(tmp_path: Path) -> None:
    sequence = ("AC" + "GT") * 3
    _write(tmp_path / "fixture.json", json.dumps({"sequence": sequence}))
    assert "sequence-field" in _rules(tmp_path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("rawSequence", ("AC" + "GT") * 3),
        ("nucleotideBases", ("AC" + "GT") * 3),
        ("amino_acids", "ACDEFGHIKLMN"),
        ("sequence", ["ACGT", "TGCA"]),
    ],
)
def test_rejects_sequence_field_name_and_container_matrix(
    tmp_path: Path, field: str, value: object
) -> None:
    _write(tmp_path / "fixture.json", json.dumps({field: value}))
    assert "sequence-field" in _rules(tmp_path)


def test_allows_tiny_explicitly_synthetic_sequence(tmp_path: Path) -> None:
    sequence = ("AC" + "GT") * 3
    _write(tmp_path / "fixture.json", json.dumps({"synthetic_sequence": sequence}))
    assert PRIVACY_SCAN.scan(tmp_path) == []


def test_allows_tiny_camel_case_synthetic_sequence(tmp_path: Path) -> None:
    sequence = ("AC" + "GT") * 3
    _write(tmp_path / "fixture.json", json.dumps({"syntheticSequence": sequence}))
    assert PRIVACY_SCAN.scan(tmp_path) == []


def test_rejects_oversized_explicitly_synthetic_sequence(tmp_path: Path) -> None:
    sequence = ("AC" + "GT") * 9
    _write(tmp_path / "fixture.json", json.dumps({"synthetic_sequence": sequence}))
    assert "sequence-field" in _rules(tmp_path)


def test_detects_biological_file_extension(tmp_path: Path) -> None:
    _write(tmp_path / "records.fasta", "synthetic placeholder")
    assert _rules(tmp_path) == {"biological-file-extension"}


@pytest.mark.parametrize(
    "secret",
    [
        "gh" + "p_" + ("A" * 40),
        "sk" + "-" + ("A" * 30),
        "xox" + "b-" + ("A" * 24),
        "AK" + "IA" + ("A1" * 8),
        "api" + "_key=fixture-value",
        "Authorization: " + "Bearer " + "fixture-token-value-12345",
        "eyJ" + "hbGciOiJIUzI1NiJ9." + "eyJzdWIiOiIxMjM0NTY3ODkwIn0." + "signature12345",
    ],
)
def test_detects_credential_families(tmp_path: Path, secret: str) -> None:
    _write(tmp_path / "credential.txt", secret)
    assert PRIVACY_SCAN.scan(tmp_path)


def test_detects_signed_url_and_redacts_value(tmp_path: Path) -> None:
    value = "fixture-value"
    query_name = "s" + "ig"
    _write(tmp_path / "url.txt", f"https://example.org/file?{query_name}={value}")
    findings = PRIVACY_SCAN.scan(tmp_path)
    assert "credential-url" in {item.rule for item in findings}
    assert value not in " ".join(item.excerpt for item in findings)


def test_supports_custom_forbidden_terms(tmp_path: Path) -> None:
    _write(tmp_path / "plan.md", "Synthetic Project Alpha")
    findings = PRIVACY_SCAN.scan(tmp_path, ["Project Alpha"])
    assert [item.rule for item in findings] == ["forbidden-term-1"]


def test_rejects_invalid_utf8_and_binary_controls(tmp_path: Path) -> None:
    (tmp_path / "opaque.txt").write_bytes(b"prefix\xffsuffix")
    assert _rules(tmp_path) == {"content-not-utf8"}
    (tmp_path / "opaque.txt").write_bytes(b"prefix\x00suffix")
    assert _rules(tmp_path) == {"binary-control-content"}


def test_rejects_fifo_root_without_blocking(tmp_path: Path) -> None:
    fifo = tmp_path / "named-pipe"
    try:
        os.mkfifo(fifo)
    except (AttributeError, OSError) as exc:  # pragma: no cover - unsupported platforms.
        pytest.skip(f"FIFO creation unavailable: {exc}")
    assert _rules(fifo) == {"non-regular-file"}


@pytest.mark.parametrize("excluded_name", ["dist", ".git", ".venv", ".coverage"])
def test_excluded_name_symlink_is_classified_before_exclusion(
    tmp_path: Path, excluded_name: str
) -> None:
    release = tmp_path / "release"
    release.mkdir()
    target = tmp_path / "outside"
    target.mkdir()
    link = release / excluded_name
    try:
        link.symlink_to(Path("..") / target.name, target_is_directory=True)
    except OSError as exc:  # pragma: no cover - unsupported platforms.
        pytest.skip(f"symbolic link creation unavailable: {exc}")
    assert _rules(release) == {"symbolic-link"}


@pytest.mark.parametrize("excluded_name", ["dist", ".git", ".venv", ".coverage"])
def test_excluded_name_nonregular_entry_is_classified_before_exclusion(
    tmp_path: Path, excluded_name: str
) -> None:
    release = tmp_path / "release"
    release.mkdir()
    entry = release / excluded_name
    try:
        os.mkfifo(entry)
    except (AttributeError, OSError) as exc:  # pragma: no cover - unsupported platforms.
        pytest.skip(f"FIFO creation unavailable: {exc}")
    assert _rules(release) == {"non-regular-file"}


def test_expected_excluded_regular_entries_remain_ignored(tmp_path: Path) -> None:
    _write(tmp_path / ".coverage", "api" + "_key=ignored-value")
    _write(tmp_path / "dist" / "credential.txt", "api" + "_key=ignored-value")
    assert PRIVACY_SCAN.scan(tmp_path) == []


def test_rejects_hard_linked_file(tmp_path: Path) -> None:
    release = tmp_path / "release"
    release.mkdir()
    source = release / "record.txt"
    _write(source, "neutral fixture")
    try:
        os.link(source, tmp_path / "outside-alias.txt")
    except OSError as exc:  # pragma: no cover - unsupported filesystems.
        pytest.skip(f"hard-link creation unavailable: {exc}")
    assert _rules(release) == {"multiple-hard-links"}


def test_regular_file_to_symlink_swap_is_not_followed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = tmp_path / "release"
    release.mkdir()
    subject = release / "record.txt"
    target = tmp_path / "outside.txt"
    _write(subject, "neutral fixture")
    _write(target, "api" + "_key=outside-value")
    real_open = PRIVACY_SCAN.os.open
    swapped = False
    read_calls = 0

    def swap_before_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        nonlocal swapped
        if not swapped and Path(path) == subject:
            swapped = True
            subject.unlink()
            try:
                subject.symlink_to(target)
            except OSError as exc:  # pragma: no cover - unsupported platforms.
                pytest.skip(f"symbolic link creation unavailable: {exc}")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(PRIVACY_SCAN.os, "open", swap_before_open)
    real_read = PRIVACY_SCAN.os.read

    def count_reads(descriptor: int, count: int) -> bytes:
        nonlocal read_calls
        read_calls += 1
        return real_read(descriptor, count)

    monkeypatch.setattr(PRIVACY_SCAN.os, "read", count_reads)
    findings = PRIVACY_SCAN.scan(release)
    assert {item.rule for item in findings} <= {
        "file-changed-during-scan",
        "file-open-failed",
    }
    assert findings
    assert read_calls == 0
    assert "outside-value" not in json.dumps(
        [(item.rule, item.path, item.excerpt) for item in findings]
    )


def test_regular_file_to_fifo_swap_cannot_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    subject = tmp_path / "record.txt"
    _write(subject, "neutral fixture")
    real_open = PRIVACY_SCAN.os.open
    swapped = False

    def swap_before_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        nonlocal swapped
        if not swapped and Path(path) == subject:
            swapped = True
            subject.unlink()
            try:
                os.mkfifo(subject)
            except (AttributeError, OSError) as exc:  # pragma: no cover - unsupported platforms.
                pytest.skip(f"FIFO creation unavailable: {exc}")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(PRIVACY_SCAN.os, "open", swap_before_open)
    assert _rules(subject) == {"non-regular-file"}


@pytest.mark.parametrize("mutation", ["size", "mtime", "nlink"])
def test_post_read_metadata_mutation_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    subject = tmp_path / "record.txt"
    _write(subject, "neutral fixture")
    real_read = PRIVACY_SCAN.os.read
    mutated = False

    def mutate_after_read(descriptor: int, count: int) -> bytes:
        nonlocal mutated
        data = real_read(descriptor, count)
        if data and not mutated:
            mutated = True
            if mutation == "size":
                with subject.open("ab") as stream:
                    stream.write(b"x")
            elif mutation == "mtime":
                status = subject.stat()
                os.utime(
                    subject,
                    ns=(status.st_atime_ns, status.st_mtime_ns + 1_000_000_000),
                )
            else:
                os.link(subject, tmp_path / "outside-alias.txt")
        return data

    monkeypatch.setattr(PRIVACY_SCAN.os, "read", mutate_after_read)
    assert _rules(subject) == {"file-changed-during-scan"}


def test_directory_enumeration_failure_is_redacted_and_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocked_path = "/" + "home" + "/person/unreadable"

    def failed_walk(
        _root: object,
        *,
        followlinks: bool,
        onerror: object,
    ) -> object:
        assert followlinks is False
        assert callable(onerror)
        onerror(PermissionError(blocked_path))
        return iter(())

    monkeypatch.setattr(PRIVACY_SCAN.os, "walk", failed_walk)
    findings = PRIVACY_SCAN.scan(tmp_path)
    assert [item.rule for item in findings] == ["directory-enumeration-failed"]
    rendered = json.dumps([(item.rule, item.path, item.excerpt) for item in findings])
    assert blocked_path not in rendered
    assert findings[0].path == PRIVACY_SCAN.REDACTED_PATH


@pytest.mark.parametrize(
    "payload",
    [
        '{"sequence":"AC' + 'GTACGT","sequence":"ordinary prose!"}',
        '{"outer":{"rawSequence":"AC' + 'GTACGT","rawSequence":"ordinary prose!"}}',
    ],
)
def test_duplicate_json_keys_fail_closed_before_last_value_wins(
    tmp_path: Path, payload: str
) -> None:
    _write(tmp_path / "fixture.json", payload)
    findings = PRIVACY_SCAN.scan(tmp_path)
    assert "duplicate-json-key" in {item.rule for item in findings}
    assert all(item.path == PRIVACY_SCAN.REDACTED_PATH for item in findings)


def test_rejects_symlink_without_reading_external_target(tmp_path: Path) -> None:
    release = tmp_path / "release"
    release.mkdir()
    target = tmp_path / "outside.txt"
    target.write_text("gh" + "p_" + ("A" * 40), encoding="utf-8")
    link = release / "linked.txt"
    try:
        link.symlink_to(Path("..") / target.name)
    except OSError as exc:  # pragma: no cover - some platforms deny link creation.
        pytest.skip(f"symbolic link creation unavailable: {exc}")
    assert [item.rule for item in PRIVACY_SCAN.scan(release)] == ["symbolic-link"]


def test_rejects_directory_symlink_without_following_it(tmp_path: Path) -> None:
    release = tmp_path / "release"
    release.mkdir()
    target = tmp_path / "outside"
    target.mkdir()
    _write(target / "credential.txt", "api" + "_key=outside-value")
    link = release / "linked-directory"
    try:
        link.symlink_to(Path("..") / target.name, target_is_directory=True)
    except OSError as exc:  # pragma: no cover - some platforms deny link creation.
        pytest.skip(f"symbolic link creation unavailable: {exc}")
    findings = PRIVACY_SCAN.scan(release)
    assert [item.rule for item in findings] == ["symbolic-link"]
    assert "outside-value" not in json.dumps(
        [(item.rule, item.path, item.excerpt) for item in findings]
    )


def test_findings_and_cli_never_echo_blocked_content_or_paths(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    token = "gh" + "p_" + ("A" * 40)
    value = "blocked-value-12345"
    blocked_name = token + "-credential.txt"
    _write(tmp_path / blocked_name, "api" + f"_key={value}")

    findings = PRIVACY_SCAN.scan(tmp_path)
    rendered = json.dumps([(item.rule, item.path, item.excerpt) for item in findings])
    assert findings
    assert all(item.path == PRIVACY_SCAN.REDACTED_PATH for item in findings)
    assert all(item.excerpt == "<redacted>" for item in findings)
    assert token not in rendered
    assert value not in rendered
    assert blocked_name not in rendered

    assert PRIVACY_SCAN.main([os.fspath(tmp_path), "--json"]) == 1
    captured = capsys.readouterr()
    assert token not in captured.out
    assert value not in captured.out
    assert blocked_name not in captured.out


def test_exact_sha256_and_benign_urls_pass(tmp_path: Path) -> None:
    digest = "a" * 64
    _write(
        tmp_path / "README.md",
        "\n".join(
            [
                f'{{"artifact_sha256":"{digest}"}}',
                "https://example.org/records?status_code=404",
                "relative/project/result.json",
            ]
        ),
    )
    assert PRIVACY_SCAN.scan(tmp_path) == []


def test_sequence_sha256_commitment_is_not_treated_as_sequence(tmp_path: Path) -> None:
    digest = "a" * 64
    _write(tmp_path / "fixture.json", json.dumps({"sequence_sha256": digest}))
    assert PRIVACY_SCAN.scan(tmp_path) == []


def test_sequence_object_identifiers_are_not_treated_as_sequence(tmp_path: Path) -> None:
    value = {
        "sequence_object_ids": ["synthetic-genomic-sequence"],
        "transcript_sequence_id": "synthetic-transcript-sequence",
    }
    _write(tmp_path / "fixture.json", json.dumps(value))
    assert PRIVACY_SCAN.scan(tmp_path) == []


def test_repository_public_surface_passes() -> None:
    assert PRIVACY_SCAN.scan(ROOT) == []
