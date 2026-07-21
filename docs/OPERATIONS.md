# FundOS 运行与运营手册

## 1. 系统边界

FundOS 生产和跟踪标准化投资组合方案，不托管资金、不读取用户持仓，也不直接执行交易。所有组合发布必须经过风险审查和投委会审批。

仓库包含两类数据模式：

- `demo-synthetic`：用于演示和测试的模拟行情；
- `alpha-vantage-etf-proxy`：真实 ETF 代理行情，不等同于指数点位或场外基金净值。

## 2. 首次启动

### 本地 Python

```powershell
python -m venv .venv
python -m pip install -e ".[test]"
python scripts/seed_demo.py
python -m uvicorn fundos.api.main:app
```

访问地址：

- 产品台：`http://127.0.0.1:8000/dashboard`
- OpenAPI：`http://127.0.0.1:8000/docs`
- 健康检查：`http://127.0.0.1:8000/health`

### Docker Compose

```powershell
Copy-Item .env.example .env
docker compose up --build -d
docker compose ps
```

启动日志：

```powershell
docker compose logs -f fundos
```

## 3. 必要环境变量

| 变量 | 用途 | 是否敏感 |
|---|---|---|
| `FUNDOS_DB_PATH` | SQLite 文件路径 | 否 |
| `FUNDOS_API_KEY` | 写接口认证 | 是 |
| `ALPHA_VANTAGE_API_KEY` | 真实行情认证 | 是 |
| `FUNDOS_ALERT_WEBHOOK_URL` | 失败告警出口 | 通常是 |

生产密钥不得提交到 Git，也不要写入日志或截图。

## 4. 日常运行

### 仅运行运营检查

```powershell
python scripts/run_cycle.py
```

该命令检查数据新鲜度、更新业绩并判断周度投委会和月度复盘是否到期。

### 同步真实行情并运行运营检查

```powershell
python scripts/run_production.py --attempts 3 --retry-delay 1
```

建议由系统任务每天收盘后运行一次。进程退出码为零表示全部成功，非零表示部分失败或完全失败。

### 单独同步行情

```powershell
python scripts/sync_market_data.py --compact
```

首次回填历史数据时移除 `--compact`。供应商配额有限时，可用多个 `--symbol` 参数分批同步。

## 5. 运营节奏

### 每日

1. 同步行情；
2. 检查缺失值和数据日期；
3. 重算组合净值与绩效；
4. 检查生产管道和告警状态；
5. 不自动生成或发布调仓。

### 每周

1. 定稿结构化研究报告；
2. 生成候选组合；
3. 执行风险规则和压力测试；
4. 投委会批准、拒绝或暂缓；
5. 批准后发布新版本。

### 每月

1. 计算资产收益贡献；
2. 比较实际收益与不调仓反事实收益；
3. 验证历史研究观点；
4. 保存复盘结论和改进事项。

## 6. 状态与告警

运营周期状态：

- `healthy`：数据与运营节奏正常；
- `attention_required`：周度或月度任务到期；
- `blocked`：数据缺失或过期，禁止继续推进。

生产管道状态：

- `succeeded`：所有步骤成功；
- `partial`：部分行情或产品周期失败；
- `failed`：没有可用的成功步骤。

检查接口：

```text
GET /pipeline-runs
GET /alerts?status=pending
GET /alerts?status=failed
GET /products/{product_id}/operations
```

## 7. 故障处理

### 行情 API 限流

1. 查看 `pipeline_steps.message`；
2. 停止高频重试；
3. 按资产分批同步；
4. 配额恢复后重新运行；
5. 确认运营周期不再处于 `blocked`。

### 数据过期

1. 确认供应商密钥及网络；
2. 检查全部组合资产和基准是否有最新数据；
3. 补齐缺失数据；
4. 重新运行生产周期；
5. 不要手工绕过数据新鲜度规则。

### Webhook 失败

告警会保留为 `failed`，不会丢失。恢复 Webhook 后再次运行生产任务，即可重新投递待处理告警。

### 数据库异常

1. 停止写入进程；
2. 保留故障数据库副本；
3. 使用最近备份恢复到新路径；
4. 启动服务并确认 `/health`；
5. 校验迁移版本、产品版本数量和最新净值日期。

## 8. SQLite 运行边界

当前 SQLite 方案适合 MVP 单实例运行。不要同时启动多个写入副本，也不要将同一数据库文件挂载给多个容器实例。需要多实例、高并发或更严格高可用时，应先迁移至 PostgreSQL。

