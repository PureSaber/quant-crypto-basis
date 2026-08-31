# quant-crypto-basis

## Scope

- Research, backtest and paper-simulation only.
- Market inputs are committed, desensitized Binance and OKX offline fixtures.
- Never add API keys, credential loading, network collectors, live brokers or order transmission.
- Strategies emit only `quant_execution.OrderIntent` values from `Strategy.on_event`.
- `quant_execution.DeterministicRunEngine`, `RuleBookRiskGate`, matching models and
  `ExactAccountLedger` are the sole certified execution and accounting path.
- Event-stage audit snapshots must come from that same engine and ledger authority; never keep a
  shadow position, cash, margin or NAV state.
- Certified artifacts are `quant_lab` `standard/v2`, profile `backtest-ledger`, and must be
  read back with `load_and_validate_standard_run`.

## Frozen dependencies

- `quant-data-kit==v0.8.1` (`8f258f11be8e4d8edddcd41b79b817bd6c925970`)
- `quant-execution==v0.5.1` (`15e4e5c9dbaf2fe9b438732b2e94db295d5ea58c`)
- `quant-lab==v0.3.1` (`27489d270e132adbec1bced93eb2ae84ad5e1a9b`)

Do not replace these with floating branches or adjacent working-tree dependencies for release
evidence.

## Commands

```bash
ruff format --check .
ruff check .
pytest --cov=quant_crypto_basis --cov-branch --cov-report=term-missing --cov-fail-under=80
```
