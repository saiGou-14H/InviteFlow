# Operation / Outbox 持久化基础

迁移 `0002_operations_outbox` 在原认证基础上增加 operations 与 outbox_messages；readiness 要求新版本。应用启动不自动执行迁移。

## 可用原语

`src/inviteflow/persistence/outbox.py` 提供数据库内的通用能力，所有函数都要求调用方显式开启 AsyncSession 事务：

- `create_operation`：Operation 与初始 Outbox 原子落库；使用服务器派生的 scope/key digest/request digest。相同身份和请求返回原 Operation，不会因幂等回执/已发送通知被清理而重新入队；内容冲突拒绝。并发未提交的同键请求抛 OperationBusyError。
- `lease_outbox`：只领取明确 topics 中、已到 available_at 的 pending 消息，使用 FOR UPDATE SKIP LOCKED；每次最多 100 条。租约提交后才能调用传输层，不能把网络操作放在数据库事务内。
- `renew_outbox_lease`、`mark_outbox_sent`、`mark_outbox_unknown`：检查 worker_id、generation、processing 状态与数据库实时时钟下的租约有效期；过期或旧 Worker 一律拒绝回写。
- `quarantine_expired_leases`：每表最多 100 条，将过期 running Operation / processing Outbox 转成 unknown，并递增 generation。**不重新入队、不自动重发**。
- `transition_operation`：检查期望状态和 generation；开始运行增加 attempts 并建立租约。failed 专指已经证实无效果，不用于随意捕获异常。succeeded/cancelled 不可重新运行；unknown 禁止直接回到 running。
- `resolve_unknown_operation`：仅供受信任、已完成权限和证据审核的内部对账服务使用，记录管理员及持久证据 UUID 后解析为已确认成功/无效果；不重新排队。函数本身不能认证外部证据，目前没有 HTTP 管理接口暴露它。

## 必须遵守的接入约束

1. 在每次命令和重放之前验证可信 actor、角色、资源归属；scope 必须绑定 actor 与命令，request digest 必须覆盖完整命令及通知内容。库不接受客户端自报角色作为授权依据。
2. 原始 CDK、Cookie、密码、邮件、Provider 凭据不得放入 Outbox/回执；只存脱敏投影或持久对象引用。JSON 深拷贝且限 64 KiB，但大小校验不等于内容脱敏。
3. sent 只代表传输层明确确认接收，**不代表邀请、奖励或 Operation 业务成功**。消费者仍须按 operation/message ID 幂等去重，不能把该实现称为跨系统 exactly-once。
4. 请求超时或进程在外部动作后崩溃时，后续只能查证；禁止把租约过期当作“什么都没发生”。
5. 当前没有真实传输适配器、后台循环、消息中间件、Claim 账本或业务 Handler。没有自动调度这些函数；真实业务 Hook 仍返回 501。

## 验证

真实 PostgreSQL 测试覆盖：原子提交/回滚、永久幂等身份、并发创建冲突、SKIP LOCKED 不重复领取、topic 隔离、续租、所有者/代次/有效期围栏、过期隔离、未知不重发、不可逆终态、证据引用审计和数据库约束。`tests/test_outbox.py` 还验证 Outbox sent 不改变 Operation 为成功。

迁移已验证 upgrade / alembic check / downgrade 0001_foundation / re-upgrade，旧版本回退会移除新表，必须先停止相关组件并备份数据，不可随意在有待处理操作的生产环境回退。
