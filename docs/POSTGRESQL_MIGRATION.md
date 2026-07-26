# PostgreSQL 迁移

FundOS 当前运行时仍使用 SQLite。仓库已经具备 PostgreSQL 16 本地服务和目标环境
预检能力，但尚未把生产 API 切换至 PostgreSQL。不要把
`FUNDOS_POSTGRES_URL` 配置成现有 API 的数据库地址。

## 阶段

1. **目标环境预检（已完成）**：固定 PostgreSQL 16，检查连接、版本、SSL 和建表权限；
2. **兼容存储层（已完成）**：DB-API 连接、`?` 参数、完整性异常、时间表达式、
   元数据查询、identity 主键返回和幂等事务锁均已抽象；
3. **正式迁移基线（已完成）**：当前完整结构转换为 PostgreSQL DDL，按逻辑版本
   14 事务执行并写入 `schema_migrations`；后续结构变化继续追加迁移；
4. **API 连接与基础验收（已完成）**：`FUNDOS_DATABASE_URL` 选择数据库；CI 在
   PostgreSQL 16 上验证建表、API 写入、原子幂等和读取；
5. **数据搬迁（工具已完成，待实际执行）**：停写后锁定 SQLite，按外键顺序在单一
   事务中导入空 PostgreSQL，校正 identity 序列，并逐表核对数量和 SHA-256 摘要；
6. **完整双环境自动验收（已完成）**：同一业务契约在 SQLite 和 PostgreSQL 验证
   投研、提案、风控、投委会、发布、净值、基准、调度锁和审计链；PostgreSQL
   另验证并发幂等和失败回滚；
7. **切换与回退（工具已完成，待实际演练）**：真正停写、最终迁移、一致性快照、
   候选 API 只读冒烟和人工切换；开放 PostgreSQL 写入前失败则保持 SQLite。

## 启动预检数据库

在 `.env` 中设置独立的强密码，然后运行：

```powershell
docker compose -f compose.postgres.yaml up -d
python -m pip install -e ".[postgres]"
$env:FUNDOS_POSTGRES_URL = "postgresql://fundos:<password>@127.0.0.1:5432/fundos"
python scripts/check_postgres.py
python scripts/initialize_postgres.py
```

`ready: true` 只表示目标服务器满足迁移的基础条件，不表示 FundOS 已经完成数据库切换。
生产环境应启用 TLS；本地容器的 `ssl_enabled: false` 可用于开发，但生产验收不能通过。

## 当前已识别的兼容点

- `sqlite3.Connection` / `sqlite3.Row` 和 SQLite 异常类型；
- `PRAGMA`、`sqlite_master` 与在线备份 API；
- `INTEGER PRIMARY KEY AUTOINCREMENT`；
- `datetime(...)`、`substr(...)` 日期查询；
- SQLite 的 `?` 参数占位符；
- 原子幂等使用的 `BEGIN IMMEDIATE`；
- 当前迁移函数直接执行 SQLite schema 和列检查。

完成存储适配器前，SQLite 继续是唯一受支持的运行数据库。

当前 `PostgresDatabase` 已能为共用查询转换参数、管理提交/回滚，并为同一幂等键
取得事务级 advisory lock。`initialize()` 会在一个事务中幂等应用 PostgreSQL
schema 基线；执行失败时不会留下部分建表结果。

API 现在读取 `FUNDOS_DATABASE_URL`。留空时继续使用 `FUNDOS_DB_PATH` 指向的
SQLite；配置 `postgresql://...` 时使用 PostgreSQL。`FUNDOS_POSTGRES_URL` 仍只供
预检和初始化脚本使用。基础 API 验收不等于迁移完成；首次生产切换前必须完成数据
搬迁和完整双环境验收。

CI 的 `postgres-integration` 作业会启动临时 PostgreSQL 16，并运行最小 API
集成测试、SQLite 搬迁验收和完整业务契约。本地可使用：

```powershell
$env:FUNDOS_TEST_POSTGRES_URL = $env:FUNDOS_POSTGRES_URL
python -m unittest tests.test_postgres_integration -v
```

## 搬迁现有 SQLite 数据

搬迁工具只接受空目标业务表，不提供覆盖或合并选项。执行前应先备份 SQLite、停止
API 和调度写入，并保留原数据库用于回退：

```powershell
python scripts/backup_database.py
python scripts/check_postgres.py
python scripts/initialize_postgres.py
python scripts/migrate_sqlite_to_postgres.py --confirm-writes-stopped
```

工具在复制期间持有 SQLite 写锁，按外键依赖顺序导入，并在 PostgreSQL 单一事务内
完成。每张表都会比较行数和与顺序无关的 SHA-256 内容摘要；任何失败都会回滚整个
目标导入。浮点列使用 `DOUBLE PRECISION`，避免 SQLite 双精度数据降为 PostgreSQL
单精度。

## 切换演练

先让现有 SQLite API 进入真正的只读窗口。此模式在最外层拒绝所有修改请求，不会
连带写入审计表：

```powershell
$env:FUNDOS_READ_ONLY = "true"
# 重启当前 API，并确认 POST 请求返回 READ_ONLY_WINDOW / 503
```

完成最终备份和搬迁后，可在另一个端口启动同样只读的 PostgreSQL 候选实例，再运行：

```powershell
python scripts/drill_postgres_cutover.py `
  --confirm-writes-stopped `
  --candidate-api-url http://127.0.0.1:8001
```

演练会比较 schema 版本、表集合、逐表行数、逐表 SHA-256、审计链，并检查产品、
已发布版本、组合/基准净值、调度和管道记录。候选 API 只调用 `/health`、
`/products` 和 `/dashboard` 三个只读端点。任一检查失败，结论都是
`remain_on_sqlite`，且工具不会修改 `FUNDOS_DATABASE_URL`。

全部通过后才能由运维显式设置 `FUNDOS_DATABASE_URL` 并重启正式实例。先保持
`FUNDOS_READ_ONLY=true` 完成入口冒烟，确认后再开放写入。

回退边界必须明确：在 PostgreSQL 开放写入之前，可以直接恢复 SQLite 入口；一旦
PostgreSQL 已产生新业务写入，不能直接切回旧 SQLite，否则会丢数据，必须先执行反向
数据同步或进入新的停写迁移窗口。
