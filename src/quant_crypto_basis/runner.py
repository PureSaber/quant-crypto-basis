"""Certified QExec-only replay assembly for offline crypto fixtures."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType

from quant_data_kit import FixedPoint, InstrumentSpec
from quant_data_kit.exceptions import ValidationError
from quant_execution import (
    AccountSnapshot,
    DeterministicBroker,
    DeterministicRunEngine,
    ExactAccountLedger,
    RuleBookRiskGate,
    RunArtifacts,
    RunResult,
    TradeBBOModel,
)

from quant_crypto_basis.catalog import default_instrument_master
from quant_crypto_basis.fixtures import (
    CrossSourceQualityReport,
    FixtureBatch,
    FixtureLoader,
    load_certified_fixtures,
)
from quant_crypto_basis.strategy import BasisFundingConfig, BasisFundingStrategy

ACCOUNT_ID = "crypto-research-account"
STRATEGY_ID = "spot-perpetual-basis-funding-v1"


@dataclass(frozen=True, slots=True)
class EventStageSnapshot:
    """One QExec ledger snapshot after all events at an available-time coordinate."""

    event_ids: tuple[str, ...]
    event_types: tuple[str, ...]
    event_time: datetime
    snapshot: AccountSnapshot
    order_count: int
    fill_count: int
    fee_count: int
    funding_count: int
    ledger_transaction_count: int
    fee_total: FixedPoint
    ledger_sha256: str


@dataclass(frozen=True, slots=True)
class CertifiedBacktest:
    result: RunResult
    artifacts: RunArtifacts
    snapshot: AccountSnapshot
    instruments: Mapping[str, InstrumentSpec]
    batch: FixtureBatch
    quality_report: CrossSourceQualityReport
    strategy_config: BasisFundingConfig
    initial_cash: FixedPoint
    event_trace: tuple[EventStageSnapshot, ...]


def run_fixture_backtest(
    *,
    source: str = "binance",
    run_id: str = "crypto-basis-fixture-v1",
    seed: int = 7,
    strategy_config: BasisFundingConfig | None = None,
    initial_cash: Decimal | str = Decimal("100000"),
    fixture_loader: FixtureLoader | None = None,
) -> CertifiedBacktest:
    """Run one deterministic fixture replay through the frozen QExec fact path."""
    if source not in {"binance", "okx"}:
        raise ValidationError(f"unsupported fixture source: {source!r}")
    if not run_id.strip():
        raise ValidationError("run_id is required")
    master = (fixture_loader or FixtureLoader()).instrument_master
    loader = fixture_loader or FixtureLoader(instrument_master=master)
    batches, quality = load_certified_fixtures(loader)
    batch = batches[source]
    instruments = dict(master.instruments)
    cash = FixedPoint.from_decimal(initial_cash, 8)
    if not cash.is_positive():
        raise ValidationError("initial_cash must be positive")
    config = strategy_config or BasisFundingConfig()
    strategy = BasisFundingStrategy(config)
    ledger = ExactAccountLedger(
        account_id=ACCOUNT_ID,
        base_currency="USDT",
        instruments=instruments,
        initial_cash={"USDT": cash},
        money_scale=8,
    )
    broker = DeterministicBroker()
    risk_gate = RuleBookRiskGate(instruments=instruments, ledger=ledger, money_scale=8)
    matching_model = TradeBBOModel(instruments)
    engine = DeterministicRunEngine(
        run_id=run_id,
        account_id=ACCOUNT_ID,
        strategy_id=STRATEGY_ID,
        strategy=strategy,
        broker=broker,
        risk_gate=risk_gate,
        matching_model=matching_model,
        ledger=ledger,
    )
    if any(
        component.sends_live_orders
        for component in (strategy, broker, risk_gate, matching_model, ledger, engine)
    ):
        raise ValidationError("certification component unexpectedly exposes live order sending")
    stage_ends = [
        index
        for index in range(1, len(batch.events) + 1)
        if index == len(batch.events)
        or batch.events[index].available_at != batch.events[index - 1].available_at
    ]
    event_trace: list[EventStageSnapshot] = []
    prior_end = 0
    result: RunResult | None = None
    for stage_end in stage_ends:
        result = engine.replay(batch.events[:stage_end], seed)
        if engine.artifacts is None:
            raise ValidationError("QExec replay completed without immutable artifacts")
        stage_events = batch.events[prior_end:stage_end]
        stage_time = stage_events[-1].available_at
        stage_snapshot = ledger.snapshot(stage_time)
        ledger.assert_nav_residual(stage_snapshot)
        fee_total = sum(
            (fee.amount.to_decimal() for fee in engine.artifacts.fees),
            Decimal(0),
        )
        funding_count = sum(
            transaction.event_type.value == "funding"
            for transaction in engine.artifacts.ledger_transactions
        )
        event_trace.append(
            EventStageSnapshot(
                event_ids=tuple(event.event_id for event in stage_events),
                event_types=tuple(event.event_type for event in stage_events),
                event_time=stage_time,
                snapshot=stage_snapshot,
                order_count=result.order_count,
                fill_count=result.fill_count,
                fee_count=len(engine.artifacts.fees),
                funding_count=funding_count,
                ledger_transaction_count=len(engine.artifacts.ledger_transactions),
                fee_total=FixedPoint.from_decimal(fee_total, 8),
                ledger_sha256=result.ledger_sha256,
            )
        )
        prior_end = stage_end
    if result is None or engine.artifacts is None:
        raise ValidationError("fixture replay requires at least one event stage")
    snapshot = ledger.snapshot()
    ledger.assert_nav_residual(snapshot)
    if snapshot != event_trace[-1].snapshot:
        raise ValidationError("final event-stage snapshot differs from QExec ledger final snapshot")
    return CertifiedBacktest(
        result=result,
        artifacts=engine.artifacts,
        snapshot=snapshot,
        instruments=MappingProxyType(instruments),
        batch=batch,
        quality_report=quality,
        strategy_config=config,
        initial_cash=cash,
        event_trace=tuple(event_trace),
    )


def default_instruments() -> Mapping[str, InstrumentSpec]:
    """Expose the frozen master without creating an execution state."""
    return default_instrument_master().instruments
