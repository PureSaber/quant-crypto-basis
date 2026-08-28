from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from quant_data_kit.exceptions import ValidationError

from quant_crypto_basis.catalog import (
    BTC_PERP,
    BTC_SPOT,
    FIXTURE_EFFECTIVE_FROM,
    FIXTURE_EFFECTIVE_TO,
    INSTRUMENT_MASTER_VERSION,
    MASTER_AVAILABLE_AT,
    InstrumentMaster,
    default_instrument_master,
)

UTC = timezone.utc


def test_default_master_is_stable_versioned_and_fixture_scoped() -> None:
    master = default_instrument_master()
    assert master.version == INSTRUMENT_MASTER_VERSION
    assert set(master.instruments) == {
        "CRYPTO:BTC-USDT:SPOT",
        "CRYPTO:BTC-USDT:PERP",
        "CRYPTO:ETH-USDT:SPOT",
        "CRYPTO:ETH-USDT:PERP",
    }
    spot = master.instruments[BTC_SPOT]
    perp = master.instruments[BTC_PERP]
    assert spot.product_type == "spot" and perp.product_type == "linear_perpetual"
    assert perp.underlying_id == BTC_SPOT
    assert spot.calendar_id == perp.calendar_id == "UTC-24X7"
    assert spot.effective_from == FIXTURE_EFFECTIVE_FROM
    assert spot.effective_to == FIXTURE_EFFECTIVE_TO
    assert "not-listing-history" in spot.metadata["fixture_applicability"]
    mapping = master.symbol_mapping(
        "okx",
        "BTC-USDT-SWAP",
        observation_time=FIXTURE_EFFECTIVE_FROM + timedelta(seconds=1),
        as_of=FIXTURE_EFFECTIVE_FROM + timedelta(seconds=2),
    )
    assert mapping.instrument_id == BTC_PERP


@pytest.mark.parametrize(
    ("observation", "as_of", "message"),
    [
        (
            FIXTURE_EFFECTIVE_FROM - timedelta(seconds=1),
            FIXTURE_EFFECTIVE_FROM,
            "unavailable",
        ),
        (FIXTURE_EFFECTIVE_TO, FIXTURE_EFFECTIVE_TO, "unavailable"),
        (
            FIXTURE_EFFECTIVE_FROM,
            MASTER_AVAILABLE_AT - timedelta(seconds=1),
            "precedes observation_time|unavailable",
        ),
    ],
)
def test_master_pit_lookup_fails_closed(
    observation: datetime, as_of: datetime, message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        default_instrument_master().specification(
            BTC_SPOT,
            observation_time=observation,
            as_of=as_of,
        )


def test_master_rejects_naive_future_and_ambiguous_coordinates() -> None:
    master = default_instrument_master()
    with pytest.raises(ValidationError, match="UTC"):
        master.specification(
            BTC_SPOT,
            observation_time=datetime(2026, 1, 2),
            as_of=FIXTURE_EFFECTIVE_FROM,
        )
    with pytest.raises(ValidationError, match="future data"):
        master.specification(
            BTC_SPOT,
            observation_time=FIXTURE_EFFECTIVE_FROM + timedelta(seconds=2),
            as_of=FIXTURE_EFFECTIVE_FROM + timedelta(seconds=1),
        )
    duplicate = replace(master.instruments[BTC_SPOT], native_symbol="BTC-USDT-V2")
    ambiguous = InstrumentMaster(
        "ambiguous-v1",
        (*master.specifications, duplicate),
        master.symbol_mappings,
    )
    with pytest.raises(ValidationError, match="ambiguous"):
        ambiguous.specification(
            BTC_SPOT,
            observation_time=FIXTURE_EFFECTIVE_FROM,
            as_of=FIXTURE_EFFECTIVE_FROM,
        )


def test_master_constructor_rejects_dangling_case_ambiguity_and_empty() -> None:
    master = default_instrument_master()
    dangling = replace(master.symbol_mappings[0], instrument_id="MISSING")
    with pytest.raises(ValidationError, match="missing instruments"):
        InstrumentMaster("bad-v1", master.specifications, (dangling,))
    case_duplicate = replace(
        master.symbol_mappings[0],
        source="BINANCE",
        provider_symbol="btcusdt",
    )
    with pytest.raises(ValidationError, match="case-insensitive"):
        InstrumentMaster(
            "bad-v2",
            master.specifications,
            (*master.symbol_mappings, case_duplicate),
        )
    with pytest.raises(ValidationError, match="version"):
        InstrumentMaster("", master.specifications, master.symbol_mappings)
    with pytest.raises(ValidationError, match="requires"):
        InstrumentMaster("empty-v1", (), ())


def test_master_requires_utc_zero_offset_not_arbitrary_timezone() -> None:
    plus_eight = timezone(timedelta(hours=8))
    with pytest.raises(ValidationError, match="UTC"):
        default_instrument_master().symbol_mapping(
            "binance",
            "BTCUSDT",
            observation_time=datetime(2026, 1, 2, 8, tzinfo=plus_eight),
            as_of=datetime(2026, 1, 2, 8, tzinfo=plus_eight),
        )
    assert FIXTURE_EFFECTIVE_FROM.tzinfo is UTC
