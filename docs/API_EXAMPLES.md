# FundOS API 使用示例

以下示例假设服务地址为 `http://127.0.0.1:8000`，写接口已配置 API Key。

```powershell
$baseUrl = "http://127.0.0.1:8000"
$headers = @{ "X-API-Key" = "replace-with-your-key" }
```

## 1. 健康检查

```powershell
Invoke-RestMethod "$baseUrl/health"
```

## 2. 创建资产池

```powershell
$body = @(
  @{ symbol = "EQUITY"; name = "权益资产"; asset_class = "equity" },
  @{ symbol = "BOND"; name = "债券资产"; asset_class = "fixed_income" },
  @{ symbol = "CASH"; name = "现金资产"; asset_class = "cash" }
) | ConvertTo-Json

Invoke-RestMethod "$baseUrl/assets" -Method Post -Headers $headers `
  -ContentType "application/json" -Body $body
```

## 3. 创建产品和投资说明书

```powershell
$productHeaders = $headers.Clone()
$productHeaders["Idempotency-Key"] = "create-balanced-product-v1"

$body = @{
  product_id = "balanced-product"
  name = "多资产平衡组合"
  benchmark_symbol = "BALANCED_BENCHMARK"
  objective = "在控制回撤的前提下实现中长期稳健增值"
  risk_level = "中等风险"
  max_single_asset_weight = 0.60
  min_cash_weight = 0.05
  max_turnover = 0.30
  maximum_data_age_days = 3
  maximum_stress_loss = 0.20
} | ConvertTo-Json

Invoke-RestMethod "$baseUrl/products" -Method Post -Headers $productHeaders `
  -ContentType "application/json" -Body $body
```

## 4. 写入行情

```powershell
$body = @{
  provider = "licensed-provider"
  prices = @(
    @{ symbol = "EQUITY"; trade_date = "2026-07-20"; close = 101.25 },
    @{ symbol = "BOND"; trade_date = "2026-07-20"; close = 100.30 },
    @{ symbol = "CASH"; trade_date = "2026-07-20"; close = 100.01 }
  )
} | ConvertTo-Json -Depth 5

Invoke-RestMethod "$baseUrl/market-prices" -Method Post -Headers $headers `
  -ContentType "application/json" -Body $body
```

## 5. 创建并定稿研究报告

```powershell
$body = @{
  report_id = "research-2026w30"
  product_id = "balanced-product"
  as_of_date = "2026-07-20"
  market_regime = "neutral"
  summary = "增长与通胀信号均衡，维持多资产配置。"
  confidence = 0.75
  evidence = @(
    @{
      evidence_id = "evidence-2026w30-1"
      title = "周度市场数据"
      source = "licensed-source"
      url = "https://example.com/research/2026w30"
      published_at = "2026-07-20T00:00:00Z"
    }
  )
  asset_views = @(
    @{
      asset_symbol = "EQUITY"
      direction = "neutral"
      confidence = 0.70
      thesis = "估值和盈利预期处于平衡状态。"
      evidence_ids = @("evidence-2026w30-1")
    }
  )
  finalize = $true
} | ConvertTo-Json -Depth 8

Invoke-RestMethod "$baseUrl/research" -Method Post -Headers $headers `
  -ContentType "application/json" -Body $body
```

## 6. 创建候选组合

```powershell
$body = @{
  version_id = "balanced-v1"
  product_id = "balanced-product"
  version_number = 1
  effective_date = "2026-07-20"
  weights = @(
    @{ asset_symbol = "EQUITY"; weight = 0.55 },
    @{ asset_symbol = "BOND"; weight = 0.40 },
    @{ asset_symbol = "CASH"; weight = 0.05 }
  )
  research_report_id = "research-2026w30"
  rationale = "建立初始多资产配置"
  created_by = "portfolio-manager"
  run_id = "workflow-2026w30"
} | ConvertTo-Json -Depth 6

Invoke-RestMethod "$baseUrl/proposals" -Method Post -Headers $headers `
  -ContentType "application/json" -Body $body
```

## 7. 执行风险审查

```powershell
$body = @{
  provider = "licensed-provider"
  as_of_date = "2026-07-20"
  stress_scenarios = @{
    equity_selloff = @{ EQUITY = -0.25; BOND = 0.03; CASH = 0.00 }
    rate_shock = @{ EQUITY = -0.08; BOND = -0.10; CASH = 0.00 }
  }
} | ConvertTo-Json -Depth 6

Invoke-RestMethod "$baseUrl/workflows/workflow-2026w30/risk-review" `
  -Method Post -Headers $headers -ContentType "application/json" -Body $body
```

