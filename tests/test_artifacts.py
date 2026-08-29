from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

import pandas as pd
import pytest
from quant_data_kit import FixedPoint
from quant_data_kit.exceptions import ValidationError
from quant_lab import load_and_validate_standard_run
from quant_lab.contracts_v2 import BACKTEST_LEDGER_PROFILE, PROFILE_ARTIFACTS_V2

import quant_crypto_basis.artifacts as artifacts
from quant_crypto_basis.artifacts import (
    CATALOG_DATASET,
    FIXTURE_DATASETS,
    INTERNAL_DEPENDENCIES,
    _metadata_decimal,
    _snapshot_position_rows,
    build_standard_frames,
    write_certified_standard_run,
)
from quant_crypto_basis.runner import run_fixture_backtest


def test_standard_v2_backtest_ledger_is_complete_and_read_back(
    tmp_path: Path,
    clean_git_repo: tuple[Path, str],
    monkeypatch,
) -> None:
    repository, head = clean_git_repo
    monkeypatch.chdir(repository)
    run = run_fixture_backtest(source="binance", run_id="standard-v2")
    target = tmp_path / "run"
    manifest = write_certified_standard_run(
        run,
        target,
        code_version=head,
    )
    loaded = load_and_validate_standard_run(target)
    assert loaded == manifest
    assert manifest.profile == BACKTEST_LEDGER_PROFILE
    assert manifest.internal_dependencies == INTERNAL_DEPENDENCIES
    assert manifest.code_version == head
    assert manifest.time_range["start"] == "2026-01-02T00:00:01.001000+00:00"
    assert manifest.time_range["end"] == "2026-01-02T00:00:09.501000+00:00"
    assert "1970-01-01" not in json.dumps(manifest.time_range)
    required = {record.name for record in manifest.artifacts if record.required}
    assert required == {"config", "metrics", *PROFILE_ARTIFACTS_V2[BACKTEST_LEDGER_PROFILE]}
    row_counts = {record.name: record.rows for record in manifest.artifacts}
    assert row_counts["returns"] == row_counts["portfolio_snapshots"] == 16
    assert row_counts["positions"] > 2 and row_counts["margin"] > 1
    assert row_counts["orders"] == row_counts["fills"] == 2
    assert row_counts["costs"] == 3
    assert manifest.dataset_snapshots == {
        CATALOG_DATASET: f"sha256:{run.quality_report.catalog_sha256}",
        FIXTURE_DATASETS["binance"]: (f"sha256:{run.quality_report.fixture_sha256['binance']}"),
        FIXTURE_DATASETS["okx"]: f"sha256:{run.quality_report.fixture_sha256['okx']}",
    }
    assert manifest.lineage["config"] == [f"dataset:{CATALOG_DATASET}"]
    assert manifest.lineage["metrics"] == [
        f"dataset:{CATALOG_DATASET}",
        f"dataset:{FIXTURE_DATASETS['binance']}",
        f"dataset:{FIXTURE_DATASETS['okx']}",
    ]
    assert manifest.lineage["fills"] == [
        f"dataset:{CATALOG_DATASET}",
        f"dataset:{FIXTURE_DATASETS['binance']}",
    ]
    metrics = json.loads((target / "standard" / "v2" / "metrics.json").read_text("utf-8"))
    assert metrics["qa_dual_source_complete"] is True
    assert metrics["qa_provider_count"] == 2
    assert metrics["qa_common_instrument_count"] == 4
    assert metrics["qa_binance_row_count"] == metrics["qa_okx_row_count"] == 17
    assert metrics["qa_binance_event_type_count"] == metrics["qa_okx_event_type_count"] == 6
    assert metrics["qa_price_equality_required"] is False
    assert "l2-btc-spot-fixture-certified" in manifest.capabilities
    assert "l2-not-certified-for-eth-or-perpetual" in manifest.capabilities
    assert manifest.tags["l2_scope"] == "BTC spot fixture-certified only"


def test_standard_frames_carry_qexec_funding_fees_marks_cash_and_margin() -> None:
    run = run_fixture_backtest(source="okx", run_id="facts")
    frames = build_standard_frames(run)
    assert set(frames) == set(PROFILE_ARTIFACTS_V2[BACKTEST_LEDGER_PROFILE])
    assert set(frames["costs"]["cost_type"]) == {"maker", "funding"}
    funding = frames["costs"].loc[frames["costs"]["cost_type"] == "funding"].iloc[0]
    assert pd.isna(funding["fill_id"])
    assert funding["amount_units"] < 0
    assert {"fill", "fee", "funding", "fx_conversion"} <= set(frames["cash_ledger"]["event_type"])
    final_positions = frames["positions"].loc[
        frames["positions"]["event_time"] == run.snapshot.event_time
    ]
    assert len(final_positions) == 2
    assert final_positions["mark_price_units"].gt(0).all()
    final_margin = frames["margin"].iloc[-1]
    assert final_margin["initial_margin_units"] == run.snapshot.initial_margin.units
    assert final_margin["maintenance_margin_units"] == run.snapshot.maintenance_margin.units
    final_portfolio = frames["portfolio_snapshots"].iloc[-1]
    assert final_portfolio["nav_units"] == run.snapshot.nav.units


