"""Offline Binance/OKX fixture loading through frozen QDK v0.6.0 adapters."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any

from quant_data_kit import (
    AdapterContext,
    AdapterInstrument,
    BinanceFixtureAdapter,
    MarketEvent,
    OKXFixtureAdapter,
    adapt_fixture_messages,
    ensure_utc_datetime,
    validate_event_stream,
)
from quant_data_kit.adapters_v2.base import utc_from_milliseconds
from quant_data_kit.exceptions import ValidationError

from quant_crypto_basis.catalog import (
    FIXTURE_EFFECTIVE_TO,
    InstrumentMaster,
    default_instrument_master,
)
from quant_crypto_basis.events import market_event_from_payload

_PROVIDERS = frozenset({"binance", "okx"})
_REQUIRED_EVENT_TYPES = frozenset(
    {"trade", "quote", "book_snapshot", "book_delta", "funding_rate", "mark_price"}
)


@dataclass(frozen=True, slots=True)
class FixtureBatch:
    provider: str
    records: tuple[Mapping[str, Any], ...]
    events: tuple[MarketEvent, ...]
    file_sha256: str
    applicability_start: datetime
    applicability_end: datetime

    @property
    def event_types(self) -> frozenset[str]:
        return frozenset(str(record["event_type"]) for record in self.records)

    @property
    def instrument_ids(self) -> frozenset[str]:
        return frozenset(event.instrument_id for event in self.events)


@dataclass(frozen=True, slots=True)
class CrossSourceQualityReport:
    providers: tuple[str, str]
    common_instruments: frozenset[str]
    event_types: Mapping[str, frozenset[str]]
    row_counts: Mapping[str, int]
    catalog_sha256: str
    fixture_sha256: Mapping[str, str]
    price_equality_required: bool = False


class FixtureLoader:
    """Verify the exact fixture catalog, then adapt it without any network path."""

    def __init__(
        self,
        root: Path | None = None,
        *,
        instrument_master: InstrumentMaster | None = None,
    ) -> None:
        self.root = Path(root) if root is not None else Path(__file__).parent / "fixtures"
        self.instrument_master = instrument_master or default_instrument_master()

    def load(self, provider: str, *, as_of: datetime = FIXTURE_EFFECTIVE_TO) -> FixtureBatch:
        if provider not in _PROVIDERS:
            raise ValidationError(f"unsupported fixture provider: {provider!r}")
        cutoff = ensure_utc_datetime(as_of, field="as_of")
        manifest = self._verified_manifest()
        applicability = manifest["applicability"]
        start = self._manifest_time(applicability["effective_from"], "effective_from")
        end = self._manifest_time(applicability["effective_to"], "effective_to")
        if applicability.get("historical_listing_claim") is not False:
            raise ValidationError("fixture applicability must not claim listing history")
        relative = manifest["files"][provider]
        path = self._safe_file(relative)
        messages = self._messages(path)
        adapter = self._adapter(provider)
        self._validate_raw_pit(provider, messages, cutoff, start, end)
        records = adapt_fixture_messages(adapter, messages)
        validate_event_stream(records)
        events = tuple(market_event_from_payload(record) for record in records)
        self._validate_normalized(provider, records, events, cutoff, start, end)
        frozen_records = tuple(MappingProxyType(dict(record)) for record in records)
        return FixtureBatch(
            provider=provider,
            records=frozen_records,
            events=events,
            file_sha256=manifest["sha256"][relative],
            applicability_start=start,
            applicability_end=end,
        )

    def _verified_manifest(self) -> dict[str, Any]:
        root = self.root.resolve(strict=True)
        if not root.is_dir() or self.root.is_symlink():
            raise ValidationError("fixture root must be one unambiguous physical directory")
        index_path = self._safe_file("index.json")
        try:
            manifest = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValidationError("fixture index is unreadable or invalid JSON") from exc
        expected_keys = {
            "schema_version",
            "certification_scope",
            "applicability",
            "files",
            "sha256",
            "provider_integrity",
        }
        if set(manifest) != expected_keys or manifest["schema_version"] != "1.0.0":
            raise ValidationError("fixture index schema is unsupported or ambiguous")
        if manifest["certification_scope"] != "desensitized-offline-fixture-only":
            raise ValidationError("fixture certification scope is not offline-only")
        files = manifest["files"]
        if not isinstance(files, dict) or set(files) != _PROVIDERS:
            raise ValidationError("fixture index must declare exactly Binance and OKX")
        relative_files = list(files.values())
        if any(not isinstance(value, str) for value in relative_files):
            raise ValidationError("fixture paths must be strings")
        casefolded = [value.casefold() for value in relative_files]
        if len(casefolded) != len(set(casefolded)):
            raise ValidationError("fixture index contains case-insensitive path ambiguity")
        declared = {"index.json", *relative_files}
        actual = {
            path.relative_to(root).as_posix() for path in root.rglob("*.json") if path.is_file()
        }
        if actual != declared:
            raise ValidationError(
                f"fixture directory file set is ambiguous: missing={sorted(declared - actual)}, "
                f"extra={sorted(actual - declared)}"
            )
        hashes = manifest["sha256"]
        if not isinstance(hashes, dict) or set(hashes) != set(relative_files):
            raise ValidationError("fixture index hashes do not match declared files")
        for relative in relative_files:
            path = self._safe_file(relative)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != hashes[relative]:
                raise ValidationError(f"fixture SHA-256 mismatch: {relative}")
        return manifest

    def _safe_file(self, relative: str) -> Path:
        if not isinstance(relative, str) or not relative or "\\" in relative:
            raise ValidationError("fixture path must be a canonical POSIX relative path")
        root = self.root.resolve(strict=True)
        candidate = (root / relative).resolve(strict=True)
        if candidate == root or root not in candidate.parents:
            raise ValidationError("fixture path escapes the catalog root")
        if candidate.is_symlink() or not candidate.is_file():
            raise ValidationError("fixture path must identify one physical file")
        return candidate

    @staticmethod
    def _messages(path: Path) -> list[Mapping[str, Any]]:
        try:
            values = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValidationError(
                f"fixture file is unreadable or invalid JSON: {path.name}"
            ) from exc
        if not isinstance(values, list) or not values:
            raise ValidationError("fixture file must contain a non-empty message list")
        if any(not isinstance(value, dict) for value in values):
            raise ValidationError("fixture message list contains a non-object value")
        return values

    def _adapter(self, provider: str):
        mappings = [
            item for item in self.instrument_master.symbol_mappings if item.source == provider
        ]
        instruments = self.instrument_master.instruments
        context = AdapterContext(
            provider=provider,
            venue=provider.upper(),
            instruments={
                item.provider_symbol: AdapterInstrument(
                    item.instrument_id,
                    price_scale=instruments[item.instrument_id].price_tick.scale,
                    quantity_scale=instruments[item.instrument_id].quantity_step.scale,
                )
                for item in mappings
            },
            session_kind="24x7",
        )
        return (
            BinanceFixtureAdapter(context) if provider == "binance" else OKXFixtureAdapter(context)
        )

    def _validate_raw_pit(
        self,
        provider: str,
        messages: list[Mapping[str, Any]],
        cutoff: datetime,
        start: datetime,
        end: datetime,
    ) -> None:
        prior_received: datetime | None = None
        for message in messages:
            symbol, event_time, received_at = self._raw_coordinates(provider, message)
            if prior_received is not None and received_at < prior_received:
                raise ValidationError("fixture messages are out of available-time order")
            prior_received = received_at
            if not start <= event_time < end:
                raise ValidationError("fixture event lies outside its declared applicability")
            if received_at > cutoff:
                raise ValidationError("fixture contains data not yet available at as_of")
            self.instrument_master.symbol_mapping(
                provider,
                symbol,
                observation_time=event_time,
                as_of=received_at,
            )

    @staticmethod
    def _raw_coordinates(
        provider: str, message: Mapping[str, Any]
    ) -> tuple[str, datetime, datetime]:
        try:
            if provider == "binance":
                symbol = str(message["s"])
                event_value = message.get("T", message["E"])
            else:
                symbol = str(message["instId"])
                event_value = message["ts"]
            event_time = utc_from_milliseconds(event_value, "event_time")
            received_at = utc_from_milliseconds(message["received_at"], "received_at")
        except KeyError as exc:
            raise ValidationError("fixture message is missing symbol or temporal identity") from exc
        if received_at < event_time:
            raise ValidationError("fixture received_at precedes event_time")
        return symbol, event_time, received_at

    def _validate_normalized(
        self,
        provider: str,
        records: list[dict[str, Any]],
        events: tuple[MarketEvent, ...],
        cutoff: datetime,
        start: datetime,
        end: datetime,
    ) -> None:
        prior_available: datetime | None = None
        for record, event in zip(records, events, strict=True):
            if event.source != provider:
                raise ValidationError("normalized fixture source differs from selected provider")
            if prior_available is not None and event.available_at < prior_available:
                raise ValidationError("normalized events are out of available-time order")
            prior_available = event.available_at
            if event.available_at > cutoff:
                raise ValidationError("normalized event is future data at as_of")
            if not start <= event.event_time < end:
                raise ValidationError("normalized event lies outside fixture applicability")
            if event.trading_day != event.event_time.date():
                raise ValidationError("24x7 fixture trading_day must be the UTC event date")
            if not event.session_id.startswith(f"{provider}-24x7-"):
                raise ValidationError("fixture event has a non-24x7 session identity")
            self.instrument_master.specification(
                event.instrument_id,
                observation_time=event.event_time,
                as_of=event.available_at,
            )
            if record["available_at"] != event.available_at.isoformat().replace("+00:00", "Z"):
                raise ValidationError("MarketEvent decode changed available_at")

    @staticmethod
    def _manifest_time(value: object, field: str) -> datetime:
        if not isinstance(value, str):
            raise ValidationError(f"fixture applicability {field} must be a timestamp")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValidationError(f"fixture applicability {field} is invalid") from exc
        return ensure_utc_datetime(parsed, field=field)


def load_certified_fixtures(
    loader: FixtureLoader | None = None,
) -> tuple[dict[str, FixtureBatch], CrossSourceQualityReport]:
    selected = loader or FixtureLoader()
    batches = {provider: selected.load(provider) for provider in sorted(_PROVIDERS)}
    universes = {provider: batch.instrument_ids for provider, batch in batches.items()}
    common = set.intersection(*(set(value) for value in universes.values()))
    if any(value != frozenset(common) for value in universes.values()):
        raise ValidationError("cross-source stable instrument coverage differs")
    event_types = {provider: batch.event_types for provider, batch in batches.items()}
    missing = {
        provider: sorted(_REQUIRED_EVENT_TYPES - types)
        for provider, types in event_types.items()
        if not types >= _REQUIRED_EVENT_TYPES
    }
    if missing:
        raise ValidationError(f"cross-source adapter event coverage is incomplete: {missing}")
    catalog_path = selected._safe_file("index.json")
    report = CrossSourceQualityReport(
        providers=("binance", "okx"),
        common_instruments=frozenset(common),
        event_types=MappingProxyType(event_types),
        row_counts=MappingProxyType(
            {provider: len(batch.records) for provider, batch in batches.items()}
        ),
        catalog_sha256=hashlib.sha256(catalog_path.read_bytes()).hexdigest(),
        fixture_sha256=MappingProxyType(
            {provider: batch.file_sha256 for provider, batch in batches.items()}
        ),
    )
    return batches, report
