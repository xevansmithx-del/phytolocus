from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
WORKFLOWS = (ROOT / ".github/workflows/ci.yml", ROOT / ".github/workflows/release.yml")
SCOPE_DOCUMENTS = (
    ROOT / "README.md",
    ROOT / "docs/architecture.md",
    ROOT / "docs/provenance.md",
    ROOT / "docs/schema.md",
    ROOT / "docs/scientific-boundaries.md",
    ROOT / "docs/testing.md",
    ROOT / "docs/tooluniverse.md",
)
PUBLIC_CAPABILITY_FILES = (
    *SCOPE_DOCUMENTS,
    ROOT / "docs/threat-model.md",
    ROOT / "examples/synthetic/README.md",
    ROOT / "CHANGELOG.md",
    ROOT / "CITATION.cff",
    ROOT / "pyproject.toml",
    ROOT / "src/phytolocus_tu/data/phytolocus_tools.json",
)


def test_lockfile_is_part_of_the_source_distribution_contract() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    release = WORKFLOWS[1].read_text(encoding="utf-8")

    assert '"/uv.lock"' in pyproject
    assert re.search(r"(?m)^include uv\.lock$", manifest)
    assert 'test -f "$sdist_root/uv.lock"' in release


def test_ci_and_release_execute_the_frozen_lock() -> None:
    for path in WORKFLOWS:
        workflow = path.read_text(encoding="utf-8")
        assert "python -m pip install -e" not in workflow
        assert "uv sync --frozen" in workflow
        assert "uv run --frozen --no-sync" in workflow
        assert "astral-sh/setup-uv@08807647e7069bb48b6ef5acd8ec9567f424441b" in workflow
        assert 'version: "0.11.32"' in workflow


def test_ci_covers_posix_capsules_and_windows_refusal() -> None:
    ci = WORKFLOWS[0].read_text(encoding="utf-8")
    assert "ubuntu-latest" in ci
    assert "macos-latest" in ci
    assert "windows-latest" in ci
    assert "runner.os != 'Windows'" in ci
    assert "tests/test_platform_contract.py" in ci


def test_every_third_party_action_is_pinned_to_a_full_commit() -> None:
    action = re.compile(r"^\s*uses:\s*([^\s#]+)", re.MULTILINE)
    for path in WORKFLOWS:
        for reference in action.findall(path.read_text(encoding="utf-8")):
            _, separator, revision = reference.rpartition("@")
            assert separator == "@"
            assert re.fullmatch(r"[0-9a-f]{40}", revision)


def test_release_wheel_runtime_is_exported_from_the_lock() -> None:
    release = WORKFLOWS[1].read_text(encoding="utf-8")
    assert (
        "uv export --frozen --no-dev --extra dev --extra tooluniverse --no-emit-project" in release
    )
    assert "uv pip sync" in release
    assert "--require-hashes" in release
    assert "uv pip install" in release
    assert "--no-deps" in release


def test_clean_wheel_and_sdist_each_run_full_and_loaded_tool_contracts() -> None:
    for workflow_path in WORKFLOWS:
        workflow = workflow_path.read_text(encoding="utf-8")
        assert workflow.count("-m 'not live'") >= 3
        assert workflow.count("test_loaded_tools_own_all_malformed_argument_refusals") >= 2
        assert "--no-build-isolation" in workflow
        assert "cmp uv.lock" in workflow
        assert "Path(phytolocus.__file__).resolve().is_relative_to" in workflow


def test_initial_release_trigger_is_exact() -> None:
    release = WORKFLOWS[1].read_text(encoding="utf-8")
    assert 'tags: ["v0.1.0"]' in release
    assert 'tags: ["v*"]' not in release


def test_public_capability_claims_match_the_closed_v01_entity_surface() -> None:
    exact_boundary = (
        "v0.1 has no semantic entities for experiments, sequencing runs, files, variants or "
        "structural variants, tissues, stages, assays, or conditions."
    )
    for path in SCOPE_DOCUMENTS:
        normalized = " ".join(path.read_text(encoding="utf-8").split())
        assert exact_boundary in normalized

    combined = " ".join(
        " ".join(path.read_text(encoding="utf-8").casefold().split())
        for path in PUBLIC_CAPABILITY_FILES
    )
    forbidden_overclaims = (
        "assembly/annotation/sample/run",
        "sample linked to the declared run",
        "sample, experiment, run, and file relationships",
        "sample/run, variant/phase",
        "structural-variant, haplotype, and transcript evidence",
        "tissue, stage, and assay scope",
        "required file records",
        "useful file receipts",
    )
    assert not any(phrase in combined for phrase in forbidden_overclaims)
