# 数据库命令幂等基础

`src/inviteflow/idempotency.py` 提供 `IdempotencyExecutor` 与 `CommandResult`。

- scope 必须包含可信 actor 和业务命令，不接受客户端自由指定权限范围。
- key 使用 8–128 位 ASCII 字母/数字/点/下划线/冒号/连字符；数据库只存 HMAC 摘要。
- 请求使用稳定排序 JSON 计算 HMAC 指纹，不保存 CDK 等请求原文。
- PostgreSQL transaction advisory lock 串行保护同 scope+key；另有唯一约束。并发在途返回 409 IDEMPOTENCY_IN_PROGRESS。
- 同 key 同内容返回已提交安全回执；不同内容返回 409 IDEMPOTENCY_CONFLICT。不同 actor 的 scope 独立。
- 命令收到同一个 AsyncSession，数据变化与回执在同一个事务提交；异常一起回滚。
- 只缓存 2xx 回执，最大 64 KiB；默认保留 24 小时。超过保留期重用 key 会被视为新命令，业务层还必须使用操作 ID/状态约束防止业务重复。

## 必须遵守

1. 在每次请求调用执行器**之前**检查角色、对象归属和当前授权，重放不能绕过鉴权。
2. command 必须使用收到的 db，不得自行 commit/rollback、创建独立事务或调用外部 Provider。
3. 回执由调用方提供脱敏投影；禁止存密码、Cookie、完整 CDK、外部凭证或原始邮件。
4. 外部动作需要在该事务中只写 Operation/Outbox，交由 Worker 后续执行；本模块不提供外部服务 exactly-once 保证。
5. 六个业务写入口强制要求且仅允许一个 `Idempotency-Key` 请求头，OpenAPI 声明为必填；缺失为 422，格式非法/重复为 400。通过认证、CSRF 与模型验证后，将有效 key 作为关键字参数传给 ClaimHook/AdminHook。读取快照与会话接口无需此头。
6. Hook 实现必须将执行器接入同一数据库事务；入口校验与传参本身不提供业务去重，也不会自动包裹任意外部请求。业务仍默认 501，不能宣称所有业务端点已完成幂等。
7. 过期且回执不完整（缺响应状态、响应体为空或不是 JSON 对象）仍拒绝执行并保留待查证。只有完整回执才允许按 TTL 淘汰；长期业务重复防护可使用 `create_operation` 的永久身份约束，详见 `OUTBOX.md`。

## 验证

真实 PostgreSQL 测试覆盖同键重放、载荷冲突、独立 actor scope、同时在途请求拒绝、命令异常后业务写入与回执一起回滚、请求原文不进入回执。见 `tests/test_idempotency.py`。
