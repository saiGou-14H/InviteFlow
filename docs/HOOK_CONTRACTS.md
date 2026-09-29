# InviteFlow Hook 契约

本文档说明当前骨架已经预留的业务边界。当前没有接入真实 CDK、邮箱、邀请平台或奖励接口；未实现 Hook 统一返回 `501 HOOK_NOT_IMPLEMENTED`。

## Hook 分层

```text
ClaimWorkflow
├── CdkHook       CDK 校验、预约、消耗、释放、吊销
├── ResourceHook  邮箱资源领取、健康检查、清理、释放
├── ClaimHook     批量领取、状态快照、确认、用户重试、用户补发
├── AdminHook     管理员发卡、任务对账和运营动作
├── DealerHook    经销商查卡和受限重发
└── InvitationProvider
                 外部邀请探测、执行、结果查证
```

## 现有 Python 接口

接口定义位于 `src/inviteflow/domain/hooks.py`。

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

### `AdminHook` 与 `DealerHook`

管理员 Hook 负责发卡和人工对账；经销商 Hook 负责所属卡查询和按资源预算限制的重发。二者必须由服务端进行角色与对象级授权，前端隐藏按钮不能替代授权。

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
- `POST /api/v1/dealer/cdks/lookup`
- `POST /api/v1/dealer/claims/{claim_id}/probe-resend`

业务端点当前只验证请求模型，然后调用对应 Hook；默认 Hook 会返回 `501`。下一阶段实现时，API 路由保持薄层，事务、幂等、PostgreSQL 锁、Outbox 和 Worker 调度放在应用服务中。

## 推荐实现顺序

1. PostgreSQL 模型、Alembic 迁移和 Unit of Work。
2. CDK digest、reservation、consume/release 不可变账本。
3. Resource 租约、generation fencing 和 IP gate。
4. Claim 状态机、幂等命令和十分钟确认窗口。
5. Python Worker、Outbox、未知结果查证和恢复。
6. 管理员、经销商权限与售后预算。
7. 真实 Provider 的脱敏夹具、受控联调和奖励结果观察。
