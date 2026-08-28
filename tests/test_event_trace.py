from __future__ import annotations

import json
from pathlib import Path

from quant_crypto_basis.artifacts import build_standard_frames
from quant_crypto_basis.catalog import BTC_PERP, BTC_SPOT
from quant_crypto_basis.runner import run_fixture_backtest


def _quantity(stage, instrument_id: str) -> str:
    value = stage.snapshot.positions.get(instrument_id)
    return str(value.to_decimal()) if value is not None else "0"


def _golden_rows(run) -> list[list]:
    return [
        [
            ",".join(stage.event_ids),
            stage.event_time.isoformat().replace("+00:00", "Z"),
            stage.order_count,
            stage.fill_count,
            stage.fee_count,
            stage.funding_count,
            stage.ledger_transaction_count,
            str(stage.snapshot.cash_balances["USDT"].to_decimal()),
            _quantity(stage, BTC_SPOT),
            _quantity(stage, BTC_PERP),
            str(stage.snapshot.nav.to_decimal()),
            str(stage.snapshot.initial_margin.to_decimal()),
            str(stage.snapshot.maintenance_margin.to_decimal()),
            stage.ledger_sha256,
        ]
        for stage in run.event_trace
    ]


def test_event_stage_trace_matches_committed_golden_exactly() -> None:
    expected = json.loads(
        (Path(__file__).parent / "golden" / "event_trace.json").read_text(encoding="utf-8")
    )
    run = run_fixture_backtest(source="binance", run_id="golden-trace", seed=7)
    assert len(run.event_trace) == 16
    assert _golden_rows(run) == expected["rows"]


def test_trace_proves_fill_fee_funding_mark_position_and_nav_changes() -> None:
    run = run_fixture_backtest(source="binance", run_id="trace-audit", seed=7)
    stages = run.event_trace
    spot_fill = next(stage for stage in stages if stage.fill_count == 1)
    before_spot_fill = stages[stages.index(spot_fill) - 1]
    assert spot_fill.fee_count == 1 and before_spot_fill.fee_count == 0
    assert BTC_SPOT in spot_fill.snapshot.positions
    assert spot_fill.snapshot.nav.units < before_spot_fill.snapshot.nav.units

    perp_fill = next(stage for stage in stages if stage.fill_count == 2)
    assert BTC_PERP in perp_fill.snapshot.positions
    assert perp_fill.snapshot.initial_margin.units > 0
    assert perp_fill.ledger_transaction_count == 5

    funding = next(stage for stage in stages if stage.funding_count == 1)
    before_funding = stages[stages.index(funding) - 1]
    assert (
        funding.snapshot.cash_balances["USDT"].units
        > before_funding.snapshot.cash_balances["USDT"].units
    )
    assert funding.snapshot.nav.units > before_funding.snapshot.nav.units
    assert funding.ledger_transaction_count == before_funding.ledger_transaction_count + 1

    final_mark = stages[-1]
    assert final_mark.event_types == ("mark_price",)
    assert final_mark.snapshot.cash_balances == funding.snapshot.cash_balances
    assert final_mark.snapshot.nav.units != funding.snapshot.nav.units
    assert final_mark.snapshot.initial_margin.units != funding.snapshot.initial_margin.units
    assert final_mark.snapshot == run.snapshot


def test_standard_v2_research_frames_use_every_qexec_event_stage() -> None:
    run = run_fixture_backtest(source="binance", run_id="trace-frames", seed=7)
    frames = build_standard_frames(run)
    trace_times = [stage.event_time for stage in run.event_trace]
    assert frames["returns"]["event_time"].tolist() == trace_times
    assert frames["portfolio_snapshots"]["event_time"].tolist() == trace_times
    assert len(frames["returns"]) == len(frames["portfolio_snapshots"]) == 16
    assert len(frames["positions"]) > 2
    assert len(frames["margin"]) > 1
    assert set(frames["positions"]["instrument_id"]) == {BTC_SPOT, BTC_PERP}
    assert frames["returns"].iloc[-1]["nav_units"] == run.snapshot.nav.units
    assert frames["portfolio_snapshots"].iloc[-1]["nav_units"] == run.snapshot.nav.units
