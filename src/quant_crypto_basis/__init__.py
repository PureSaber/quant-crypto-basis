"""Offline spot-perpetual basis and funding research package."""

from quant_crypto_basis.artifacts import write_certified_standard_run
from quant_crypto_basis.catalog import (
    BTC_PERP,
    BTC_SPOT,
    ETH_PERP,
    ETH_SPOT,
    INSTRUMENT_MASTER_VERSION,
    InstrumentMaster,
    default_instrument_master,
)
from quant_crypto_basis.fixtures import (
    CrossSourceQualityReport,
    FixtureBatch,
    FixtureLoader,
    load_certified_fixtures,
)
from quant_crypto_basis.provenance import resolve_clean_head
from quant_crypto_basis.runner import CertifiedBacktest, EventStageSnapshot, run_fixture_backtest
from quant_crypto_basis.strategy import BasisFundingConfig, BasisFundingStrategy

__version__ = "0.1.2"

__all__ = [
    "BTC_PERP",
    "BTC_SPOT",
    "ETH_PERP",
    "ETH_SPOT",
    "INSTRUMENT_MASTER_VERSION",
    "BasisFundingConfig",
    "BasisFundingStrategy",
    "CertifiedBacktest",
    "CrossSourceQualityReport",
    "EventStageSnapshot",
    "FixtureBatch",
    "FixtureLoader",
    "InstrumentMaster",
    "__version__",
    "default_instrument_master",
    "load_certified_fixtures",
    "resolve_clean_head",
    "run_fixture_backtest",
    "write_certified_standard_run",
]
