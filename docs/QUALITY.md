# FundOS 测试覆盖率与性能基线

## 验收基线

- Python 3.12；
- 全部单元、接口、工作流和恢复测试通过；
- 分支覆盖率不低于 75%；
- 20 年、6 资产异步净值计算不超过 5 秒；
- Python 源码编译检查通过；
- `git diff --check` 通过。

当前本机结果：

- 150 项测试通过；
- 总分支覆盖率 82%；
- 20 年、6 资产、5,040 个估值日和 27,492 条原始价格的核心计算约 0.075 秒；
- 标准化过程中包含 2,748 次时点安全前值延用。

性能时间会受机器和运行环境影响，因此 CI 门槛采用 5 秒，而不是把本机耗时作为
硬性限制。该基准用于发现数量级退化，不代表 API、数据库或外部供应商端到端延迟。

## 本地运行

```powershell
python -m coverage erase
python -m coverage run -m unittest discover -s tests -v
python -m coverage report
python scripts/benchmark_history.py --years 20 --assets 6 --max-seconds 5
python -m compileall -q src scripts
git diff --check
```

## 属性测试覆盖的不变量

`tests/test_analytics_properties.py` 使用固定随机种子重复验证：

- 非负且合计为 100% 的权重，其组合收益不会超出同期资产收益上下界；
- 价格序列乘以任意正常数不会改变收益率；
- 组合净值严格等于逐期复利；
- 异步净值对齐只能使用估值日当时或之前已经出现的数据。

固定种子保证失败可以稳定复现。新增核心数学逻辑时，应优先补充不变量，而不仅是
单一示例输入。

## 性能基准范围

`scripts/benchmark_history.py` 测量：

1. 六资产异步净值时点对齐；
2. 单资产收益率计算；
3. 固定权重组合收益计算；
4. 组合净值复利。

基准刻意不包含网络请求和 SQLite 写入，以便稳定识别核心计算性能回退。数据同步、
数据库和 API 延迟由生产运行指标与 OpenTelemetry 单独监控。
