# InviteFlow Hook 契约

本文档说明当前骨架已经预留的业务边界。当前没有接入真实 CDK、邮箱、邀请平台或奖励接口；未实现 Hook 统一返回 `501 HOOK_NOT_IMPLEMENTED`。

## 两角色边界

产品角色只允许 `user`（用户）与 `admin`（管理员），定义在 `src/inviteflow/domain/roles.py`。用户通过自己的会话访问领取任务，管理员通过管理入口处理运营和售后。未登录浏览是用户访问状态，Worker、Scheduler、Provider 都是内部组件。

用户匿名会话、管理员认证、入口角色鉴权和 CSRF/Origin 已实现，详见 `AUTHENTICATION.md`。Hook 参数中的 `actor_id` 现由可信服务端会话提供，已移除固定身份字符串。真实业务的 Claim/CDK 对象归属与预算授权仍须在 Hook/应用服务中实现，不能只凭入口角色授权。

经销商专属功能和预算已取消；原有用户与管理员的业务权限和次数规则保持独立。

## Hook 分层

```text
ClaimWorkflow
├── CdkHook       CDK 校验、预约、消耗、释放、吊销
├── ResourceHook  邮箱资源领取、健康检查、清理、释放
├── ClaimHook     批量领取、状态快照、确认、用户重试、用户补发
├── AdminHook     管理员发卡、任务对账和运营动作
└── InvitationProvider
                 外部邀请探测、执行、结果查证
```

## 现有 Python 接口

接口定义位于 `src/inviteflow/domain/hooks.py`。

所有通过 HTTP 暴露的 ClaimHook/AdminHook 写方法新增必填关键字参数 `idempotency_key`；`get_snapshot` 为只读，不接收该参数。管理员 `reconcile_claim` 还接收必填 `reason`，用于后续审计。真实实现应先校验对象归属，再在 IdempotencyExecutor 提供的事务中更新业务状态并创建 Operation/Outbox。原始幂等键不得落库或写日志；仅校验请求头并不等于已实现幂等。OpenAPI 公开写请求的必填 `Idempotency-Key` 契约，默认占位 Hook 继续返回 501。

`OUTBOX.md` 描述已经实现的持久化原语与围栏；Worker 调度循环、真实消息传输和 Provider 仍待接入。

### `CdkHook`

- `validate(code)`：校验 CDK 并返回安全摘要。
- `reserve(cdk_id, claim_id)`：为领取任务预约一次权益。
- `consume(cdk_id, claim_id)`：首次交付完成后消耗预约。
- `release(cdk_id, claim_id, reason=...)`：明确未交付时释放预约。
- `revoke(cdk_id, actor_id=..., reason=...)`：吊销卡密，保留历史流水。

### `ResourceHook`

- `acquire(claim_id)`：取得一个可用邮箱资源并绑定领取任务。
- `health_check(resource_id)`：检查资源是否适合继续使用。
- `release(resource_id, reason=...)`：启动资源释放流程。
- `cleanup(resource_id, generation=...)`：清理并核验资源；结果不明时不能回池。

### `ClaimHook`

- `claim_batch(codes, actor_id=...)`：逐行验证 CDK 并创建领取任务。
- `get_snapshot(claim_id, actor_id=...)`：返回当前调用者可见的安全快照。
- `confirm(claim_id, actor_id=...)`：确认用户已发送邀请并排队处理。
- `retry(claim_id, actor_id=..., reason=...)`：对明确失败的初始任务执行有限重试。
- `follow_up(claim_id, actor_id=...)`：对完成后仍待处理的任务执行有限补发。

### `AdminHook`

管理员 Hook 当前预留发卡和人工对账接口；后续扩展任务处理和运营动作。用户入口使用 `ClaimHook`，管理员入口使用 `AdminHook`。实现时必须由服务端执行角色与对象级授权，用户只能访问自己的任务；前端隐藏按钮不能替代授权。

### `InvitationProvider`

Provider 不直接返回“HTTP 200 即成功”，而是返回：

- `confirmed_success`：有完整证据证明操作完成；
- `confirmed_no_effect`：有证据证明没有产生外部效果；
- `unknown`：超时、断连、Worker 崩溃或证据不足，必须进入查证。

真实 Provider 接入前必须确认邀请探测、执行、完成上报、结果查询、幂等键、限流、资源清理和凭证保留规则。未知结果不能自动重发、退回 CDK 或将邮箱放回资源池。

## API 占位

骨架已提供以下入口：

- `GET /healthz`
- `GET /readyz`
- `GET /api/v1/capabilities`
- `POST /api/v1/claims/batches`
- `GET /api/v1/claims/{claim_id}`
- `POST /api/v1/claims/{claim_id}/confirm`
- `POST /api/v1/claims/{claim_id}/retry`
- `POST /api/v1/claims/{claim_id}/followup`
- `POST /api/v1/admin/cdk-batches`
- `POST /api/v1/admin/claims/{claim_id}/reconcile`

业务端点先验证会话、角色、写请求来源和 CSRF，再验证请求模型并调用对应 Hook；未认证/越权分别拒绝，默认 Hook 返回 `501`。`business_hooks_enabled=false` 强制使用占位 Hook；设为 true 仍需明确安装真实实现。幂等执行基础仅适用于同一数据库事务内的命令，不会自动包裹外部请求或这些尚未实现的 Hook。下一阶段应把业务事务、幂等、PostgreSQL 锁、Outbox 和 Worker 调度放在应用服务中。

## 推荐实现顺序

1. PostgreSQL 模型、Alembic 迁移和 Unit of Work。
2. CDK digest、reservation、consume/release 不可变账本。
3. Resource 租约、generation fencing 和 IP gate。
4. Claim 状态机、幂等命令和十分钟确认窗口。
5. Python Worker、Outbox、未知结果查证和恢复。
6. 用户与管理员权限、对象归属校验及各自的售后预算。
7. 真实 Provider 的脱敏夹具、受控联调和奖励结果观察。
