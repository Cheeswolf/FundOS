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

下一步将实现组合版本跨调仓日生效、基准净值对比和绩效快照。
