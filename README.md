# InviteFlow（邀程）

Python 邀请兑换平台，产品角色仅有 **user（用户）** 和 **admin（管理员）**。访客是用户访问状态；Worker、Scheduler、Provider 是内部组件，不增加产品角色。经销商分支及专属次数预算已取消。

## 当前状态

已实现 PostgreSQL 持久化基础、Alembic 迁移、用户匿名会话、管理员认证/退出/会话撤销、CSRF/Origin 校验、登录限流、真实 readiness、请求大小限制及安全错误响应。后端 Python 3.10+，部署配置选用 Python 3.13。依赖由 `uv.lock` 锁定，开发与测试使用项目独立 `.venv`。

前端位于 `frontend/`，提供 Vue 3 + TypeScript 用户页和管理员页，中英文界面及响应式布局；登录、会话恢复和退出连接真实后端。业务表单遇到 501 明确提示 Hook 尚未实现。

**尚未实现真实 CDK、邮箱分配、Claim 状态机、邀请或奖励动作、持久 Worker、Outbox、WebSocket。** 现有业务端点先验证会话/权限，再调用 Hook；认证通过但未接入时返回 `501 HOOK_NOT_IMPLEMENTED`。不得将前端表单、接口契约或测试夹具描述为业务成功。

## 本地开发

准备 PostgreSQL（请使用独立开发库），安装 uv 后：

```bash
uv sync --frozen --extra dev
cp .env.example .env
# 编辑 .env：数据库 URL、随机 session_secret、浏览器 public_origin
# 此处从环境传递给 Alembic；API 也支持从 .env 读取配置
set -a; . ./.env; set +a
uv run alembic upgrade head
uv run inviteflow-admin create admin
uv run inviteflow-api
```

密码由隐藏终端输入，无内置管理员/默认密码。API 默认监听 `127.0.0.1:8000`。

另开终端：

```bash
cd frontend
npm ci
npm run dev
```

前端 `http://127.0.0.1:5173`，用户页 `/`，管理员页 `/admin`。本地配置必须为 `INVITEFLOW_PUBLIC_ORIGIN=http://127.0.0.1:5173`；仅本地 HTTP 使用 `INVITEFLOW_COOKIE_SECURE=false`。生产环境必须 HTTPS 和 Secure Cookie。

## 基础端点

- `GET /healthz`：进程存活。
- `GET /readyz`：数据库连通且迁移版本匹配才返回 200，否则 503。
- `GET /api/v1/capabilities`：基础能力、两角色及保留的业务 Hook；implemented 仍不声明未实现业务。
- `/api/v1/public/sessions`、`/public/session`、`/public/logout`：用户会话。
- `/api/v1/staff/login`、`/staff/session`、`/staff/logout`：管理员会话。

## 测试

```bash
uv run ruff check src tests
uv run mypy src
uv run pytest
cd frontend && npm run typecheck && npm test && npm run build
```

数据库集成测试需 `INVITEFLOW_TEST_DATABASE_URL` 指向已迁移、可清空的 `inviteflow_test` 库（仅接受测试主机名/loopback）。**测试会清空该测试库的基础表，禁止指定生产库。** 未配置时集成测试明确跳过，不能当作 PostgreSQL 集成验收通过。

## 文档与边界

- [会话、权限与配置](docs/AUTHENTICATION.md)
- [数据库幂等执行契约](docs/IDEMPOTENCY.md)
- [容器启动与生产边界](docs/DEPLOYMENT.md)
- [本轮验收记录](docs/VERIFICATION.md)
- [Hook 契约与后续实现顺序](docs/HOOK_CONTRACTS.md)
- [前端开发说明](frontend/README.md)

完整产品级设计稿目前保留在工作区 `reports/CODEX_INVITATION_DEVELOPMENT_DESIGN.zh-CN.md`（仓库外，1.1 两角色版）。实际实现状态以本仓库代码及测试为准。

角色入口鉴权已实现；未来业务 Hook 必须自行执行 Claim/CDK 对象归属、次数预算及状态校验。`business_hooks_enabled=false` 强制占位实现，改为 true 不会自动接入上游。

后续外部请求应由事务 Outbox 与 Worker 执行，结果未知必须查证，不得盲目重发、返还 CDK 或回池。每完成一个独立功能并通过验证后创建本地 Git 提交；不自动推送。
