# quant-crypto-basis

`quant-crypto-basis`是BTC/ETH现货与USDT线性永续的离线basis/funding研究包。认证范围仅限
research、backtest和paper-simulation；没有联网采集、live broker、API key、凭据读取或真实订单发送路径。

## 认证边界

- 数据：只读取仓内脱敏Binance/OKX fixture，通过`quant-data-kit v0.5.0`的标准
  `InstrumentSpec`、`SymbolMapping`、provider adapter和`MarketEvent`契约。
- 信号：`BasisFundingStrategy.on_event`是唯一产生`OrderIntent`的位置；策略不修改持仓、现金或NAV。
- 执行：只使用`quant-execution v0.2.0`的`DeterministicRunEngine`、
  `RuleBookRiskGate`、匹配模型和`ExactAccountLedger`。
- 审计：按每个`available_at`阶段从同一个执行引擎与账本实例取得`AccountSnapshot`，逐时点保留
  event id、mark、funding、fee、fill、持仓、保证金和NAV变化；不维护第二套状态。
- 产物：只使用`quant-lab v0.3.0`的`write_standard_run_v2(profile="backtest-ledger")`，
  将上述多时点账本事实写入完整产物，随后由`load_and_validate_standard_run`回读。

Fixture适用期明确限定为`2026-01-02T00:00:00Z`至`2026-01-03T00:00:00Z`，不代表交易所
上市历史。Binance与OKX仅做适配完整性、稳定instrument映射和交叉质量核验，不假定逐笔价格相等。

## 安装与运行

依赖在`pyproject.toml`中固定到GitHub release tag：QDK v0.5.0、QExec v0.2.0、QLab v0.3.0。

```bash
python -m pip install -e ".[dev]"
qcb-run-fixture --source binance --output output/demo \
  --code-version 6df91eca238542c9c9d3013f733e7dc7b94f19dc
```

命令只消费离线fixture并写本地`standard/v2`目录。输出中的订单、成交、费用、funding、现金、
margin、持仓和NAV均来自QExec账本事实。

## 质量门禁

```bash
ruff format --check .
ruff check .
pytest --cov=quant_crypto_basis --cov-branch --cov-report=term-missing --cov-fail-under=80
```

CI覆盖Python 3.10、3.11和3.12。关键E2E、确定性、PIT、序列、重复、缺口、双源、funding、
保证金和强平测试均为0 skip。
