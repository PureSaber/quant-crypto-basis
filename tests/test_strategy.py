from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from quant_data_kit import FixedPoint, FundingRateEvent, QuoteEvent
from quant_data_kit.exceptions import ValidationError
from quant_execution import OrderType, Side, StrategyContext

from quant_crypto_basis.catalog import BTC_PERP, BTC_SPOT
from quant_crypto_basis.strategy import BasisFundingConfig, BasisFundingStrategy

UTC = timezone.utc
T0 = datetime(2026, 1, 2, tzinfo=UTC)


def _base(event_id: str, instrument_id: str, seconds: int) -> dict:
    event_time = T0 + timedelta(seconds=seconds)
    available_at = event_time + timedelta(milliseconds=1)
    return {
        "event_id": event_id,
        "instrument_id": instrument_id,
        "event_time": event_time,
        "received_at": available_at,
        "available_at": available_at,
        "source": "binance",
        "trading_day": event_time.date(),
        "session_id": f"binance-24x7-{instrument_id}",
    }


def _quote(event_id: str, instrument_id: str, seconds: int, bid: str, ask: str) -> QuoteEvent:
    return QuoteEvent(
        **_base(event_id, instrument_id, seconds),
        bid_price=FixedPoint.from_decimal(bid, 2),
        bid_quantity=FixedPoint.from_decimal("2", 3),
        ask_price=FixedPoint.from_decimal(ask, 2),
        ask_quantity=FixedPoint.from_decimal("2", 3),
    )


def _funding(event_id: str, seconds: int, rate: float = 0.001) -> FundingRateEvent:
    return FundingRateEvent(
        **_base(event_id, BTC_PERP, seconds),
        rate=rate,
        interval_start=T0 - timedelta(hours=8),
        interval_end=T0,
    )


def test_strategy_emits_only_paired_order_intents_from_on_event() -> None:
    strategy = BasisFundingStrategy()
    context = StrategyContext(run_id="run", account_id="account", strategy_id="strategy", seed=7)
    assert strategy.on_event(context, _quote("spot", BTC_SPOT, 1, "999", "1001")) == ()
    assert strategy.on_event(context, _quote("perp", BTC_PERP, 2, "1019", "1021")) == ()
    intents = strategy.on_event(context, _funding("funding", 3))
    assert len(intents) == 2
    assert {intent.instrument_id for intent in intents} == {BTC_SPOT, BTC_PERP}
    assert {intent.side for intent in intents} == {Side.BUY, Side.SELL}
    assert all(intent.order_type is OrderType.LIMIT for intent in intents)
    assert all(intent.created_at == T0 + timedelta(seconds=3, milliseconds=1) for intent in intents)
    assert not any(intent.reduce_only for intent in intents)
    assert not hasattr(strategy, "positions")
    assert not hasattr(strategy, "cash")
    assert not hasattr(strategy, "nav")


def test_strategy_exit_is_pairwise_and_only_perpetual_is_reduce_only() -> None:
    strategy = BasisFundingStrategy()
    context = StrategyContext(run_id="run", account_id="account", strategy_id="strategy", seed=7)
    strategy.on_event(context, _quote("spot", BTC_SPOT, 1, "999", "1001"))
    strategy.on_event(context, _quote("perp-high", BTC_PERP, 2, "1019", "1021"))
    assert len(strategy.on_event(context, _funding("open", 3))) == 2
    strategy.on_event(context, _quote("perp-low", BTC_PERP, 4, "1000", "1002"))
    closing = strategy.on_event(context, _funding("close", 5))
    assert [(item.instrument_id, item.side, item.reduce_only) for item in closing] == [
        (BTC_SPOT, Side.SELL, False),
        (BTC_PERP, Side.BUY, True),
    ]
    assert strategy.on_event(context, _funding("no-reopen", 6, rate=0.0)) == ()
    strategy.reset()
    assert strategy.on_event(context, _funding("missing-prices", 7)) == ()


def test_taker_configuration_generates_market_intents_without_prices() -> None:
    strategy = BasisFundingStrategy(BasisFundingConfig(passive_limits=False))
    context = StrategyContext(run_id="run", account_id="account", strategy_id="strategy", seed=7)
    strategy.on_event(context, _quote("spot", BTC_SPOT, 1, "999", "1001"))
    strategy.on_event(context, _quote("perp", BTC_PERP, 2, "1019", "1021"))
    intents = strategy.on_event(context, _funding("funding", 3))
    assert all(item.order_type is OrderType.MARKET for item in intents)
    assert all(item.limit_price is None for item in intents)


def test_config_validation_fails_closed() -> None:
    with pytest.raises(ValidationError, match="different"):
        BasisFundingConfig(perpetual_instrument_id=BTC_SPOT)
    with pytest.raises(ValidationError, match="entry_basis"):
        BasisFundingConfig(entry_basis_bps=Decimal("20"), exit_basis_bps=Decimal("25"))
    with pytest.raises(ValidationError, match="finite Decimal"):
        BasisFundingConfig(minimum_funding_rate=Decimal("NaN"))
    with pytest.raises(ValidationError, match="positive"):
        BasisFundingConfig(quantity=FixedPoint(0, 3))
