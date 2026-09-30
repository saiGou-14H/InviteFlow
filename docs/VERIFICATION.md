# 本轮验收记录

验证日期：2026-09-30。所有真实业务 Hook 仍为占位实现，业务成功不能由下面的基础设施验收推断。

## 已验证

| 范围 | 结果 |
|---|---|
| Python 3.10.12 虚拟环境 | `.venv` 创建成功，`uv sync --frozen --extra dev` 成功 |
| PostgreSQL 18 | 独立 loopback Docker 容器；Alembic upgrade → check → downgrade → upgrade 成功 |
| Python 后端 | 79 passed；覆盖迁移、模型约束、配置、安全、会话、管理员边界、限流、CSRF、幂等和 Hook 501 |
| Ruff / mypy | `ruff check src tests` 通过；`mypy src` 通过 |
| Python 3.13 容器 | API 镜像运行完整 79 项测试通过 |
| Vue 前端 | `npm ci`、`npm run typecheck`、`npm test`（60/60）、`npm run build` 通过 |
| 浏览器 | Playwright Chromium 2/2 通过：真实 HttpOnly Cookie、刷新恢复、CSRF 拒绝、用户/管理员退出、501 提示、390px 无横向溢出 |
| 容器 | API/Web 镜像构建成功；Nginx `-t` 成功；`docker compose config --quiet` 成功 |
| CI 文件 | 已加入 Python 3.10/3.13、PostgreSQL、前端和浏览器 job；未推送，未声称远端 CI 已执行 |

## 提交拆分

- `2802a0f feat: add PostgreSQL persistence and migration foundation`
- `7c58309 feat: add persistent user and admin sessions with CSRF protection`
- `3a0471e feat: add transactional database idempotency executor`
- `056d00a feat: add two-role Vue frontend and real browser session tests`

部署与文档收尾变更随后单独提交。分支没有推送，远端为空/已消失状态保持不变。

## 测试边界

集成测试会清空明确命名的 `inviteflow_test` 数据库；本地和 CI 使用一次性测试凭据。浏览器管理员测试需要专门测试账号，不能使用生产账号。没有做公网 TLS、真实 Provider、真实第三方邀请协议、多浏览器矩阵、像素级视觉回归或生产恢复演练。

`business_hooks_enabled=true` 只允许已安装的 Hook Registry 执行，当前仓库没有真实业务实现；用户/管理员入口角色鉴权也不等于未来 Claim 对象所有权鉴权。接入业务前仍需完成账本、状态机、Outbox/Worker、未知结果查证和对象授权。