def test_standard_v2_artifact_hashes_are_deterministic_and_directory_is_immutable(
    tmp_path: Path,
    clean_git_repo: tuple[Path, str],
    monkeypatch,
) -> None:
    repository, head = clean_git_repo
    monkeypatch.chdir(repository)
    runs = [
        run_fixture_backtest(source="binance", run_id="deterministic-artifacts", seed=11)
        for _ in range(3)
    ]
    manifests = [
        write_certified_standard_run(run, tmp_path / name, code_version=head)
        for run, name in zip(runs, ("first", "second", "third"), strict=True)
    ]
    run_hashes = [
        (run.result.event_sha256, run.result.fill_sha256, run.result.ledger_sha256) for run in runs
    ]
    assert run_hashes[0] == run_hashes[1] == run_hashes[2]
    artifact_hashes = [
        {record.name: record.sha256 for record in manifest.artifacts} for manifest in manifests
    ]
    assert artifact_hashes[0] == artifact_hashes[1] == artifact_hashes[2]
    with pytest.raises(FileExistsError, match="immutable"):
        write_certified_standard_run(
            runs[0],
            tmp_path / "first",
            code_version=head,
        )


@pytest.mark.parametrize("code_version", ["main", "0" * 40])
def test_standard_writer_rejects_non_head_code_version(
    tmp_path: Path,
    clean_git_repo: tuple[Path, str],
    code_version: str,
    monkeypatch,
) -> None:
    repository, _ = clean_git_repo
    monkeypatch.chdir(repository)
    run = run_fixture_backtest(source="binance", run_id="bad-code")
    with pytest.raises(ValidationError, match="full lowercase|does not match"):
        write_certified_standard_run(
            run,
            tmp_path / "bad",
            code_version=code_version,
        )


def test_standard_writer_rejects_incomplete_or_mismatched_cross_source_provenance(
    tmp_path: Path,
    clean_git_repo: tuple[Path, str],
    monkeypatch,
) -> None:
    repository, head = clean_git_repo
    monkeypatch.chdir(repository)
    run = run_fixture_backtest(source="binance", run_id="bad-qa")
    incomplete = replace(
        run,
        quality_report=replace(
            run.quality_report,
            fixture_sha256=MappingProxyType(
                {"binance": run.quality_report.fixture_sha256["binance"]}
            ),
        ),
    )
    with pytest.raises(ValidationError, match="complete Binance and OKX"):
        write_certified_standard_run(incomplete, tmp_path / "incomplete", code_version=head)

    mismatched = replace(
        run,
        quality_report=replace(
            run.quality_report,
            fixture_sha256=MappingProxyType(
                {
                    "binance": "0" * 64,
                    "okx": run.quality_report.fixture_sha256["okx"],
                }
            ),
        ),
    )
    with pytest.raises(ValidationError, match="selected fixture hash differs"):
        write_certified_standard_run(mismatched, tmp_path / "mismatched", code_version=head)


def test_standard_frame_boundaries_fail_closed_for_currency_zero_position_and_settlement() -> None:
    run = run_fixture_backtest(source="binance", run_id="boundary-guards")
    with pytest.raises(ValidationError, match="requires USDT base currency"):
        build_standard_frames(replace(run, snapshot=replace(run.snapshot, base_currency="BTC")))

    zero = replace(
        run.snapshot,
        positions={"CRYPTO:BTC-USDT:SPOT": FixedPoint.from_decimal("0", 3)},
    )
    assert _snapshot_position_rows(zero.event_time, zero, run.instruments) == []

    instrument_id = "CRYPTO:BTC-USDT:SPOT"
    incompatible = replace(run.instruments[instrument_id], settlement_currency="BTC")
    with pytest.raises(ValidationError, match="unversioned FX rate"):
        _snapshot_position_rows(
            run.snapshot.event_time,
            run.snapshot,
            {**run.instruments, instrument_id: incompatible},
        )


def test_standard_margin_and_metadata_guards_preserve_qexec_reconciliation() -> None:
    run = run_fixture_backtest(source="okx", run_id="reconciliation-guards")
    bad_initial = replace(
        run.event_trace[-1],
        snapshot=replace(
            run.event_trace[-1].snapshot,
            initial_margin=FixedPoint.from_decimal(
                run.event_trace[-1].snapshot.initial_margin.to_decimal() + 1, 8
            ),
        ),
    )
    with pytest.raises(ValidationError, match="initial margin differs"):
        build_standard_frames(replace(run, event_trace=(*run.event_trace[:-1], bad_initial)))

    bad_maintenance = replace(
        run.event_trace[-1],
        snapshot=replace(
            run.event_trace[-1].snapshot,
            maintenance_margin=FixedPoint.from_decimal(
                run.event_trace[-1].snapshot.maintenance_margin.to_decimal() + 1, 8
            ),
        ),
    )
    with pytest.raises(ValidationError, match="maintenance margin differs"):
        build_standard_frames(replace(run, event_trace=(*run.event_trace[:-1], bad_maintenance)))

    spec = run.instruments["CRYPTO:BTC-USDT:PERP"]
    with pytest.raises(ValidationError, match="initial_margin_rate.*required"):
        _metadata_decimal(replace(spec, metadata={}), "initial_margin_rate")
    with pytest.raises(ValidationError, match="must be finite"):
        _metadata_decimal(
            replace(spec, metadata={**spec.metadata, "initial_margin_rate": "NaN"}),
            "initial_margin_rate",
        )


def test_standard_writer_rejects_readback_mismatch(
    tmp_path: Path,
    clean_git_repo: tuple[Path, str],
    monkeypatch,
) -> None:
    repository, head = clean_git_repo
    monkeypatch.chdir(repository)
    run = run_fixture_backtest(source="binance", run_id="bad-readback")
    monkeypatch.setattr(artifacts, "load_and_validate_standard_run", lambda _: object())
    with pytest.raises(ValidationError, match="readback differs"):
        write_certified_standard_run(run, tmp_path / "bad-readback", code_version=head)
