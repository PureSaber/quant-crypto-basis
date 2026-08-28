"""Versioned point-in-time instrument and provider-symbol master."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType

from quant_data_kit import (
    AssetClass,
    FixedPoint,
    InstrumentSpec,
    MarginMode,
    SymbolMapping,
    ensure_utc_datetime,
)
from quant_data_kit.exceptions import ValidationError

UTC = timezone.utc
FIXTURE_EFFECTIVE_FROM = datetime(2026, 1, 2, tzinfo=UTC)
FIXTURE_EFFECTIVE_TO = datetime(2026, 1, 3, tzinfo=UTC)
MASTER_AVAILABLE_AT = datetime(2026, 1, 1, tzinfo=UTC)
INSTRUMENT_MASTER_VERSION = "crypto-fixture-master-v1"

BTC_SPOT = "CRYPTO:BTC-USDT:SPOT"
BTC_PERP = "CRYPTO:BTC-USDT:PERP"
ETH_SPOT = "CRYPTO:ETH-USDT:SPOT"
ETH_PERP = "CRYPTO:ETH-USDT:PERP"


def _fp(value: str, scale: int) -> FixedPoint:
    return FixedPoint.from_decimal(value, scale)


def _instrument(
    instrument_id: str,
    *,
    product_type: str,
    base_currency: str,
    native_symbol: str,
) -> InstrumentSpec:
    derivative = product_type == "linear_perpetual"
    metadata = {
        "fixture_applicability": "2026-01-02-only-not-listing-history",
        "min_quantity": "0.010",
        "maker_fee_rate": "0.0002",
        "taker_fee_rate": "0.0005",
    }
    if derivative:
        metadata.update(
            initial_margin_rate="0.10",
            maintenance_margin_rate="0.05",
        )
    return InstrumentSpec(
        instrument_id=instrument_id,
        asset_class=AssetClass.CRYPTO,
        product_type=product_type,
        venue="MULTI-FIXTURE",
        native_symbol=native_symbol,
        base_currency=base_currency,
        quote_currency="USDT",
        settlement_currency="USDT",
        price_tick=_fp("0.01", 2),
        quantity_step=_fp("0.001", 3),
        contract_multiplier=_fp("1", 0),
        calendar_id="UTC-24X7",
        margin_mode=MarginMode.CROSS if derivative else MarginMode.CASH,
        inverse=False,
        effective_from=FIXTURE_EFFECTIVE_FROM,
        effective_to=FIXTURE_EFFECTIVE_TO,
        available_at=MASTER_AVAILABLE_AT,
        underlying_id=(instrument_id.replace(":PERP", ":SPOT") if derivative else None),
        metadata=metadata,
    )


def _mapping(source: str, provider_symbol: str, instrument_id: str) -> SymbolMapping:
    return SymbolMapping(
        source=source,
        provider_symbol=provider_symbol,
        instrument_id=instrument_id,
        effective_from=FIXTURE_EFFECTIVE_FROM,
        effective_to=FIXTURE_EFFECTIVE_TO,
        available_at=MASTER_AVAILABLE_AT,
    )


@dataclass(frozen=True, slots=True)
class InstrumentMaster:
    """Fail-closed business-time and knowledge-time lookup."""

    version: str
    specifications: tuple[InstrumentSpec, ...]
    symbol_mappings: tuple[SymbolMapping, ...]

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValidationError("instrument master version is required")
        if not self.specifications or not self.symbol_mappings:
            raise ValidationError("instrument master requires specifications and mappings")
        for value in self.specifications:
            if not isinstance(value, InstrumentSpec):
                raise ValidationError("instrument master contains a non-InstrumentSpec value")
        for value in self.symbol_mappings:
            if not isinstance(value, SymbolMapping):
                raise ValidationError("instrument master contains a non-SymbolMapping value")
        specification_ids = {item.instrument_id for item in self.specifications}
        dangling = {
            item.instrument_id
            for item in self.symbol_mappings
            if item.instrument_id not in specification_ids
        }
        if dangling:
            raise ValidationError(
                f"symbol mappings reference missing instruments: {sorted(dangling)}"
            )
        identity_keys = [
            (item.source.casefold(), item.provider_symbol.casefold(), item.effective_from)
            for item in self.symbol_mappings
        ]
        if len(identity_keys) != len(set(identity_keys)):
            raise ValidationError("instrument master contains case-insensitive symbol ambiguity")

    @property
    def instruments(self) -> MappingProxyType[str, InstrumentSpec]:
        values: dict[str, InstrumentSpec] = {}
        for item in self.specifications:
            if item.instrument_id in values:
                raise ValidationError(
                    "instrument_id has multiple versions without PIT selection: "
                    f"{item.instrument_id}"
                )
            values[item.instrument_id] = item
        return MappingProxyType(values)

    def specification(
        self,
        instrument_id: str,
        *,
        observation_time: datetime,
        as_of: datetime,
    ) -> InstrumentSpec:
        observation, knowledge = self._times(observation_time, as_of)
        matches = [
            item
            for item in self.specifications
            if item.instrument_id == instrument_id
            and item.effective_from <= observation
            and (item.effective_to is None or observation < item.effective_to)
            and item.available_at <= knowledge
            and (item.superseded_at is None or knowledge < item.superseded_at)
        ]
        return self._unique(matches, f"InstrumentSpec {instrument_id}")

    def symbol_mapping(
        self,
        source: str,
        provider_symbol: str,
        *,
        observation_time: datetime,
        as_of: datetime,
    ) -> SymbolMapping:
        observation, knowledge = self._times(observation_time, as_of)
        matches = [
            item
            for item in self.symbol_mappings
            if item.source == source
            and item.provider_symbol == provider_symbol
            and item.effective_from <= observation
            and (item.effective_to is None or observation < item.effective_to)
            and item.available_at <= knowledge
            and (item.superseded_at is None or knowledge < item.superseded_at)
        ]
        mapping = self._unique(matches, f"SymbolMapping {source}:{provider_symbol}")
        self.specification(
            mapping.instrument_id,
            observation_time=observation,
            as_of=knowledge,
        )
        return mapping

    @staticmethod
    def _times(observation_time: datetime, as_of: datetime) -> tuple[datetime, datetime]:
        observation = ensure_utc_datetime(observation_time, field="observation_time")
        knowledge = ensure_utc_datetime(as_of, field="as_of")
        if knowledge < observation:
            raise ValidationError("as_of precedes observation_time; future data is unavailable")
        return observation, knowledge

    @staticmethod
    def _unique(values: list, label: str):
        if not values:
            raise ValidationError(f"{label} is unavailable at the requested PIT coordinates")
        if len(values) != 1:
            raise ValidationError(f"{label} is ambiguous at the requested PIT coordinates")
        return values[0]


def default_instrument_master() -> InstrumentMaster:
    specifications = (
        _instrument(BTC_SPOT, product_type="spot", base_currency="BTC", native_symbol="BTC-USDT"),
        _instrument(
            BTC_PERP,
            product_type="linear_perpetual",
            base_currency="BTC",
            native_symbol="BTC-USDT-PERP",
        ),
        _instrument(ETH_SPOT, product_type="spot", base_currency="ETH", native_symbol="ETH-USDT"),
        _instrument(
            ETH_PERP,
            product_type="linear_perpetual",
            base_currency="ETH",
            native_symbol="ETH-USDT-PERP",
        ),
    )
    mappings = (
        _mapping("binance", "BTCUSDT", BTC_SPOT),
        _mapping("binance", "BTCUSDT_PERP", BTC_PERP),
        _mapping("binance", "ETHUSDT", ETH_SPOT),
        _mapping("binance", "ETHUSDT_PERP", ETH_PERP),
        _mapping("okx", "BTC-USDT", BTC_SPOT),
        _mapping("okx", "BTC-USDT-SWAP", BTC_PERP),
        _mapping("okx", "ETH-USDT", ETH_SPOT),
        _mapping("okx", "ETH-USDT-SWAP", ETH_PERP),
    )
    return InstrumentMaster(INSTRUMENT_MASTER_VERSION, specifications, mappings)
