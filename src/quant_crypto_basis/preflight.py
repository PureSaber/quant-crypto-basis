"""Read-only input preflight for the packaged Binance and OKX offline fixtures."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any

from quant_crypto_basis.catalog import INSTRUMENT_MASTER_VERSION
from quant_crypto_basis.fixtures import FixtureLoader
from quant_crypto_basis.runner import prepare_fixture_inputs
from quant_crypto_basis.strategy import BasisFundingConfig


def preflight(
    *,
    source: str = "binance",
    run_id: str = "crypto-basis-fixture-v1",
    seed: int = 7,
    strategy_config: BasisFundingConfig | None = None,
    initial_cash: Decimal | str = Decimal("100000"),
    fixture_loader: FixtureLoader | None = None,
) -> dict[str, Any]:
    prepared = prepare_fixture_inputs(
        source=source,
        run_id=run_id,
        seed=seed,
        strategy_config=strategy_config,
        initial_cash=initial_cash,
        fixture_loader=fixture_loader,
    )
    config = prepared.strategy_config
    settings = {
        "source": source,
        "run_id": run_id,
        "seed": seed,
        "initial_cash": str(prepared.initial_cash.to_decimal()),
        "spot_instrument_id": config.spot_instrument_id,
        "perpetual_instrument_id": config.perpetual_instrument_id,
        "quantity": str(config.quantity.to_decimal()),
        "entry_basis_bps": str(config.entry_basis_bps),
        "exit_basis_bps": str(config.exit_basis_bps),
        "minimum_funding_rate": str(config.minimum_funding_rate),
        "passive_limits": config.passive_limits,
    }
    hashes = {name: state[0] for name, state in prepared.input_state.items()}
    batch = prepared.batch
    return {
        "schema_version": "quant-crypto-basis.preflight/v1",
        "status": "pass",
        "read_only": True,
        "investable": False,
        "evidence_kind": "synthetic",
        "scope": "desensitized-offline-fixture-only / backtest-only",
        "base_currency": "USDT",
        "config": settings,
        "config_sha256": hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest(),
        "input_fingerprint": hashlib.sha256(
            json.dumps(hashes, sort_keys=True).encode()
        ).hexdigest(),
        "inputs": {
            name: {"sha256": state[0], "mtime_ns": state[1]}
            for name, state in prepared.input_state.items()
        },
        "instrument_master_version": INSTRUMENT_MASTER_VERSION,
        "instrument_ids": sorted(batch.instrument_ids),
        "event_count": len(batch.events),
        "cross_source_rows": dict(prepared.quality_report.row_counts),
        "available_start": min(event.available_at for event in batch.events).isoformat(),
        "available_end": max(event.available_at for event in batch.events).isoformat(),
        "checks": [
            "config",
            "catalog_hashes",
            "cross_source_quality",
            "strategy_legs",
            "input_stability",
        ],
        "limitations": [
            "No strategy, execution engine or account ledger is created.",
            "Fills, pair completion, liquidity and margin remain replay-time checks.",
            "Passing does not certify real historical market data or lock future inputs.",
        ],
    }
