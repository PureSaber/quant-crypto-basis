from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from decimal import Decimal

import pytest
from conftest import load_messages, rewrite_messages
from quant_data_kit import FixedPoint, MarkPriceEvent, market_event_payload
from quant_data_kit.exceptions import ValidationError
from quant_execution import ExactAccountLedger, Fill, LedgerEventType, Side

from quant_crypto_basis.catalog import BTC_PERP, FIXTURE_EFFECTIVE_FROM
from quant_crypto_basis.fixtures import FixtureLoader
from quant_crypto_basis.runner import default_instruments, run_fixture_backtest
from quant_crypto_basis.strategy import BasisFundingConfig


@pytest.mark.parametrize("source", ["binance", "okx"])
def test_golden_e2e_uses_qexec_ledger_for_spot_perpetual_fees_funding_and_margin(
    source: str,
) -> None:
    run = run_fixture_backtest(source=source, run_id=f"golden-{source}")
    assert run.result.event_count == 17
    assert run.result.order_count == run.result.fill_count == 2
    assert {fill.instrument_id for fill in run.artifacts.fills} == {
        "CRYPTO:BTC-USDT:SPOT",
        "CRYPTO:BTC-USDT:PERP",
    }
    assert {fill.liquidity_role.value for fill in run.artifacts.fills} == {"maker"}
    assert len(run.artifacts.fees) == 2
    assert any(
        transaction.event_type is LedgerEventType.FUNDING
        for transaction in run.artifacts.ledger_transactions
    )
    assert set(run.snapshot.positions) == {
        "CRYPTO:BTC-USDT:SPOT",
        "CRYPTO:BTC-USDT:PERP",
    }
    assert run.snapshot.positions["CRYPTO:BTC-USDT:SPOT"].units > 0
    assert run.snapshot.positions["CRYPTO:BTC-USDT:PERP"].units < 0
    assert run.snapshot.initial_margin.units > run.snapshot.maintenance_margin.units > 0
    assert not run.snapshot.liquidation_required
    assert run.result.ledger_sha256 == run.artifacts.result.ledger_sha256


def test_identical_input_is_deterministic_across_three_complete_replays() -> None:
    runs = [
        run_fixture_backtest(source="binance", run_id="deterministic", seed=42) for _ in range(3)
    ]
    evidence = [
        (
            run.result.result_sha256,
            run.result.event_sha256,
            run.result.fill_sha256,
            run.result.ledger_sha256,
            hashlib.sha256(
                json.dumps(
                    [market_event_payload(event) for event in run.artifacts.market_events],
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ).encode()
            ).hexdigest(),
            run.snapshot.nav,
            run.artifacts.ledger_transactions,
        )
        for run in runs
    ]
    assert evidence[0] == evidence[1] == evidence[2]


def test_taker_path_and_minimum_quantity_fail_closed_are_qexec_decisions() -> None:
    taker = run_fixture_backtest(
        source="binance",
        run_id="taker",
        strategy_config=BasisFundingConfig(passive_limits=False),
    )
    assert taker.result.fill_count == 2
    assert {fill.liquidity_role.value for fill in taker.artifacts.fills} == {"taker"}

    with pytest.raises(ValidationError, match="basis pair execution incomplete.*MIN_QUANTITY"):
        run_fixture_backtest(
            source="binance",
            run_id="minimum",
            strategy_config=BasisFundingConfig(quantity=FixedPoint.from_decimal("0.001", 3)),
        )


def test_margin_rejection_and_liquidation_boundary_come_from_qexec() -> None:
    with pytest.raises(
        ValidationError,
        match="basis pair execution incomplete.*INSUFFICIENT_CASH.*INSUFFICIENT_MARGIN",
    ):
        run_fixture_backtest(
            source="binance",
            run_id="margin-rejection",
            initial_cash="100",
            strategy_config=BasisFundingConfig(quantity=FixedPoint.from_decimal("1000", 3)),
        )

    instruments = default_instruments()
    ledger = ExactAccountLedger(
        account_id="account",
        base_currency="USDT",
        instruments=instruments,
        initial_cash={"USDT": FixedPoint.from_decimal("100", 8)},
    )
    ledger.apply(
        Fill(
            fill_id="leveraged-fill",
            order_id="qexec-boundary-order",
            account_id="account",
            strategy_id="boundary",
            instrument_id=BTC_PERP,
            side=Side.BUY,
            quantity=FixedPoint.from_decimal("10", 3),
            price=FixedPoint.from_decimal("100", 2),
            event_time=FIXTURE_EFFECTIVE_FROM,
        )
    )
    crash_time = FIXTURE_EFFECTIVE_FROM + timedelta(seconds=1)
    ledger.mark(
        MarkPriceEvent(
            event_id="crash-mark",
            instrument_id=BTC_PERP,
            event_time=crash_time,
            received_at=crash_time,
            available_at=crash_time,
            source="binance",
            trading_day=crash_time.date(),
            session_id=f"binance-24x7-{BTC_PERP}",
            sequence=1,
            price=FixedPoint.from_decimal("1", 2),
        )
    )
    snapshot = ledger.snapshot()
    assert ledger.liquidation_required()
    assert snapshot.liquidation_required
    assert snapshot.nav.to_decimal() <= snapshot.maintenance_margin.to_decimal()


def test_one_leg_cash_rejection_fails_closed_without_returning_a_naked_run() -> None:
    with pytest.raises(
        ValidationError,
        match=r"action=open.*spot=rejected:0\.000/0\.100.*perpetual=filled:0\.100/0\.100",
    ):
        run_fixture_backtest(source="binance", run_id="one-leg-cash", initial_cash="50")


def test_one_leg_liquidity_failure_fails_closed(
    fixture_root,
) -> None:
    messages = load_messages(fixture_root, "binance")
    messages = [
        message
        for message in messages
        if not (
            message.get("e") == "trade"
            and message.get("s") == "BTCUSDT_PERP"
            and message.get("t") == 7004
        )
    ]
    rewrite_messages(fixture_root, "binance", messages)
    with pytest.raises(
        ValidationError,
        match=r"action=open.*spot=filled:0\.100/0\.100.*perpetual=accepted:0\.000/0\.100",
    ):
        run_fixture_backtest(
            source="binance",
            run_id="one-leg-liquidity",
            fixture_loader=FixtureLoader(fixture_root),
        )


def test_one_leg_partial_fill_fails_closed(fixture_root) -> None:
    messages = load_messages(fixture_root, "binance")
    for message in messages:
        if (
            message.get("e") == "trade"
            and message.get("s") == "BTCUSDT_PERP"
            and message.get("t") == 7004
        ):
            message["q"] = "0.050"
    rewrite_messages(fixture_root, "binance", messages)
    with pytest.raises(
        ValidationError,
        match=(
            r"action=open.*spot=filled:0\.100/0\.100.*"
            r"perpetual=partially_filled:0\.050/0\.100"
        ),
    ):
        run_fixture_backtest(
            source="binance",
            run_id="one-leg-partial",
            fixture_loader=FixtureLoader(fixture_root),
        )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"source": "kraken"}, "unsupported"),
        ({"run_id": ""}, "run_id"),
        ({"initial_cash": Decimal("0")}, "positive"),
    ],
)
def test_runner_configuration_fails_closed(kwargs: dict, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        run_fixture_backtest(**kwargs)
