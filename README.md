# InviteFlow（邀程）

InviteFlow 是一个围绕邀请凭证、邮箱资源、异步任务和售后操作构建的平台骨架。

当前版本只完成 Python 后端工程初始化和业务 Hook 接口，**没有实现真实 CDK、邮箱、邀请、奖励或第三方平台操作**。所有尚未接入的业务入口会返回 `501 HOOK_NOT_IMPLEMENTED`，避免把未确认的上游协议写死。

## 当前技术基线

- Python 3.10+
- FastAPI + Pydantic v2
- Uvicorn
- 预留 SQLAlchemy/asyncpg/Alembic 持久化扩展
- 预留 HTTPX 外部 Provider 扩展
- 前端和数据库尚未接入

## 本地运行

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
inviteflow-api
```

默认监听 `127.0.0.1:8000`。

也可以直接运行：

```bash
uvicorn inviteflow.app:create_app --factory --reload
```

健康检查：

```bash
curl http://127.0.0.1:8000/healthz
curl http://127.0.0.1:8000/readyz
curl http://127.0.0.1:8000/api/v1/capabilities
```

## Hook 设计

后续业务实现从以下接口接入：

- `CdkHook`：CDK 校验、预约、消耗、释放和吊销
- `ResourceHook`：邮箱资源领取、清理、健康探测和隔离
- `ClaimHook`：领取批次、确认、重试、补发和状态查询
- `AdminHook`：管理员发卡、任务处理、批量售后和系统操作
- `DealerHook`：经销商 CDK 查询、吊销和受限重发
- `InvitationProvider`：外部邀请平台的探测、处理、查证和资源释放

Hook 接口只表达业务边界，不假设真实上游的 API、账号格式、邮件供应商或奖励规则。实现后应把 PostgreSQL 事务、Outbox、Worker、幂等键和结果不明处理放在服务层，不要在 FastAPI 路由中直接发起长时间外部请求。

## 目录

```text
src/inviteflow/
├── api/          FastAPI 路由、请求模型和依赖
├── domain/       领域模型、Hook 协议和业务错误
├── providers/    外部 Provider 协议
└── workers/      Worker 执行 Hook 的扩展点
```

详细功能设计见：

- [`reports/CODEX_INVITATION_DEVELOPMENT_DESIGN.zh-CN.md`](../reports/CODEX_INVITATION_DEVELOPMENT_DESIGN.zh-CN.md)

## 开发约束

1. 尚未核验的真实业务规则必须通过接口和配置隔离。
2. 外部请求超时不得直接当作失败；需要支持 `unknown` 和后续查证。
3. 业务状态、权益和预算最终以 PostgreSQL 为准；Redis 只做辅助能力。
4. 每完成一个独立功能并通过验证后，按功能创建本地 Git 提交；不自动推送。
