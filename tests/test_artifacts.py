from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from quant_lab import load_and_validate_standard_run
from quant_lab.contracts_v2 import BACKTEST_LEDGER_PROFILE, PROFILE_ARTIFACTS_V2

from quant_crypto_basis.artifacts import (
    INTERNAL_DEPENDENCIES,
    build_standard_frames,
    write_certified_standard_run,
)
from quant_crypto_basis.runner import run_fixture_backtest

CODE_VERSION = "6df91eca238542c9c9d3013f733e7dc7b94f19dc"


def test_standard_v2_backtest_ledger_is_complete_and_read_back(tmp_path: Path) -> None:
    run = run_fixture_backtest(source="binance", run_id="standard-v2")
    target = tmp_path / "run"
    manifest = write_certified_standard_run(run, target, code_version=CODE_VERSION)
    loaded = load_and_validate_standard_run(target)
    assert loaded == manifest
    assert manifest.profile == BACKTEST_LEDGER_PROFILE
    assert manifest.internal_dependencies == INTERNAL_DEPENDENCIES
    assert manifest.code_version == CODE_VERSION
    required = {record.name for record in manifest.artifacts if record.required}
    assert required == {"config", "metrics", *PROFILE_ARTIFACTS_V2[BACKTEST_LEDGER_PROFILE]}
    row_counts = {record.name: record.rows for record in manifest.artifacts}
    assert row_counts["returns"] == row_counts["portfolio_snapshots"] == 16
    assert row_counts["positions"] > 2 and row_counts["margin"] > 1
    assert row_counts["orders"] == row_counts["fills"] == 2
    assert row_counts["costs"] == 3


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
) -> None:
    run = run_fixture_backtest(source="binance", run_id="deterministic-artifacts", seed=11)
    first = write_certified_standard_run(run, tmp_path / "first", code_version=CODE_VERSION)
    second = write_certified_standard_run(run, tmp_path / "second", code_version=CODE_VERSION)
    first_hashes = {record.name: record.sha256 for record in first.artifacts}
    second_hashes = {record.name: record.sha256 for record in second.artifacts}
    assert first_hashes == second_hashes
    with pytest.raises(FileExistsError, match="immutable"):
        write_certified_standard_run(run, tmp_path / "first", code_version=CODE_VERSION)


def test_standard_writer_rejects_non_commit_code_version(tmp_path: Path) -> None:
    run = run_fixture_backtest(source="binance", run_id="bad-code")
    with pytest.raises(ValueError, match="full lowercase Git commit"):
        write_certified_standard_run(run, tmp_path / "bad", code_version="main")
