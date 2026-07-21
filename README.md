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
