from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

tooluniverse = pytest.importorskip("tooluniverse", reason="ToolUniverse is an optional extra")
jsonschema = pytest.importorskip("jsonschema", reason="ToolUniverse installs jsonschema")

from phytolocus import compile_capsule  # noqa: E402
from phytolocus_integrations.tooluniverse import (  # noqa: E402
    receipt_from_call,
    study_receipt_from_call,
)
from phytolocus_tu.fixture import fixture_payload  # noqa: E402
from phytolocus_tu.tool import PhytoLocusTool  # noqa: E402

ROOT = Path(__file__).parents[1]
CONFIG = ROOT / "src" / "phytolocus_tu" / "data" / "phytolocus_tools.json"


def _configs() -> list[dict[str, Any]]:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def _config(name: str) -> dict[str, Any]:
    return next(config for config in _configs() if config["name"] == name)


def _empty_study() -> dict[str, Any]:
    return {
        "assemblies": [],
        "bound_memory_head_sha256": None,
        "correspondence_claims": [],
        "locus_models": [],
        "materials": [],
        "observations": [],
        "receipts": [],
        "schema_version": "phytolocus.study.v0.1",
        "sequence_objects": [],
        "species": "Synthetic plant model",
        "study_id": "synthetic-empty-study",
        "target_selection": {"allowed": False, "status": "PROHIBITED"},
        "transcript_models": [],
    }


def test_level_one_direct_tools_and_schemas() -> None:
    configs = _configs()
    assert {config["name"] for config in configs} == {
        "PhytoLocus_compile",
        "PhytoLocus_preflight",
        "PhytoLocus_validate_capsule",
    }
    for config in configs:
        jsonschema.Draft202012Validator.check_schema(config["return_schema"])
        assert len(config["name"]) <= 55
        assert len(config["description"]) >= 150
        assert config["type"] == "PhytoLocusTool"
        assert config["parameter"]["additionalProperties"] is True
        assert "oneOf" in config["return_schema"]
        output = PhytoLocusTool(config).run(config["test_examples"][0])
        jsonschema.validate(output, config["return_schema"])
        assert output["status"] == "success"
        assert output["target_selected"] is False
        assert output["target_selection"] == {"allowed": False, "status": "PROHIBITED"}


def test_level_one_errors_are_structured_and_redacted() -> None:
    for config in _configs():
        output = PhytoLocusTool(config).run({"private_absolute_path": "/secret/project"})
        jsonschema.validate(output, config["return_schema"])
        assert output["status"] == "error"
        assert output["error_code"] == "INVALID_ARGUMENTS"
        assert "/secret/project" not in json.dumps(output)


def test_level_two_registration_and_loaded_invocation() -> None:
    names = [config["name"] for config in _configs()]
    universe = tooluniverse.ToolUniverse()
    universe.load_tools(include_tools=names)
    assert set(names) <= set(universe.all_tool_dict)
    for name in names:
        result = getattr(universe.tools, name)(fixture_id="synthetic-locus-lock-v1")
        jsonschema.validate(result, _config(name)["return_schema"])
        assert result["status"] == "success"
        assert result["target_selected"] is False


def test_loaded_tools_own_all_malformed_argument_refusals() -> None:
    names = [config["name"] for config in _configs()]
    universe = tooluniverse.ToolUniverse()
    universe.load_tools(include_tools=names)
    caller_path = "/" + "private" + "/caller-secret"
    hostile_calls = (
        {"unexpected": caller_path},
        {"fixture_id": 123},
        {},
    )
    for name in names:
        for arguments in hostile_calls:
            result = getattr(universe.tools, name)(**arguments)
            jsonschema.validate(result, _config(name)["return_schema"])
            assert result["status"] == "error"
            assert result["error_code"] == "INVALID_ARGUMENTS"
            assert result["target_selected"] is False
            assert result["target_selection"] == {
                "allowed": False,
                "status": "PROHIBITED",
            }
            assert caller_path not in json.dumps(result)


@pytest.mark.parametrize("invalid_path", ["bad\x00path", "bad\ud800path"])
def test_loaded_capsule_tool_rejects_invalid_filesystem_strings(
    invalid_path: str,
) -> None:
    config = _config("PhytoLocus_validate_capsule")
    universe = tooluniverse.ToolUniverse()
    universe.load_tools(include_tools=[config["name"]])
    result = universe.tools.PhytoLocus_validate_capsule(
        capsule_dir=invalid_path,
        expected_capsule_digest="a" * 64,
    )
    jsonschema.validate(result, config["return_schema"])
    assert result["status"] == "error"
    assert result["error_code"] == "INVALID_ARGUMENTS"
    assert result["target_selected"] is False


