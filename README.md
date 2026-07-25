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

运行日志可通过 `GET /pipeline-runs` 查询，告警通过 `GET /alerts` 查询。设置 `FUNDOS_ALERT_WEBHOOK_URL` 后，失败或部分失败的生产运行会尝试发送 JSON Webhook；发送失败的事件保留在数据库中，后续运行可以重试。

## 容器部署

```powershell
Copy-Item .env.example .env
docker compose up --build -d
```

服务启动时自动执行版本化数据库迁移，SQLite 数据保存在独立卷中。生产管道输出 JSON 结构化日志，可由容器日志平台直接采集。

## 持续集成

GitHub Actions 会在 `main` 分支推送及 Pull Request 上自动执行完整测试、源码编译检查和 Docker 镜像构建。Dependabot 每周检查 Python、GitHub Actions 和 Docker 基础镜像更新。

## 项目文档

- [运行与运营手册](docs/OPERATIONS.md)
- [API 使用示例](docs/API_EXAMPLES.md)
- [生产部署检查清单](docs/DEPLOYMENT_CHECKLIST.md)
- [项目整体审计](docs/PROJECT_AUDIT.md)

## AI 研究草稿

系统支持通过兼容 Chat Completions 协议的模型服务生成结构化研究草稿。模型仅能使用输入文件中的可信证据，输出还会经过资产覆盖、证据引用、观点方向和置信度校验；生成结果保持 `draft` 状态，仍需人工确认和既有审批流程。

```powershell
$env:FUNDOS_LLM_API_KEY = "your-key"
$env:FUNDOS_LLM_MODEL = "your-model"
$env:FUNDOS_LLM_BASE_URL = "https://your-provider.example/v1"
python scripts/generate_research.py path\to\research-input.json
```

可通过 `FUNDOS_LLM_MAX_DAILY_COST_USD`、`FUNDOS_LLM_MAX_DAILY_TOKENS` 和
`FUNDOS_LLM_CIRCUIT_FAILURE_THRESHOLD` 设置每日预算及连续失败熔断阈值；设为 `0`
表示关闭对应限制。策略触发时不会调用模型，并会写入运营告警。

## API 权限

`FUNDOS_API_KEY` 保持向后兼容并具有 `admin` 权限。生产环境可使用
`FUNDOS_API_KEYS_JSON` 配置分级密钥，例如
`{"operations-key":"operator","governance-key":"admin"}`。`operator` 可维护数据、研究、
提案及风险审查；投委会决策、组合发布、告警处置和模型熔断重置仅允许 `admin`。
所有修改型 API 请求都会写入审计日志；`GET /audit-events` 仅允许 `admin` 查询。
日志保存密钥指纹、角色、路径、结果和请求 ID，不保存密钥或请求正文。
管理员可通过 `/audit-events/integrity` 校验哈希链，通过 `/audit-events/export.csv`
导出记录，并通过 `POST /audit-events/retention?days=365` 执行保留策略。清理至少保留
30 天，且会保存链锚点以继续验证剩余记录。

## 数据库备份与恢复

```powershell
python scripts/backup_database.py
python scripts/drill_recovery.py
python scripts/restore_database.py backups\fundos-xxx.sqlite3 data\fundos.sqlite3 --replace
```

备份包含 SHA-256、文件大小、迁移版本及关键表行数清单。恢复前必须通过完整性校验；
替换已有数据库时会先生成恢复前副本。建议定期运行非破坏性的恢复演练，而不仅是检查备份文件存在。
