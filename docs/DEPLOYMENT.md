# 启动与部署基础

这是可运行基础平台的部署配置，**不是业务功能已经上线或生产加固完成的声明**。核心邀请/CDK/资源行为仍返回 501。

## 本地容器启动

准备 Docker Engine 与 Compose v2。在仓库根目录复制 `.env.example` 为 `.env`：

- `INVITEFLOW_POSTGRES_PASSWORD`：自己生成的 URL-safe 随机密码（Compose 拼接 DSN，不接受未编码的 @、: 等字符）。
- `INVITEFLOW_SESSION_SECRET`：另一个独立、至少 32 字符的随机密钥。
- `INVITEFLOW_PUBLIC_ORIGIN=http://127.0.0.1:8088`（不同于 Vite 开发端口 5173）。
- `INVITEFLOW_COOKIE_SECURE=false`、`INVITEFLOW_ENVIRONMENT=development` 仅适用于本地 HTTP。

生成随机值的示例命令，每个用途独立执行一次；不要提交输出：

```bash
python -c 'import secrets; print(secrets.token_urlsafe(48))'
chmod 600 .env
docker compose config --quiet
docker compose up -d --build
docker compose exec api inviteflow-admin create admin
```

Compose 顺序：数据库健康 → 独立 migrate 容器 `alembic upgrade head` 完成 → API readiness 通过 → Web。不会在每个 API 进程启动时抢占执行迁移。

访问 `http://127.0.0.1:8088/` 和 `/admin`。仅 Web 端口绑定宿主 loopback；数据库与 API 不发布宿主端口。PostgreSQL 18 使用命名卷 `postgres_data` 挂载 `/var/lib/postgresql`。默认无管理员账号，必须主动初始化。

```bash
curl -f http://127.0.0.1:8088/healthz
curl -f http://127.0.0.1:8088/readyz
docker compose logs --tail=100 api migrate
docker compose down
```

普通 down 保留数据库卷。**不要对有需保留数据的环境执行 `down --volumes`**；升级前先备份并在独立副本上验证迁移/恢复。

## 生产前必做

1. 在已有可信入口配置 TLS，设置真实 HTTPS `INVITEFLOW_PUBLIC_ORIGIN`、`INVITEFLOW_ENVIRONMENT=production`、`INVITEFLOW_COOKIE_SECURE=true`，不直接发布开发服务器。
2. 为数据库、会话密钥、管理员密码建立独立秘密存储、备份及轮换策略。仓库中 CI/test 字符串只供一次性测试，不得复制为生产凭据。
3. 当前 Uvicorn 显式不信任代理头；反代下来源登录限流可能共享代理地址。上线前配置严格可信代理边界与边缘 IP 限流，不能简单信任任意 X-Forwarded-For。
4. 补足会话/限流/幂等旧记录清理、审计归档、监控告警、MFA 评估，以及数据库最小权限和备份恢复演练。
5. 在独立版本中实现并测试对象归属、CDK 账本、资源租约、业务状态机、Outbox/Worker 与未知结果查证，再接入真实 Provider。
6. 镜像使用版本标签而非内容 digest；生产发布应锁定经过审核的 digest 并执行依赖/镜像漏洞扫描。

## 验证与 CI

GitHub Actions 配置：Python 3.10/3.13 + PostgreSQL 18 的迁移升级/结构差异/回退/重升、Ruff、mypy、pytest；Node 24 前端类型/单元测试/构建；独立真实 PostgreSQL + Chromium 浏览器会话验证。

当前只完成本地验证；没有推送仓库，**不声称远端 GitHub Actions 已运行**。本地 Docker 构建和配置检查也不等同于生产部署、TLS 验收或安全审计。
