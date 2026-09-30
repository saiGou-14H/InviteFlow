# 本轮验收记录

验证日期：2026-09-30。所有真实业务 Hook 仍为占位实现，业务成功不能由基础设施验收推断。

## 已验证

| 范围 | 结果 |
|---|---|
| Python 3.10.12 | `uv sync --frozen --extra dev` 成功；195 项 pytest 全通过（真实 PostgreSQL 集成项未跳过） |
| Python 3.13.15 容器 | 更新后的 API 镜像安装锁定 dev 依赖后，同一套 195 项 pytest 全通过；maintenance 入口运行成功 |
| PostgreSQL 18 | 独立 loopback 临时容器；0002 migration 结构检查通过；自动测试验证回退 0001/重升、保留已有管理员认证数据及旧版本 readiness 拒绝 |
| 静态检查 | `ruff check src tests alembic`、`mypy src` 通过 |
| Operation / Outbox | 原子落库/回滚、永久身份去重、并发创建、SKIP LOCKED、topic 隔离、续租、代次与过期围栏、unknown 不重发、证据审计、数据库约束 |
| 并发缺陷回归 | 三项持有旧 ORM 引用的双会话测试；登录限流 UPSERT 与维护删除确定性交错；任一登录维度耗尽时仍消耗另一维度额度 |
| JSONB | 在真实 PG 复现指数数字从小 JSON 膨胀为大整数；新入库边界拒绝异常载荷，后续正常消息可领取 |
| 幂等入口 | 六个写入口必填头、非法/重复头拒绝、OpenAPI、可信 actor/key/reason 传入 Hook；过期不完整回执不会触发重执行 |
| 维护 | 只读事务预览、有界删除、保留期、保护非 sent 消息与操作/审计、锁跳过、回滚、SQL/JSON null 回执保护、实际 CLI entrypoint |
| Vue 前端 | 类型检查、60/60 单元测试、生产构建通过 |
| 浏览器 | Playwright Chromium 2/2 通过：真实用户/管理员 HttpOnly Cookie、刷新恢复、CSRF 拒绝、退出、501 提示、390px 无横向溢出 |
| 镜像与配置 | API/Web 镜像构建成功；提供 Compose 中的 api 主机名后 Nginx `-t` 通过；`docker compose config --quiet` 通过 |
| CI | Python 3.10/3.13、PG 迁移与回退、pytest、维护 CLI、前端、Chromium job 已配置；未推送，未在 GitHub 执行 |

后端保留一条现有 Starlette TestClient/httpx 弃用警告，不影响上述测试结果。Nginx 单独容器首次检查因为缺少 `api` DNS 失败；在检查容器提供该 upstream 名称后通过，无需改动真实配置。

## 本轮提交拆分

- `832bc0e`：Operation/Outbox 模型、迁移、持久化原语与集成测试。
- `fade05d`：业务写请求幂等键契约、Hook 参数传递、待查证回执保护。
- `f802a50`：默认预览、有界删除的维护命令。
- `9e407d4`：锁定查询刷新 ORM 对象，阻止旧 Worker 覆盖状态。
- `4df5352`：阻止 JSONB 数值展开导致队列消息无法领取。
- `41452fa`：登录窗口原子 UPSERT，消除维护并发删除间隙。

迁移数据保留测试、CI 和部署文档随后单独提交。本轮没有 push。

## 测试边界

集成测试会清空明确命名的 `inviteflow_test` 数据库；迁移测试也会删除/重建新增业务基础表，只能在独立测试库运行。浏览器管理员使用专用一次性测试凭据，不涉及生产账号。

镜像构建、容器内测试和配置检查不等于实际生产 Compose 部署、TLS 验收、安全审计或备份恢复演练。没有测试真实第三方 Provider、奖励到账、多浏览器矩阵或像素级视觉回归。

`business_hooks_enabled=true` 只允许已安装的 Hook Registry 执行，当前仓库没有真实业务实现。已有入口角色鉴权不等于 Claim 对象所有权鉴权。接入业务仍需账本、状态机、对象授权、资源租约/IP gate、消费/重试预算、持久 Worker/传输适配器，以及基于真实证据的 Provider 对账。
