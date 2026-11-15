# LangChain 订单日结与统计 API

基于 Conda、FastAPI、LangChain、MySQL 和 Redis 的订单报表模板。提供固定且参数化的数据库聚合查询、自然语言报表助手、JWT 认证、角色权限、Redis 限流、数据库瞬时故障重试和健康检查。

> 数据口径按模板约定：订单表为 `orders(id, created_at, status, amount, keyword, user_id)`；`paid` 和 `completed` 状态计入支付订单/金额。接入现有库时，先核对表名、字段类型、状态值和时区，并相应修改 `app/db/models.py` 与 `.env`。

## 项目结构

```text
app/
├── api/v1/routes/       # HTTP 路由与请求处理
├── core/                # 配置、认证、依赖注入、限流
├── db/                  # ORM 模型与异步数据库会话
├── services/            # 报表查询与 LangChain 助手
├── schemas.py           # API 请求/响应模型
├── cli.py               # 数据库初始化、用户管理命令
└── main.py              # FastAPI 应用装配与健康检查
tests/
├── unit/                # 单元测试
└── integration/         # API 集成测试
```

## 功能

- `admin`、`analyst`、`viewer` 三种角色；所有角色可读聚合报表，仅管理员可创建用户。
- JWT Bearer 令牌，密码使用 Argon2 哈希；停用用户立即无法继续访问。
- 日报与前一日对比、关键词逐日对比、订单区间统计和状态分布。
- LangChain 工具调用仅能访问预定义的聚合查询，不接受或执行模型生成的 SQL。
- Redis Lua 原子固定窗口限流；Redis 不可用时受保护接口返回 `503`，不绕过限流。
- MySQL 瞬时 `OperationalError` 最多重试 3 次；其余数据库错误不重试。
- `/health/live` 与检查 MySQL/Redis 的 `/health/ready`，并返回 `X-Request-ID`。
- 可选 OpenAI 兼容模型。未配置 `LLM_API_KEY` 时，报表 API 仍可用，自然语言接口明确返回 `503`。

## Conda 环境

项目使用仓库内的 `.conda` 前缀环境。运行一次初始化脚本会创建/更新环境、在尚未初始化时创建 `main` Git 仓库、生成本地 `.env` 并安装提交前检查：

```powershell
.\scripts\setup.ps1
conda activate .\.conda
```

VS Code 会优先使用仓库内的 `.conda\python.exe`。环境目录已加入 `.gitignore`，不会提交到 Git；其他开发者可从 `environment.yml` 和依赖文件重建。依赖清单有更新时，重新运行 `.\scripts\setup.ps1` 同步环境。

编辑 `.env` 中的 MySQL、Redis 连接信息和密钥。`.env` 及其他 `.env.*` 文件默认不会提交，唯一例外是用于模板的 `.env.example`。生成随机密钥示例：

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

如需手动检查或安装 Git Hook：

```powershell
.\scripts\check.ps1
conda run --prefix .\.conda python -m pre_commit install
```

提交代码时，Pre-commit 会使用 `.conda` 环境依次运行 Ruff、格式检查和测试。提交前的手动检查与 CI 可复用 `.\scripts\check.ps1`。不要将整个 Conda 环境导出并提交；通过 `environment.yml`、`requirements.txt` 和 `requirements-dev.txt` 管理项目依赖。

确保 MySQL 数据库和 Redis 服务已启动。生产环境请为应用创建仅有必要读写权限的 MySQL 账户；本服务的报表读取只执行固定的 `SELECT` 聚合语句。

## 初始化与启动

```powershell
python -m app.cli init-db
python -m app.cli create-user --username admin --role admin
uvicorn app.main:app --reload
```

`init-db` 只创建模板表，不会迁移或改写已有表。正式部署请使用经过审核的数据库迁移流程，并确保现有 `orders` 表字段与模型一致。创建用户时会交互式输入并确认密码，不将密码放进命令行参数。

文档地址：`http://127.0.0.1:8000/docs`

## API

| 方法 | 路径 | 权限 | 用途 |
|---|---|---|---|
| `POST` | `/api/v1/auth/token` | 无（按来源 IP 限流） | OAuth2 表单登录，获取 Bearer Token |
| `POST` | `/api/v1/admin/users` | admin | 创建 analyst/viewer/admin 用户 |
| `GET` | `/api/v1/reports/daily?date=2026-01-01` | 任一角色 | 日统计及前一日变化 |
| `GET` | `/api/v1/reports/keyword?keyword=coffee&start_date=2026-01-01&end_date=2026-01-07` | 任一角色 | 关键词匹配订单的逐日对比 |
| `GET` | `/api/v1/reports/orders?start_date=2026-01-01&end_date=2026-01-07&status=paid` | 任一角色 | 订单数、支付金额、状态分布及每日变化 |
| `POST` | `/api/v1/assistant/query` | 任一角色 | 使用已配置的 LangChain 模型查询报表 |
| `GET` | `/health/live`、`/health/ready` | 无 | 存活与依赖就绪检查 |

报表区间最多为 `REPORT_MAX_RANGE_DAYS` 天（默认 90 天）；日期按 UTC 日界线和左闭右开方式过滤，结束日期包含在统计范围内。请将 `orders.created_at` 统一存为 UTC（MySQL `DATETIME` 按 UTC 无时区值处理）。百分比变化以前一日为基数，基数为 0 时返回 `null`。关键词使用包含匹配；金额为 Decimal。

## 权限与部署建议

- 初始化后立即轮换 `.env` 里的示例凭据；不要提交 `.env`。
- 对外部署使用 HTTPS、密钥管理服务和网络层访问控制；按最小权限配置 MySQL/Redis。
- 多租户场景必须先定义并验证租户归属，再对所有查询统一增加租户过滤；模板暂不把 `user_id` 擅自解释为租户边界。
- `init-db` 不适用于管理生产 schema；生产请使用 Alembic 等迁移工具。
- 可选设置 `LLM_API_KEY`、`LLM_MODEL` 启用自然语言助手；避免向模型提供个人或敏感数据。

## 测试与检查

```powershell
.\scripts\check.ps1
```
