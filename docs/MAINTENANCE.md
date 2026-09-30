# 手动维护与外部调度接入

维护入口为 `inviteflow-maintenance`（亦可用 `python -m inviteflow.maintenance_cli`）。
**项目没有后台清理调度器，也不会随 API 启动自动清理。** 运维人员确认保留政策后，
可以自行将命令接入外部 cron / systemd timer / 作业平台；该接入需要单独部署和验证。
本命令不建库、不迁移，也不循环清空表，每次只执行一个有界批次。

## 安全运行

先在隔离、已迁移至当前版本的 PostgreSQL 数据库验证，再由获授权的运维人员
在目标环境配置 `INVITEFLOW_DATABASE_URL`。不要将含凭据的 DSN 放进命令行、工单或日志。
使用项目安装环境中的入口：

```sh
# 默认 dry-run；最多统计每张表 100 个符合条件的记录
.venv/bin/inviteflow-maintenance --purge-expired

# 显式 dry-run：同样不会删除
.venv/bin/inviteflow-maintenance --purge-expired --dry-run --limit 100

# 只有完整、显式 --apply 才删除；不接受 --app 等缩写
.venv/bin/inviteflow-maintenance --purge-expired --apply --limit 100
```

`--limit` 是**每表**的上限，必须为 1..1000 的整数，默认 100；五张可清理表
合计最多删除 `5 × limit` 行。`--apply` 和 `--dry-run` 互斥。
存在积压时由外部调度按运行负载安排后续批次，不要假定单次执行已经清空历史记录。

成功时 stdout 只有一个 JSON 对象，各值均为计数：

```json
{"idempotency_requests": 0, "login_rate_limits": 0, "public_sessions": 0, "sent_outbox_messages": 0, "staff_sessions": 0}
```

模式由启动参数确定，不额外输出记录 ID、cookie、令牌、回执内容、消息 payload 或 DSN。
退出码 0 表示完成；参数、配置、连接或数据库执行错误返回 2。执行异常只报告固定安全
提示，不打印异常内容或堆栈；检查环境与迁移版本时也不要把密钥写入排障记录。

## 删除资格与保留期

| 数据 | 删除条件 |
| --- | --- |
| public_sessions / staff_sessions | `expires_at` 或 `revoked_at` 已经过会话清理保留期；刚过期或刚撤销的会话仍保留 |
| idempotency_requests | 已过期，`response_status` 非空，`response_body` 为 JSON 对象；普通事务可见性仅看到其他事务已提交的数据 |
| login_rate_limits | 窗口起点已超过 `max(login_window_seconds, 3600)` 秒 |
| outbox_messages | 仅 `status=sent` 且 `sent_at` 已经过 outbox 清理保留期 |

时间比较为 `<= cutoff`，恰好达到保留期边界可清理。一次运行使用同一个 UTC 时间点。
回执响应状态缺失、SQL NULL / JSON null 响应体、未提交或被其他事务锁住的回执
都不由 apply 删除；完整回执包括空 JSON 对象 `{}`。不完整回执须另行人工查证。

配置项：

- `INVITEFLOW_SESSION_CLEANUP_RETENTION_SECONDS`：默认 86400（1 天）。
- `INVITEFLOW_OUTBOX_CLEANUP_RETENTION_SECONDS`：默认 604800（7 天）。
- 上述保留期都允许 60..31536000 秒。
- 登录限流清理至少保留 3600 秒，覆盖配置允许的最大登录窗口，避免当前进程缩小
  窗口后提前删除另一个进程仍在使用的窗口。低层 API 若收到更大窗口则按更大值保留。

**永不清理** pending / processing / unknown / failed outbox、operations、audit_logs、
账号以及仍有效且未达到撤销保留期的会话。即使消息很旧，非 sent 状态仍保留。
业务记录、审计保留政策和待查证状态不属于本工具处理范围。

## 事务与并发

- apply 按每表主键有序选取有限行，并使用 `FOR UPDATE SKIP LOCKED`；随后只按
  选中的主键删除。锁持有到事务结束，避免维护进程等待其他进程持有的候选行锁。
- 五表删除在同一事务中提交；失败由 session 关闭回滚，不会提交部分清理。
- SKIP LOCKED 不承诺绕过 DDL / 表级锁；维护窗口仍应避开迁移和人工重型 DDL。
- dry-run 不加行锁、不 autoflush、不执行 DELETE / INSERT / UPDATE；CLI 另行设置
  PostgreSQL `SET TRANSACTION READ ONLY`，计数后回滚。
- dry-run 计数是有界候选快照，不是全表总数，也不是删除承诺；并发修改或 apply
  跳过被锁定记录时，实际删除数可能不同。
- 低层 `purge_expired` API 同样默认 dry-run；调用方必须使用专用、没有业务写入的
  session / transaction，显式传 `dry_run=False` 才删除，并自行管理 commit / rollback。

## 验证

无需数据库的单测：

```sh
.venv/bin/pytest tests/test_maintenance.py -m 'not integration'
.venv/bin/ruff check src/inviteflow/maintenance.py src/inviteflow/maintenance_cli.py tests/test_maintenance.py
.venv/bin/mypy --follow-imports=silent src/inviteflow/maintenance.py src/inviteflow/maintenance_cli.py
```

真实 PG 集成测试复用 `tests/test_auth_integration.py` 的 `harness`，包括保留边界、
不完整回执、受保护表、批次上限、事务回滚、read-only/no-autoflush、锁跳过和未提交回执。
**该 fixture 会清空测试数据**，只能由负责人提供并迁移独立测试库，然后设置
`INVITEFLOW_TEST_DATABASE_URL` 再运行：

```sh
.venv/bin/pytest tests/test_maintenance.py -m integration
```

未配置测试 URL 时集成测试跳过；跳过不代表真实 PG 已验证。不要为运行测试
自行创建数据库、使用生产 URL 或解除共享 fixture 的数据库安全检查。
