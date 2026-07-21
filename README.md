# FundOS

FundOS 是一个面向标准化投资组合的投研、风控、审批、发布和复盘系统。

当前正在实施第一阶段：数据与指标底座。现有代码提供无第三方依赖的组合计算内核，包括权重校验、组合收益、净值、年化收益、波动率、最大回撤和夏普比率。

目前还包括 SQLite 数据结构、资产池配置、CSV 行情校验与导入能力，以及可以重复运行的初始组合演示。

## 本地验证

```powershell
python -m unittest discover -s tests -v
python scripts/demo.py
python scripts/import_prices.py path\to\prices.csv --provider your-provider
```

## 目录

```text
src/fundos/domain/      投资组合领域对象
src/fundos/analytics/   确定性指标计算
src/fundos/storage/     SQLite 持久化
src/fundos/data_providers/ CSV 行情读取与校验
config/                 初始资产池配置
scripts/                可运行演示
tests/                  自动化测试
```

当前计算链路已经可以完成多资产共同交易日对齐、价格收益率转换、固定权重组合净值计算和 SQLite 持久化。

组合版本会在其生效日开始参与下一收益区间计算，系统支持跨版本连续净值、基准归一化、超额收益及绩效快照持久化。

第二阶段已经建立投资说明书约束、草稿/发布状态、受控发布和调仓审计记录。只有正式发布的版本会参与业绩计算。

候选组合现在通过显式状态机流转：`proposed → risk_passed → approved → published`。任何硬规则失败都会进入 `rejected`，未通过风控或投委会审批的方案不能发布。

风险审查同时验证行情数据新鲜度，并运行调用方提供的多个压力情景。聚合报告记录全部规则、硬规则失败数量和最终是否通过。

投研层保存结构化市场状态、报告置信度、资产方向观点及其证据引用。研究报告定稿后才能被候选组合引用，且证据发布日期不能晚于报告日期。

复盘层计算期初配置的几何链接收益贡献，将实际跨版本收益与不调仓反事实收益比较，并验证此前正面、负面或中性资产观点是否兑现。

## API

安装并启动：

```powershell
python -m pip install -e ".[test]"
fundos-api
```

OpenAPI 文档默认位于 `http://127.0.0.1:8000/docs`。查询接口覆盖产品、投资说明书、组合版本、业绩、研究、工作流审计和复盘结果。

写入接口覆盖资产初始化、产品与投资说明书创建、行情写入、研究报告定稿、候选组合生成、风险审查、投委会决策和正式发布。所有写入仍经过领域约束和工作流状态检查。

生产环境可设置 `FUNDOS_API_KEY`，写接口随后要求 `X-API-Key` 请求头。产品创建和组合发布支持 `Idempotency-Key`，相同请求重复提交会返回首次结果，不会重复写入。

## 产品界面

```powershell
.\.venv\bin\python.exe -m uvicorn fundos.api.main:app
```

打开 `http://127.0.0.1:8000/dashboard`。界面提供组合总览、资产配置、投资说明书、投研观点、证据来源、风险检查、投委会记录和业绩复盘五个工作区。

生成完整模拟演示数据：

```powershell
.\.venv\bin\python.exe scripts/seed_demo.py
```

该命令可重复运行，不会重复创建产品、版本、工作流或复盘记录。所有行情和研究材料均明确属于演示数据，不代表真实投资建议。

运行一次日常运营周期：

```powershell
.\.venv\bin\python.exe scripts/run_cycle.py --as-of 2026-07-10
```

周期任务会审计数据新鲜度、重算业绩并提示周度投委会或月度复盘是否到期。行情过期时任务进入阻断状态，不会继续推动投资决策。

周期结果可通过 `GET /products/{product_id}/operations` 查询，便于接入仪表盘、系统定时任务和外部告警。

## 真实市场数据

系统支持通过 [Alpha Vantage 官方日线接口](https://www.alphavantage.co/documentation/#daily) 同步真实市场数据。API Key 只从环境变量读取：

```powershell
$env:ALPHA_VANTAGE_API_KEY = "your-key"
.\.venv\bin\python.exe scripts/sync_market_data.py --compact
```

MVP 配置使用 ETF 作为资产类别代理，映射关系位于 `config/market_data.alpha_vantage.json`。这些代理价格不等同于指数点位或场外基金净值，正式组合上线前需要替换为获得授权且与产品说明书一致的数据源。

生产周期可以合并执行行情同步与运营检查：

```powershell
.\.venv\bin\python.exe scripts/run_production.py --attempts 3
```

每个资产同步步骤会按指数退避进行有限重试。管道运行及步骤结果保存在 `pipeline_runs` 和 `pipeline_steps`，部分失败会返回非零退出码，便于系统定时任务触发告警。