## 8. 投委会决定

```powershell
$opinion = @{
  member_role = "risk"
  recommendation = "approve"
  rationale = "硬性风险规则全部通过。"
  alternative_weights = @()
  conditions = "若组合波动率显著上升则重新审查。"
  submitted_by = "risk-officer"
} | ConvertTo-Json -Depth 5

Invoke-RestMethod "$baseUrl/workflows/workflow-2026w30/committee-opinions" `
  -Method Post -Headers $headers -ContentType "application/json" -Body $opinion

$body = @{
  approved = $true
  rationale = "已审阅研究和风险委员意见，同意发布。"
  decided_by = "committee-chair"
  minimum_opinions = 2
} | ConvertTo-Json

Invoke-RestMethod "$baseUrl/workflows/workflow-2026w30/committee-decision" `
  -Method Post -Headers $headers -ContentType "application/json" -Body $body
```

每个 `member_role` 对同一工作流只能提交一次不可变意见。意见可以是
`approve`、`reject` 或 `abstain`，并可附带覆盖全部提案资产且合计为 100%
的备选权重。最终决策与委员意见分开保存，主席需要在最终依据中说明如何处理分歧。

## 9. 发布组合

```powershell
$publishHeaders = $headers.Clone()
$publishHeaders["Idempotency-Key"] = "publish-workflow-2026w30"

Invoke-RestMethod "$baseUrl/workflows/workflow-2026w30/publish" `
  -Method Post -Headers $publishHeaders
```

## 10. 查询结果

```powershell
Invoke-RestMethod "$baseUrl/products/balanced-product/versions"
Invoke-RestMethod "$baseUrl/products/balanced-product/performance"
Invoke-RestMethod "$baseUrl/products/balanced-product/research"
Invoke-RestMethod "$baseUrl/products/balanced-product/workflows"
Invoke-RestMethod "$baseUrl/products/balanced-product/reviews"
Invoke-RestMethod "$baseUrl/pipeline-runs"
Invoke-RestMethod "$baseUrl/scheduled-jobs/runs?job_name=daily-production-pipeline&limit=20"
Invoke-RestMethod "$baseUrl/scheduled-jobs/locks"
Invoke-RestMethod "$baseUrl/alerts?status=failed"
```

研究报告以草稿创建时，可由 operator 定稿：

```powershell
Invoke-RestMethod "$baseUrl/research/research-2026w30/finalize" `
  -Method Post -Headers $headers
```

仪表盘“决策操作”页提供同一受控流程的操作入口。API Key 仅保存在页面内存，
通过 `X-API-Key` 请求头发送；刷新页面后清空。研究定稿、创建提案和风险审查
需要 operator 权限，投委会决策和发布需要 admin 权限。发布动作仍使用
`Idempotency-Key` 防止重复提交。

计划任务状态包括 `running`、`succeeded`、`failed`、`skipped` 和
`abandoned`。活动租约响应包含 `active` 布尔值，但不暴露内部 owner ID。

## 11. 常见响应状态

| 状态码 | 含义 |
|---|---|
| `200` | 查询或状态流转成功 |
| `201` | 资源创建成功 |
| `401` | API Key 缺失或错误 |
| `404` | 产品或工作流不存在 |
| `409` | 唯一约束或幂等键冲突 |
| `422` | 输入或领域规则不合法 |

## 12. 分页与错误契约

主要运行记录接口支持 `limit` 和 `offset`，响应正文继续保持数组以兼容已有客户端：

```powershell
$response = Invoke-WebRequest "$baseUrl/pipeline-runs?limit=20&offset=0"
$response.Headers["X-Total-Count"]
$response.Headers["X-Limit"]
$response.Headers["X-Offset"]
```

适用接口包括运营周期、生产管道、计划任务、告警、模型调用、审计事件、原始研究
证据和证据采集运行。

所有 API 错误均返回兼容的 `detail`，以及机器可读结构：

```json
{
  "detail": "committee decision requires at least 2 opinions",
  "error": {
    "code": "DOMAIN_RULE_VIOLATION",
    "message": "committee decision requires at least 2 opinions",
    "request_id": "request-id",
    "issues": []
  }
}
```

常用错误码包括：

- `AUTHENTICATION_REQUIRED`
- `PERMISSION_DENIED`
- `RESOURCE_NOT_FOUND`
- `RESOURCE_CONFLICT`
- `DOMAIN_RULE_VIOLATION`
- `VALIDATION_ERROR`

客户端可以通过 `X-Request-ID` 传入自己的请求 ID；服务端会在响应头和错误正文中
返回相同 ID。未提供时由服务端生成。