def test_level_three_fixture_compile_is_held_and_deterministic() -> None:
    tool = PhytoLocusTool(_config("PhytoLocus_compile"))
    first = tool.run({"fixture_id": "synthetic-locus-lock-v1"})
    second = tool.run({"fixture_id": "synthetic-locus-lock-v1"})
    assert first == second
    assert first["data"]["producer_status"] == "HOLD"
    assert first["data"]["hold_reasons"] == [
        "BOUND_MEMORY_HEAD_MISSING",
        "COPY_UNRESOLVED",
        "EXTERNAL_RECEIPT_AUTHORITY_NOT_ESTABLISHED",
        "HAPLOTYPE_UNRESOLVED",
        "ISOFORM_UNRESOLVED",
    ]


def test_level_three_fixture_capsule_uses_full_semantic_recomputation() -> None:
    result = PhytoLocusTool(_config("PhytoLocus_validate_capsule")).run(
        {"fixture_id": "synthetic-locus-lock-v1"}
    )
    assert result["status"] == "success"
    assert result["data"]["capsule_valid"] is True
    assert result["data"]["snapshot_valid"] is True
    assert result["data"]["validation_subject"] == "OWNED_BYTE_SNAPSHOT"
    assert result["data"]["mutable_path_current_at_return_guaranteed"] is False
    assert result["data"]["capsule_semantics_trusted"] is True
    assert result["data"]["validation_mode"] == "STUDY_RECOMPUTED"
    assert result["data"]["producer_status"] == "HOLD"


def test_preflight_schema_accepts_core_valid_zero_counts() -> None:
    config = _config("PhytoLocus_preflight")
    result = PhytoLocusTool(config).run({"study": _empty_study()})
    jsonschema.validate(result, config["return_schema"])
    assert result["status"] == "success"
    counts = {key: value for key, value in result["data"].items() if key.endswith("_count")}
    assert counts
    assert set(counts.values()) == {0}


def test_tool_boundary_enforces_reference_and_collection_resource_limits() -> None:
    config = _config("PhytoLocus_preflight")
    oversized = _empty_study()
    oversized["receipts"] = [{}] * 10_001
    result = PhytoLocusTool(config).run({"study": oversized})
    jsonschema.validate(result, config["return_schema"])
    assert result["status"] == "error"
    assert result["error_code"] == "INVALID_ARGUMENTS"


def test_tool_boundary_accepts_source_call_provenance_and_rejects_kind_laundering() -> None:
    config = _config("PhytoLocus_preflight")
    study = fixture_payload()
    call = receipt_from_call(
        tool_name="Synthetic_public_source",
        arguments={"record": "synthetic-control"},
        response={"data": {"record": "synthetic-control"}, "status": "success"},
        source_name="synthetic-public-source",
        source_release="synthetic-v1",
        retrieved_at="2026-08-13T18:00:00Z",
    )
    source_receipt = study_receipt_from_call(
        call,
        receipt_id="receipt-material",
        subject_sha256=study["receipts"][0]["subject_sha256"],
    )
    study["receipts"][0] = source_receipt
    accepted = PhytoLocusTool(config).run({"study": study})
    jsonschema.validate(accepted, config["return_schema"])
    assert accepted["status"] == "success"

    source_receipt["kind"] = "MATERIAL"
    rejected = PhytoLocusTool(config).run({"study": study})
    jsonschema.validate(rejected, config["return_schema"])
    assert rejected["status"] == "error"
    assert rejected["error_code"] == "INVALID_ARGUMENTS"


def test_fixture_rejects_an_external_digest_parameter() -> None:
    config = _config("PhytoLocus_validate_capsule")
    result = PhytoLocusTool(config).run(
        {
            "expected_capsule_digest": "a" * 64,
            "fixture_id": "synthetic-locus-lock-v1",
        }
    )
    jsonschema.validate(result, config["return_schema"])
    assert result["status"] == "error"
    assert result["error_code"] == "INVALID_ARGUMENTS"


