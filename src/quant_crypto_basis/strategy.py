"""Explicit spot-long/perpetual-short basis and funding research strategy."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from quant_data_kit import (
    FixedPoint,
    FundingRateEvent,
    MarketEvent,
    MarkPriceEvent,
    QuoteEvent,
    TradeEvent,
)
from quant_data_kit.exceptions import ValidationError
from quant_execution import (
    OrderIntent,
    OrderType,
    Side,
    StrategyContext,
    TimeInForce,
)

from quant_crypto_basis.catalog import BTC_PERP, BTC_SPOT


def _decimal(value: FixedPoint) -> Decimal:
    return value.to_decimal()


@dataclass(frozen=True, slots=True)
class BasisFundingConfig:
    spot_instrument_id: str = BTC_SPOT
    perpetual_instrument_id: str = BTC_PERP
    quantity: FixedPoint = FixedPoint(100, 3)
    entry_basis_bps: Decimal = Decimal("100")
    exit_basis_bps: Decimal = Decimal("25")
    minimum_funding_rate: Decimal = Decimal("0.0001")
    passive_limits: bool = True

    def __post_init__(self) -> None:
        if not self.spot_instrument_id.strip() or not self.perpetual_instrument_id.strip():
            raise ValidationError("basis strategy instrument ids are required")
        if self.spot_instrument_id == self.perpetual_instrument_id:
            raise ValidationError("basis strategy legs must be different instruments")
        if not isinstance(self.quantity, FixedPoint) or not self.quantity.is_positive():
            raise ValidationError("basis strategy quantity must be a positive FixedPoint")
        for field_name in ("entry_basis_bps", "exit_basis_bps", "minimum_funding_rate"):
            value = getattr(self, field_name)
            if not isinstance(value, Decimal) or not value.is_finite():
                raise ValidationError(f"{field_name} must be a finite Decimal")
        if self.entry_basis_bps <= self.exit_basis_bps:
            raise ValidationError("entry_basis_bps must exceed exit_basis_bps")
        if not isinstance(self.passive_limits, bool):
            raise ValidationError("passive_limits must be boolean")


class BasisFundingStrategy:
    """Signal state only; QExec remains the sole owner of positions, cash and NAV."""

    sends_live_orders = False

    def __init__(self, config: BasisFundingConfig | None = None) -> None:
        self.config = config or BasisFundingConfig()
        self.reset()

    def reset(self) -> None:
        self._prices: dict[str, FixedPoint] = {}
        self._regime_open = False

    def on_event(self, context: StrategyContext, event: MarketEvent) -> tuple[OrderIntent, ...]:
        price = self._event_price(event)
        if price is not None and event.instrument_id in {
            self.config.spot_instrument_id,
            self.config.perpetual_instrument_id,
        }:
            self._prices[event.instrument_id] = price
        if not isinstance(event, FundingRateEvent):
            return ()
        if event.instrument_id != self.config.perpetual_instrument_id:
            return ()
        spot = self._prices.get(self.config.spot_instrument_id)
        perpetual = self._prices.get(self.config.perpetual_instrument_id)
        if spot is None or perpetual is None:
            return ()
        basis_bps = (_decimal(perpetual) / _decimal(spot) - Decimal(1)) * Decimal(10_000)
        funding_rate = Decimal(str(event.rate))
        if not self._regime_open:
            if (
                basis_bps < self.config.entry_basis_bps
                or funding_rate < self.config.minimum_funding_rate
            ):
                return ()
            self._regime_open = True
            return self._pair_intents(context, event, closing=False, spot=spot, perpetual=perpetual)
        if basis_bps > self.config.exit_basis_bps:
            return ()
        self._regime_open = False
        return self._pair_intents(context, event, closing=True, spot=spot, perpetual=perpetual)

    def _pair_intents(
        self,
        context: StrategyContext,
        event: FundingRateEvent,
        *,
        closing: bool,
        spot: FixedPoint,
        perpetual: FixedPoint,
    ) -> tuple[OrderIntent, OrderIntent]:
        action = "close" if closing else "open"
        order_type = OrderType.LIMIT if self.config.passive_limits else OrderType.MARKET
        shared = {
            "account_id": context.account_id,
            "strategy_id": context.strategy_id,
            "quantity": self.config.quantity,
            "order_type": order_type,
            "time_in_force": TimeInForce.GTC,
            "created_at": event.available_at,
        }
        spot_side = Side.SELL if closing else Side.BUY
        perpetual_side = Side.BUY if closing else Side.SELL
        return (
            OrderIntent(
                idempotency_key=f"{context.run_id}:{event.event_id}:{action}:spot",
                instrument_id=self.config.spot_instrument_id,
                side=spot_side,
                limit_price=spot if self.config.passive_limits else None,
                reduce_only=False,
                **shared,
            ),
            OrderIntent(
                idempotency_key=f"{context.run_id}:{event.event_id}:{action}:perpetual",
                instrument_id=self.config.perpetual_instrument_id,
                side=perpetual_side,
                limit_price=perpetual if self.config.passive_limits else None,
                reduce_only=closing,
                **shared,
            ),
        )

    @staticmethod
    def _event_price(event: MarketEvent) -> FixedPoint | None:
        if isinstance(event, (TradeEvent, MarkPriceEvent)):
            return event.price
        if isinstance(event, QuoteEvent):
            midpoint = (_decimal(event.bid_price) + _decimal(event.ask_price)) / 2
            return FixedPoint.from_decimal(midpoint, event.bid_price.scale)
        return None
