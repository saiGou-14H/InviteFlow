# 会话、权限与持久化基础

本页描述已实现能力；完整产品设计中的 CDK、资源池、任务状态机和真实 Provider 仍未实现。产品角色只允许 `user`、`admin`。

## 已实现

- PostgreSQL + SQLAlchemy async；显式 Alembic `0001_foundation`，不在应用启动时自动建表。
- `staff_accounts` 仅允许 admin；用户使用匿名 `public_sessions`，后台使用独立 `staff_sessions`。
- Cookie 只保存高熵随机令牌；数据库保存按角色区分的 HMAC 摘要。CSRF token 由会话导出，其摘要保存在数据库。
- 默认 Cookie 为 `__Host-if-user` / `__Host-if-admin`，Secure、HttpOnly、SameSite=Lax、Path=/，无 Domain。
- 管理员密码 Argon2id；未知账号执行占位哈希校验，失败统一返回 INVALID_CREDENTIALS。
- 读取时检查过期、撤销、管理员账号状态与 session_epoch；滚动有效期不得超过绝对上限。
- 写请求校验精确 Origin 和 X-CSRF-Token；会话创建与登录也要求同源 Origin，但尚无会话时不要求 CSRF token。
- 登录按用户名摘要和连接来源地址摘要分别做 PostgreSQL 原子限流，默认每分钟各 5 次尝试，包含成功尝试。不信任客户端 X-Forwarded-For。
- 登录、登录失败、退出、管理员 CLI 操作记录审计，不记录密码/Cookie 原文。
- /healthz 仅用于进程存活；/readyz 检查真实数据库连接及精确迁移版本，不可用时返回 503。
- 请求体上限默认 256 KiB；校验错误不回显输入；响应默认 no-store。

## API

成功会话响应为 `{"status":"ok","data":{"role":"user或admin","actor_id":"UUID","csrf_token":"..."}}`。浏览器不读取 HttpOnly Cookie；CSRF token 只放内存，刷新时从 session 查询恢复。

| 方法 | 路径（前缀 /api/v1） | 作用 |
|---|---|---|
| POST | /public/sessions | 创建或复用当前匿名用户会话，不重置既有会话绝对期限 |
| GET | /public/session | 验证并返回当前用户会话 |
| POST | /public/logout | 服务端撤销当前用户会话后清除 Cookie |
| POST | /staff/login | username/password 登录，轮换本浏览器原管理会话 |
| GET | /staff/session | 验证并返回管理员会话 |
| POST | /staff/logout | 服务端撤销当前管理会话后清除 Cookie |

所有 `/claims/...` 入口依赖用户会话；`/admin/...` 依赖管理员会话。Hook 的 actor_id 已替换为可信会话提供的真实 UUID。未经认证为 401；用户尝试管理入口为 403；认证通过但业务未接入返回 501。没有配置持久层时不创建临时/内存会话，返回 503。

## 配置与 CLI

复制根目录 `.env.example` 为 `.env` 并填入**自己生成**的数据库密码及至少 32 字符随机会话密钥。密钥轮换会使旧会话和旧幂等摘要失效，生产必须制定轮换策略。不要提交 `.env`。

```bash
uv sync --frozen --extra dev
# Alembic 只读取进程环境，不自动加载 .env；该模板内容可由 shell 加载
set -a; . ./.env; set +a
uv run alembic upgrade head
uv run inviteflow-admin create admin
uv run inviteflow-api
```

管理员 CLI 从隐藏终端输入密码，不支持命令行明文密码参数；无默认管理员账号或默认密码。

```bash
uv run inviteflow-admin reset-password admin
uv run inviteflow-admin revoke-sessions admin
uv run inviteflow-admin disable admin
```

重置密码、撤销会话、禁用账号都会递增 session_epoch；旧管理会话随后被拒绝。

## 边界

- 实现了入口角色校验，**不是已实现完整 Claim 对象级授权**。真实业务 Hook 必须再次检查任务/批次归属；目前业务入口无数据可返回，只返回 501。
- `business_hooks_enabled=false` 会强制使用占位 Hook；设为 true 也不会自动产生真实业务实现。默认 capabilities 的 implemented 指业务能力，仍为空；infrastructure 单独列基础能力。
- 没有 Redis、WebSocket、持久 Worker、业务 Outbox、资源预算和 CDK 账本；不能宣称完整业务系统上线。
- 登录限流不替代边缘限流/WAF。反向代理下应根据可信代理配置传入真实来源，否则可能共享代理来源额度。
- 尚未实现会话/限流旧数据定期清理、审计归档、MFA；上线前补齐运维保留策略和安全审查。
- `cookie_secure=false` 仅供本地 HTTP，改用 `if-user-dev` / `if-admin-dev`；production 环境强制 HTTPS、安全 Cookie、数据库与密钥。
