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

- `quant-data-kit==v0.5.0` (`84884f5005cbb9c0111564732d96509a63f34d79`)
- `quant-execution==v0.2.0` (`9529d95a7c19ff3e605a8377d54af75a4260a49e`)
- `quant-lab==v0.3.0` (`ae0e9edea5cef136f9888d734030da1922b07283`)

Do not replace these with floating branches or adjacent working-tree dependencies for release
evidence.

## Commands

```bash
ruff format --check .
ruff check .
pytest --cov=quant_crypto_basis --cov-branch --cov-report=term-missing --cov-fail-under=80
```
