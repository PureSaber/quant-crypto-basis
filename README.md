# quant-crypto-basis

本版本`0.1.2`完成M8运行时发布列车升级。认证路径继续只消费离线fixture，不改变基差策略、
统一QExec执行/账本或历史`standard/v1`语义；费用、滑点、funding、保证金、强平和NAV不由
策略自行计算或修改。

`quant-crypto-basis`是BTC/ETH现货与USDT线性永续的离线basis/funding研究包。认证范围仅限
research、backtest和paper-simulation；没有联网采集、live broker、API key、凭据读取或真实订单发送路径。

## 认证边界

- 数据：只读取仓内脱敏Binance/OKX fixture，通过`quant-data-kit v0.8.1`的标准
  `InstrumentSpec`、`SymbolMapping`、provider adapter和`MarketEvent`契约。
- 信号：`BasisFundingStrategy.on_event`是唯一产生`OrderIntent`的位置；策略不修改持仓、现金或NAV。
- 执行：只使用`quant-execution v0.5.1`的`DeterministicRunEngine`、
  `RuleBookRiskGate`、匹配模型和`ExactAccountLedger`。
- 审计：按每个`available_at`阶段从同一个执行引擎与账本实例取得`AccountSnapshot`，逐时点保留
  event id、mark、funding、fee、fill、持仓、保证金和NAV变化；不维护第二套状态。
- 产物：只使用`quant-lab v0.3.1`的`write_standard_run_v2(profile="backtest-ledger")`，
  将上述多时点账本事实写入完整产物，随后由`load_and_validate_standard_run`回读。

Fixture适用期明确限定为`2026-01-02T00:00:00Z`至`2026-01-03T00:00:00Z`，不代表交易所
上市历史。Binance与OKX仅做适配完整性、稳定instrument映射和交叉质量核验，不假定逐笔价格相等。
L2范围严格限于fixture中的BTC现货盘口回放；ETH现货及所有永续只认证相应的非L2事件，不能
据此宣称全标的L2认证。

## 安装与运行

依赖在`pyproject.toml`和`requirements.lock`中固定到已发布annotatedtag：QDK`v0.8.1`
（peeledcommit`8f258f11be8e4d8edddcd41b79b817bd6c925970`）、QExec`v0.5.1`
（`15e4e5c9dbaf2fe9b438732b2e94db295d5ea58c`）和QLab`v0.3.1`
（`27489d270e132adbec1bced93eb2ae84ad5e1a9b`）。

```bash
python -m pip install --no-deps --requirement requirements.lock
python -m pip check
python -m pip install --no-deps --no-build-isolation --editable .
python -m pip check
qcb-run-fixture --source binance --output output/demo \
  --run-id crypto-basis-demo
```

命令只消费离线fixture并写本地`standard/v2`目录。输出中的订单、成交、费用、funding、现金、
margin、持仓和NAV均来自QExec账本事实。`code_version`总是从当前`quant-crypto-basis`仓库的
clean HEAD解析；工作树dirty时拒绝写出。可选`--code-version <完整SHA>`仅作为预期值，若与
当前HEAD不符同样拒绝写出。

每次run都会读取并核验Binance与OKX两源fixture。manifest的`dataset_snapshots`记录
catalog/index及两源fixture的SHA-256，`metrics`保留双源行数、事件类型和共同instrument质量，
`lineage`区分所选执行源与双源QA来源。

## 质量门禁

```bash
ruff format --check .
ruff check .
pytest --cov=quant_crypto_basis --cov-branch --cov-report=term-missing --cov-fail-under=80
```

`requirements.lock`由Python3.10重建，覆盖runtime、dev和editable-build依赖；CI在Python3.10、
3.11、3.12中严格按锁安装、执行两次`pip check`、Ruff、完整测试和认证产物核心纯分支覆盖率门禁。
锁重建命令为：

```bash
pip-compile --extra dev --build-deps-for editable --allow-unsafe --strip-extras \
  --resolver backtracking --index-url https://pypi.org/simple \
  --output-file requirements.lock pyproject.toml
```

迁移只新增不可变`standard/v2`目录，不覆盖历史v1；回滚使用Git revert同时恢复治理文件和锁，
旧tag不移动、不覆盖。

CI覆盖Python 3.10、3.11和3.12。关键E2E、确定性、PIT、序列、重复、缺口、双源、funding、
保证金和强平测试均为0 skip。
