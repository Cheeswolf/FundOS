# PostgreSQL 迁移

FundOS 当前运行时仍使用 SQLite。仓库已经具备 PostgreSQL 16 本地服务和目标环境
预检能力，但尚未把生产 API 切换至 PostgreSQL。不要把
`FUNDOS_POSTGRES_URL` 配置成现有 API 的数据库地址。

## 阶段

1. **目标环境预检（已完成）**：固定 PostgreSQL 16，检查连接、版本、SSL 和建表权限；
2. **兼容存储层**：引入 PostgreSQL 连接适配器，替换 `?` 参数、`PRAGMA`、
   `AUTOINCREMENT` 和 SQLite 日期函数；
3. **正式迁移文件**：为 PostgreSQL 建立可向前执行、可验证的版本化 DDL；
4. **数据搬迁**：停写后导出 SQLite，按外键顺序导入 PostgreSQL并核对数量和摘要；
5. **双环境验收**：在 SQLite 和 PostgreSQL 分别运行领域、API、调度和并发测试；
6. **切换与回退**：备份、停写、最终增量导入、切换连接、冒烟检查；失败则切回
   SQLite 只读副本。

## 启动预检数据库

在 `.env` 中设置独立的强密码，然后运行：

```powershell
docker compose -f compose.postgres.yaml up -d
python -m pip install -e ".[postgres]"
$env:FUNDOS_POSTGRES_URL = "postgresql://fundos:<password>@127.0.0.1:5432/fundos"
python scripts/check_postgres.py
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
