"""Opt-in public-network contract tests for the pinned ToolUniverse source layer."""

from __future__ import annotations

import os

import pytest

from phytolocus_integrations import ToolUniverseAdapter, audit_collection, receipt_from_call

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("PHYTOLOCUS_RUN_LIVE") != "1",
        reason="set PHYTOLOCUS_RUN_LIVE=1 to call authoritative public services",
    ),
]

tooluniverse = pytest.importorskip("tooluniverse", reason="ToolUniverse is an optional extra")


@pytest.fixture(scope="module")
def source_adapter() -> ToolUniverseAdapter:
    universe = tooluniverse.ToolUniverse()
    universe.load_tools(
        include_tools=[
            "NCBIDatasets_get_genome_assembly",
            "NCBIDatasets_list_genomes_by_taxon",
            "NCBI_SRA_locate_run_files",
        ]
    )
    return ToolUniverseAdapter(universe)


def test_level_one_public_assembly_contract(source_adapter: ToolUniverseAdapter) -> None:
    result = source_adapter.call(
        "NCBIDatasets_get_genome_assembly",
        accession="GCF_000001735.4",
    )
    assert result["status"] == "success"
    assert result["data"]["organism_name"] == "Arabidopsis thaliana"
    assert result["data"]["tax_id"] == 3702
    assert result["data"]["assembly_name"] == "TAIR10.1"
    assert result["data"]["paired_accession"] == "GCA_000001735.2"


def test_level_two_public_census_fails_closed_on_missing_cursor(
    source_adapter: ToolUniverseAdapter,
) -> None:
    arguments = {"taxon": "3702", "limit": 3, "reference_only": False}
    result = source_adapter.call("NCBIDatasets_list_genomes_by_taxon", **arguments)
    assert result["status"] == "success"
    metadata = result["metadata"]
    audit = audit_collection(
        returned_count=metadata["returned"],
        reported_total=metadata["total_available"],
        next_cursor=metadata.get("next_page_token"),
        requested_limit=metadata["page_size"],
    )
    assert audit["coverage_state"] == "TRUNCATED_WITHOUT_CURSOR"
    receipt = receipt_from_call(
        tool_name="NCBIDatasets_list_genomes_by_taxon",
        arguments=arguments,
        response=result,
        source_name="NCBI Datasets API",
        source_release="v2",
        retrieved_at="2026-08-13T00:00:00Z",
        collection_audit=audit,
    )
    assert receipt["collection_audit"]["complete"] is False
    assert receipt["collection_audit"]["truncated_without_cursor"] is True


def test_level_three_public_run_file_has_size_checksum_and_location(
    source_adapter: ToolUniverseAdapter,
) -> None:
    result = source_adapter.call(
        "NCBI_SRA_locate_run_files",
        operation="locate_run_files",
        accessions="DRR234978",
    )
    assert result["status"] == "success"
    run = result["data"][0]
    assert run["accession"] == "DRR234978"
    assert run["file_count"] >= 1
    file_record = run["files"][0]
    assert file_record["size"] > 0
    assert len(file_record["md5"]) == 32
    assert any(location["link"].startswith("https://") for location in file_record["locations"])
