# 真实试用产品的 ResearchAgent

ResearchAgent 已接入受控投研入口，但不会自动调仓或交易。它只根据人工提供的可信证据生成结构化投研草稿，草稿仍需人工检查和定稿。

## 0. 注册来源并导入原始证据

首次运行或来源配置更新后执行：

```powershell
python scripts/register_research_sources.py
```

复制 `config/raw_evidence.example.json`，填写真实标题、原始 URL、发布时间、
涉及资产和事实正文，然后导入：

```powershell
python scripts/import_research_evidence.py config/你的原始证据文件.json
```

导入过程会校验来源白名单、域名、资产覆盖和时间戳，保存采集时间及 SHA-256。
重复内容不会再次写入。新证据固定进入 `pending` 状态。

审核人核对原始链接、标题、日期、事实正文和资产标签后，执行批准或拒绝：

```powershell
python scripts/review_research_evidence.py raw-证据ID `
  --approve `
  --reviewed-by "审核人标识" `
  --note "已核对来源、日期、正文和资产标签"
```

拒绝时将 `--approve` 改为 `--reject`。审核决定不可覆盖；如来源发布修订内容，
应按新内容重新导入为另一条原始证据。

批准证据覆盖当前组合全部资产后，可以自动生成 ResearchAgent 输入：

```powershell
python scripts/build_approved_research_input.py `
  --report-id fundos-trial-research-2026-07-31 `
  --as-of 2026-07-31 `
  --output config/research.real_trial.2026-07-31.json
```

生成过程只读取 `approved` 证据，会重新校验内容哈希、来源状态、发布时间和资产
覆盖。`pending`、`rejected`、来源已停用或内容完整性失败的记录均不会进入模型输入。

## 1. 准备投研输入

人工通道可复制 `config/research.real_trial.example.json`，例如保存为
`config/research.real_trial.2026-07-26.json`。

填写要求：

- `report_id` 必须唯一，建议包含报告日期；
- `as_of_date` 不得早于任何证据的发布时间；
- `asset_symbols` 必须与当前已发布组合完全一致；
- 每条证据必须来自官方或已授权来源；
- `content` 应填写模型可以直接分析的事实摘录或人工核验摘要；
- 不要把网页链接本身当作证据内容，Agent 不会自行打开链接。

## 2. 配置模型

PowerShell 当前会话：

```powershell
$env:FUNDOS_LLM_API_KEY = "你的密钥"
$env:FUNDOS_LLM_MODEL = "deepseek-v4-flash"
$env:FUNDOS_LLM_BASE_URL = "https://api.deepseek.com"
$env:FUNDOS_LLM_PROVIDER = "deepseek"
$env:FUNDOS_LLM_MAX_DAILY_COST_USD = "1"
$env:FUNDOS_LLM_MAX_DAILY_TOKENS = "100000"
```

除 API Key 外，其余变量均可省略。项目默认使用 `deepseek-v4-flash` 和
`https://api.deepseek.com`，输入、输出计费估算分别按每百万 Token 0.14 美元和
0.28 美元记录，并启用每日 1 美元、100,000 Token 的安全上限。将预算设为 `0`
表示关闭对应限制，而不是零额度。

需要更高质量时可将模型改为 `deepseek-v4-pro`，并同步设置当前 Pro 模型的价格：

```powershell
$env:FUNDOS_LLM_MODEL = "deepseek-v4-pro"
$env:FUNDOS_LLM_INPUT_COST_PER_MILLION = "0.435"
$env:FUNDOS_LLM_OUTPUT_COST_PER_MILLION = "0.87"
```

## 3. 生成草稿

```powershell
python scripts/generate_research.py config/research.real_trial.2026-07-26.json
```

执行前会检查产品、报告 ID、当前组合资产、证据正文和时间安全性。检查失败时不会调用模型，也不会产生费用。

成功后，报告以 `draft` 状态保存，并显示在仪表盘“投研观点”页。它不会自动进入风险审查、投委会或调仓流程。

## 4. 当前安全边界

- Agent 只能使用提交给它的证据；
- 模型输出必须通过固定结构校验；
- 每个资产观点必须引用证据；
- 所有调用记录 Token、成本、耗时和错误；
- 日成本、日 Token 和连续失败熔断均在模型调用前生效；
- Agent 无交易权限，不能自行发布组合版本。
- 原始证据审核 API 仅允许 `admin` 角色访问；
- 证据审核决定不可修改，所有 API 审核操作进入审计日志。