def test_validate_schema_closes_mode_specific_success_fields(tmp_path: Path) -> None:
    config = _config("PhytoLocus_validate_capsule")
    capsule = tmp_path / "capsule"
    study = fixture_payload()
    compiled = compile_capsule(study, capsule)
    digest_only = PhytoLocusTool(config).run(
        {
            "capsule_dir": str(capsule),
            "expected_capsule_digest": compiled["capsule_digest"],
        }
    )
    jsonschema.validate(digest_only, config["return_schema"])
    assert digest_only["status"] == "success"
    assert digest_only["data"]["validation_mode"] == "EXTERNAL_DIGEST_ANCHORED"
    assert not {"hold_reasons", "producer_status", "study_id"} & set(digest_only["data"])

    recomputed = PhytoLocusTool(config).run(
        {
            "capsule_dir": str(capsule),
            "expected_capsule_digest": compiled["capsule_digest"],
            "study": study,
        }
    )
    jsonschema.validate(recomputed, config["return_schema"])
    assert recomputed["status"] == "success"
    assert recomputed["data"]["validation_mode"] == (
        "STUDY_RECOMPUTED_AND_EXTERNAL_DIGEST_ANCHORED"
    )
    assert {"hold_reasons", "producer_status", "study_id"} <= set(recomputed["data"])

    digest_only["data"]["unexpected"] = True
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(digest_only, config["return_schema"])


def test_validate_capsule_error_codes_distinguish_invalid_and_unavailable(
    tmp_path: Path,
) -> None:
    config = _config("PhytoLocus_validate_capsule")
    missing = tmp_path / "caller-private-missing"
    unavailable = PhytoLocusTool(config).run(
        {"capsule_dir": str(missing), "expected_capsule_digest": "a" * 64}
    )
    jsonschema.validate(unavailable, config["return_schema"])
    assert unavailable["error_code"] == "CAPSULE_UNAVAILABLE"
    assert str(tmp_path) not in json.dumps(unavailable)

    malformed = tmp_path / "caller-private-malformed"
    malformed.mkdir()
    invalid = PhytoLocusTool(config).run(
        {"capsule_dir": str(malformed), "expected_capsule_digest": "a" * 64}
    )
    jsonschema.validate(invalid, config["return_schema"])
    assert invalid["error_code"] == "CAPSULE_INVALID"
    assert str(tmp_path) not in json.dumps(invalid)


def test_loaded_capsule_tool_attributes_malformed_study_to_arguments(
    tmp_path: Path,
) -> None:
    config = _config("PhytoLocus_validate_capsule")
    capsule = tmp_path / "capsule"
    report = compile_capsule(fixture_payload(), capsule)
    universe = tooluniverse.ToolUniverse()
    universe.load_tools(include_tools=[config["name"]])
    result = universe.tools.PhytoLocus_validate_capsule(
        capsule_dir=str(capsule),
        expected_capsule_digest=report["capsule_digest"],
        study={},
    )
    jsonschema.validate(result, config["return_schema"])
    assert result["status"] == "error"
    assert result["error_code"] == "INVALID_ARGUMENTS"
    assert result["target_selected"] is False


def test_loaded_capsule_tool_freezes_study_before_recomputation(tmp_path: Path) -> None:
    config = _config("PhytoLocus_validate_capsule")
    capsule = tmp_path / "capsule"
    study = fixture_payload()
    report = compile_capsule(study, capsule)

    class SwitchingStudy(dict[str, Any]):
        iterations = 0

        def __iter__(self):  # type: ignore[no-untyped-def]
            self.iterations += 1
            if self.iterations >= 3:
                self["schema_version"] = "switched-after-preflight"
            return super().__iter__()

    mutable_study = SwitchingStudy(study)
    universe = tooluniverse.ToolUniverse()
    universe.load_tools(include_tools=[config["name"]])
    result = universe.tools.PhytoLocus_validate_capsule(
        capsule_dir=str(capsule),
        expected_capsule_digest=report["capsule_digest"],
        study=mutable_study,
    )
    jsonschema.validate(result, config["return_schema"])
    assert result["status"] == "success"
    assert result["data"]["capsule_semantics_trusted"] is True
    assert mutable_study.iterations == 2


def test_external_capsule_requires_semantic_or_external_anchor(tmp_path: Path) -> None:
    capsule = tmp_path / "caller-private-capsule"
    capsule.mkdir()
    result = PhytoLocusTool(_config("PhytoLocus_validate_capsule")).run(
        {"capsule_dir": str(capsule)}
    )
    jsonschema.validate(result, _config("PhytoLocus_validate_capsule")["return_schema"])
    assert result["status"] == "error"
    assert result["error_code"] == "INVALID_ARGUMENTS"
    assert str(tmp_path) not in json.dumps(result)
    assert "caller-private-capsule" not in json.dumps(result)
