"""QLab standard/v2 backtest-ledger reporting from QExec immutable facts."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
from quant_data_kit import FixedPoint, InstrumentSpec
from quant_data_kit.exceptions import ValidationError
from quant_execution import AccountSnapshot, LedgerEventType
from quant_lab import RunManifestV2, load_and_validate_standard_run, write_standard_run_v2
from quant_lab.contracts_v2 import ARTIFACT_SCHEMAS_V2, BACKTEST_LEDGER_PROFILE

from quant_crypto_basis.catalog import INSTRUMENT_MASTER_VERSION
from quant_crypto_basis.provenance import resolve_clean_head
from quant_crypto_basis.runner import ACCOUNT_ID, STRATEGY_ID, CertifiedBacktest

INTERNAL_DEPENDENCIES = {
    "quant-data-kit": "v0.5.0",
    "quant-execution": "v0.2.0",
    "quant-lab": "v0.3.0",
}
CATALOG_DATASET = "crypto-fixture-catalog-index"
FIXTURE_DATASETS = {
    "binance": "binance-offline-fixture",
    "okx": "okx-offline-fixture",
}


def _decimal(value: FixedPoint) -> Decimal:
    return value.to_decimal()


def _fixed(value: Decimal, scale: int = 8) -> FixedPoint:
    return FixedPoint.from_decimal(value, scale)


def _frame(name: str, rows: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=ARTIFACT_SCHEMAS_V2[name])


def build_standard_frames(run: CertifiedBacktest) -> dict[str, pd.DataFrame]:
    """Transform QExec snapshots/journal facts; never recompute an alternative account state."""
    if run.snapshot.base_currency != "USDT":
        raise ValidationError("certified crypto fixture reporting requires USDT base currency")
    position_rows = _position_rows(run)
    returns = _frame(
        "returns",
        [
            {
                "event_time": stage.event_time,
                "strategy_id": STRATEGY_ID,
                "gross_return": float(
                    (_decimal(stage.snapshot.nav) + _decimal(stage.fee_total))
                    / _decimal(run.initial_cash)
                    - Decimal(1)
                ),
                "net_return": float(
                    _decimal(stage.snapshot.nav) / _decimal(run.initial_cash) - Decimal(1)
                ),
                "nav_units": stage.snapshot.nav.units,
                "nav_scale": stage.snapshot.nav.scale,
                "base_currency": stage.snapshot.base_currency,
            }
            for stage in run.event_trace
        ],
    )
    portfolio = _frame(
        "portfolio_snapshots",
        [_portfolio_row(stage.event_time, stage.snapshot) for stage in run.event_trace],
    )
    exposures = _frame(
        "exposures",
        [
            {
                "event_time": row["event_time"],
                "account_id": row["account_id"],
                "strategy_id": row["strategy_id"],
                "exposure_type": "signed_notional",
                "name": row["instrument_id"],
                "value": float(
                    Decimal(row["base_market_value_units"]).scaleb(
                        -int(row["base_market_value_scale"])
                    )
                ),
                "unit": run.snapshot.base_currency,
            }
            for row in position_rows
        ],
    )
    frames = {
        "returns": returns,
        "positions": _frame("positions", position_rows),
        "portfolio_snapshots": portfolio,
        "exposures": exposures,
        "orders": _orders(run),
        "order_events": _order_events(run),
        "fills": _fills(run),
        "costs": _costs(run),
        "cash_ledger": _cash_ledger(run),
        "margin": _margin(run, position_rows),
    }
    return frames


def _position_rows(run: CertifiedBacktest) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for stage in run.event_trace:
        rows.extend(
            _snapshot_position_rows(
                stage.event_time,
                stage.snapshot,
                run.instruments,
            )
        )
    return rows


def _snapshot_position_rows(
    event_time: datetime,
    snapshot: AccountSnapshot,
    instruments: Mapping[str, InstrumentSpec],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for instrument_id, quantity in sorted(snapshot.positions.items()):
        if quantity.units == 0:
            continue
        spec = instruments[instrument_id]
        if spec.settlement_currency != snapshot.base_currency:
            raise ValidationError("fixture position reporting cannot infer an unversioned FX rate")
        quantity_value = _decimal(quantity)
        multiplier = _decimal(spec.contract_multiplier)
        average = _decimal(snapshot.cost_basis[instrument_id])
        unrealized = _decimal(snapshot.unrealized_pnl[instrument_id])
        mark_value = average + unrealized / (quantity_value * multiplier)
        mark = _fixed(mark_value, spec.price_tick.scale)
        market_value = _fixed(mark_value * quantity_value * multiplier)
        rows.append(
            {
                "event_time": event_time,
                "account_id": snapshot.account_id,
                "strategy_id": STRATEGY_ID,
                "instrument_id": instrument_id,
                "quantity_units": quantity.units,
                "quantity_scale": quantity.scale,
                "mark_price_units": mark.units,
                "mark_price_scale": mark.scale,
                "market_value_units": market_value.units,
                "market_value_scale": market_value.scale,
                "currency": spec.settlement_currency,
                "fx_rate_units": 100_000_000,
                "fx_rate_scale": 8,
                "fx_snapshot_id": "qexec-base-USDT-v1",
                "base_market_value_units": market_value.units,
                "base_market_value_scale": market_value.scale,
            }
        )
    return rows


def _portfolio_row(event_time: datetime, snapshot: AccountSnapshot) -> dict[str, Any]:
    cash_value = sum((_decimal(value) for value in snapshot.cash_balances.values()), Decimal(0))
    unrealized = sum((_decimal(value) for value in snapshot.unrealized_pnl.values()), Decimal(0))
    realized = sum((_decimal(value) for value in snapshot.realized_pnl.values()), Decimal(0))
    market_value = _decimal(snapshot.nav) - cash_value
    return {
        "event_time": event_time,
        "account_id": snapshot.account_id,
        "base_currency": snapshot.base_currency,
        "nav_units": snapshot.nav.units,
        "nav_scale": snapshot.nav.scale,
        "cash_value_units": _fixed(cash_value).units,
        "cash_value_scale": 8,
        "market_value_units": _fixed(market_value).units,
        "market_value_scale": 8,
        "unrealized_pnl_units": _fixed(unrealized).units,
        "unrealized_pnl_scale": 8,
        "realized_pnl_units": _fixed(realized).units,
        "realized_pnl_scale": 8,
        "margin_used_units": snapshot.initial_margin.units,
        "margin_used_scale": snapshot.initial_margin.scale,
    }


def _orders(run: CertifiedBacktest) -> pd.DataFrame:
    rows = []
    for order in run.artifacts.orders:
        intent = order.intent
        rows.append(
            {
                "event_time": intent.created_at,
                "order_id": order.order_id,
                "idempotency_key": intent.idempotency_key,
                "account_id": intent.account_id,
                "strategy_id": intent.strategy_id,
                "instrument_id": intent.instrument_id,
                "side": intent.side.value,
                "quantity_units": intent.quantity.units,
                "quantity_scale": intent.quantity.scale,
                "order_type": intent.order_type.value,
                "limit_price_units": intent.limit_price.units if intent.limit_price else None,
                "limit_price_scale": intent.limit_price.scale if intent.limit_price else None,
                "stop_price_units": intent.stop_price.units if intent.stop_price else None,
                "stop_price_scale": intent.stop_price.scale if intent.stop_price else None,
                "time_in_force": intent.time_in_force.value,
                "reduce_only": intent.reduce_only,
                "status": order.status.value,
                "filled_quantity_units": order.filled_quantity.units,
                "filled_quantity_scale": order.filled_quantity.scale,
                "version": order.version,
            }
        )
    return _frame("orders", sorted(rows, key=lambda row: (row["event_time"], row["order_id"])))


def _order_events(run: CertifiedBacktest) -> pd.DataFrame:
    rows = [
        {
            "event_time": event.event_time,
            "event_id": event.event_id,
            "order_id": event.order_id,
            "event_sequence": event.sequence,
            "from_status": event.from_status.value,
            "to_status": event.to_status.value,
            "fill_quantity_units": event.fill_quantity.units if event.fill_quantity else None,
            "fill_quantity_scale": event.fill_quantity.scale if event.fill_quantity else None,
            "reason": event.reason,
        }
        for event in run.artifacts.order_events
    ]
    return _frame(
        "order_events",
        sorted(rows, key=lambda row: (row["event_time"], row["order_id"], row["event_sequence"])),
    )


def _fills(run: CertifiedBacktest) -> pd.DataFrame:
    rows = [
        {
            "event_time": fill.event_time,
            "fill_id": fill.fill_id,
            "order_id": fill.order_id,
            "account_id": fill.account_id,
            "strategy_id": fill.strategy_id,
            "instrument_id": fill.instrument_id,
            "side": fill.side.value,
            "quantity_units": fill.quantity.units,
            "quantity_scale": fill.quantity.scale,
            "price_units": fill.price.units,
            "price_scale": fill.price.scale,
            "currency": run.instruments[fill.instrument_id].settlement_currency,
            "liquidity_role": fill.liquidity_role.value,
            "venue_trade_id": fill.venue_trade_id,
        }
        for fill in run.artifacts.fills
    ]
    return _frame("fills", sorted(rows, key=lambda row: (row["event_time"], row["fill_id"])))


def _costs(run: CertifiedBacktest) -> pd.DataFrame:
    fills = {fill.fill_id: fill for fill in run.artifacts.fills}
    rows = [
        {
            "event_time": fee.event_time,
            "cost_id": fee.fee_id,
            "account_id": fee.account_id,
            "strategy_id": fills[fee.fill_id].strategy_id,
            "instrument_id": fills[fee.fill_id].instrument_id,
            "fill_id": fee.fill_id,
            "cost_type": fee.fee_type,
            "amount_units": fee.amount.units,
            "amount_scale": fee.amount.scale,
            "currency": fee.currency,
        }
        for fee in run.artifacts.fees
    ]
    for transaction in run.artifacts.ledger_transactions:
        if transaction.event_type is not LedgerEventType.FUNDING:
            continue
        cash_posting = next(
            posting for posting in transaction.postings if posting.ledger_account == "assets:cash"
        )
        instrument_id = next(
            posting.instrument_id
            for posting in transaction.postings
            if posting.instrument_id is not None
        )
        rows.append(
            {
                "event_time": transaction.event_time,
                "cost_id": transaction.reference_id,
                "account_id": ACCOUNT_ID,
                "strategy_id": STRATEGY_ID,
                "instrument_id": instrument_id,
                "fill_id": None,
                "cost_type": "funding",
                "amount_units": -cash_posting.amount.units,
                "amount_scale": cash_posting.amount.scale,
                "currency": cash_posting.currency,
            }
        )
    return _frame("costs", sorted(rows, key=lambda row: (row["event_time"], row["cost_id"])))


def _cash_ledger(run: CertifiedBacktest) -> pd.DataFrame:
    rows = []
    for transaction in run.artifacts.ledger_transactions:
        for index, posting in enumerate(transaction.postings):
            rows.append(
                {
                    "event_time": transaction.event_time,
                    "transaction_id": transaction.transaction_id,
                    "idempotency_key": transaction.idempotency_key,
                    "event_type": transaction.event_type.value,
                    "reference_id": transaction.reference_id,
                    "posting_index": index,
                    "ledger_account": posting.ledger_account,
                    "account_id": ACCOUNT_ID,
                    "currency": posting.currency,
                    "amount_units": posting.amount.units,
                    "amount_scale": posting.amount.scale,
                    "instrument_id": posting.instrument_id,
                    "quantity_delta_units": (
                        posting.quantity_delta.units if posting.quantity_delta else None
                    ),
                    "quantity_delta_scale": (
                        posting.quantity_delta.scale if posting.quantity_delta else None
                    ),
                }
            )
    return _frame(
        "cash_ledger",
        sorted(
            rows,
            key=lambda row: (row["event_time"], row["transaction_id"], row["posting_index"]),
        ),
    )


def _margin(run: CertifiedBacktest, position_rows: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    initial_totals: dict[datetime, Decimal] = {}
    maintenance_totals: dict[datetime, Decimal] = {}
    for position in position_rows:
        spec = run.instruments[position["instrument_id"]]
        if "perpetual" not in spec.product_type.lower() and "perp" not in spec.product_type.lower():
            continue
        quantity = abs(Decimal(position["quantity_units"]).scaleb(-int(position["quantity_scale"])))
        mark = Decimal(position["mark_price_units"]).scaleb(-int(position["mark_price_scale"]))
        notional = quantity * mark * _decimal(spec.contract_multiplier)
        initial = _fixed(notional * _metadata_decimal(spec, "initial_margin_rate"))
        maintenance = _fixed(notional * _metadata_decimal(spec, "maintenance_margin_rate"))
        event_time = position["event_time"]
        initial_totals[event_time] = initial_totals.get(event_time, Decimal(0)) + _decimal(initial)
        maintenance_totals[event_time] = maintenance_totals.get(event_time, Decimal(0)) + _decimal(
            maintenance
        )
        rows.append(
            {
                "event_time": event_time,
                "account_id": position["account_id"],
                "instrument_id": position["instrument_id"],
                "initial_margin_units": initial.units,
                "maintenance_margin_units": maintenance.units,
                "margin_scale": 8,
                "currency": spec.settlement_currency,
            }
        )
    for stage in run.event_trace:
        if (
            _fixed(initial_totals.get(stage.event_time, Decimal(0)))
            != stage.snapshot.initial_margin
        ):
            raise ValidationError(
                "reported per-instrument initial margin differs from QExec stage snapshot"
            )
        if (
            _fixed(maintenance_totals.get(stage.event_time, Decimal(0)))
            != stage.snapshot.maintenance_margin
        ):
            raise ValidationError("reported maintenance margin differs from QExec stage snapshot")
    return _frame("margin", rows)


def _metadata_decimal(spec: InstrumentSpec, key: str) -> Decimal:
    try:
        value = Decimal(spec.metadata[key])
    except (KeyError, ValueError) as exc:
        raise ValidationError(f"InstrumentSpec metadata {key!r} is required for reporting") from exc
    if not value.is_finite():
        raise ValidationError(f"InstrumentSpec metadata {key!r} must be finite")
    return value


def write_certified_standard_run(
    run: CertifiedBacktest,
    run_dir: Path,
    *,
    code_version: str | None = None,
    created_at: datetime | str | None = None,
) -> RunManifestV2:
    resolved_code_version = resolve_clean_head(expected_code_version=code_version)
    frames = build_standard_frames(run)
    created = created_at or run.snapshot.event_time
    created_text = created.isoformat() if isinstance(created, datetime) else created
    source = run.batch.provider
    expected_providers = set(FIXTURE_DATASETS)
    quality = run.quality_report
    if (
        set(quality.providers) != expected_providers
        or set(quality.fixture_sha256) != expected_providers
    ):
        raise ValidationError("certified artifact requires complete Binance and OKX QA provenance")
    if quality.fixture_sha256[source] != run.batch.file_sha256:
        raise ValidationError("selected fixture hash differs from cross-source QA provenance")
    dataset_snapshots = {
        CATALOG_DATASET: f"sha256:{quality.catalog_sha256}",
        **{
            FIXTURE_DATASETS[provider]: f"sha256:{quality.fixture_sha256[provider]}"
            for provider in quality.providers
        },
    }
    metrics = {
        "event_count": run.result.event_count,
        "order_count": run.result.order_count,
        "fill_count": run.result.fill_count,
        "final_nav": str(_decimal(run.snapshot.nav)),
        "liquidation_required": run.snapshot.liquidation_required,
        "ledger_sha256": run.result.ledger_sha256,
        "qa_provider_count": len(quality.providers),
        "qa_common_instrument_count": len(quality.common_instruments),
        "qa_binance_event_type_count": len(quality.event_types["binance"]),
        "qa_okx_event_type_count": len(quality.event_types["okx"]),
        "qa_binance_row_count": quality.row_counts["binance"],
        "qa_okx_row_count": quality.row_counts["okx"],
        "qa_price_equality_required": quality.price_equality_required,
        "qa_dual_source_complete": True,
    }
    config = {
        "source": source,
        "seed": run.result.seed,
        "strategy_id": STRATEGY_ID,
        "spot_instrument_id": run.strategy_config.spot_instrument_id,
        "perpetual_instrument_id": run.strategy_config.perpetual_instrument_id,
        "quantity": str(_decimal(run.strategy_config.quantity)),
        "entry_basis_bps": str(run.strategy_config.entry_basis_bps),
        "exit_basis_bps": str(run.strategy_config.exit_basis_bps),
        "minimum_funding_rate": str(run.strategy_config.minimum_funding_rate),
        "passive_limits": run.strategy_config.passive_limits,
        "certification_scope": "research-backtest-paper-only",
    }
    catalog_lineage = f"dataset:{CATALOG_DATASET}"
    selected_lineage = f"dataset:{FIXTURE_DATASETS[source]}"
    qa_lineage = [catalog_lineage, *(f"dataset:{FIXTURE_DATASETS[p]}" for p in quality.providers)]
    lineage = {name: [catalog_lineage, selected_lineage] for name in frames}
    lineage["config"] = [catalog_lineage]
    lineage["metrics"] = qa_lineage
    manifest = write_standard_run_v2(
        Path(run_dir),
        project="quant-crypto-basis",
        run_id=run.result.run_id,
        strategy_ids=[STRATEGY_ID],
        profile=BACKTEST_LEDGER_PROFILE,
        frames=frames,
        metrics=metrics,
        config=config,
        code_version=resolved_code_version,
        internal_dependencies=INTERNAL_DEPENDENCIES,
        random_seed=run.result.seed,
        dataset_snapshots=dataset_snapshots,
        instrument_master_version=INSTRUMENT_MASTER_VERSION,
        execution_model_version="quant-execution-v0.2.0:TradeBBOModel+ExactAccountLedger",
        base_currency=run.snapshot.base_currency,
        lineage=lineage,
        capabilities=[
            "offline-fixture",
            "spot-perpetual-basis",
            "funding",
            "backtest-ledger",
        ],
        tags={"source": source, "environment": "offline-fixture"},
        created_at=created_text,
    )
    loaded = load_and_validate_standard_run(Path(run_dir))
    if loaded != manifest:
        raise ValidationError("QLab standard/v2 readback differs from written manifest")
    return manifest
