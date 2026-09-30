# InviteFlow 前端

Vue 3 + TypeScript strict + Vite。仅提供用户 `/` 与管理员 `/admin` 两个入口，无经销商角色。中文默认，可切换真实英文界面标签、提示与错误文案。

> **基础能力已接入 · 业务处理中接口待实现**
> 这枚徽标描述前端集成范围，不保证当前后端/数据库可用。服务状态区域分别检查存活、数据库/Schema 就绪与能力接口。业务 Hook 返回 501 时明确显示「未接入业务 Hook」，不生成成功卡片、邀请码、邀请资源或虚构统计。

## 环境与启动

- Node.js **24.x**（此目录实际验证环境：Node **v24.18.0**，npm **11.16.0**）。`package.json` 的 engine 为 `^24.0.0`。
- 依赖版本固定，提交物包含 `package-lock.json`。TypeScript 固定 **5.9.3**：`vue-tsc` 3.3.11 在本机配合 TypeScript 7.0.2 会出现 `ERR_PACKAGE_PATH_NOT_EXPORTED`，因此不使用该组合。
- 所有依赖、缓存、构建输出均位于此目录。无 CDN、外部字体、遥测、Service Worker 或全局安装要求。

```sh
cd /root/dsh/InviteFlow/frontend
npm ci --cache .npm-cache
npm run typecheck
npm test
npm run build
# 开发时由操作者按需运行；不会作为安装/测试的一部分自动启动
npm run dev
```

开发入口固定为 `http://127.0.0.1:5173`，绑定 loopback 且 `strictPort: true`，避免端口自动变化导致 Origin 不一致。Vite 将 `/api/v1`、`/healthz`、`/readyz` 原路径代理至 `http://127.0.0.1:8000`，不改写浏览器 Origin。不要混用 `localhost` 与 `127.0.0.1`。

### 后端本地开发配置（不是 Vite 环境变量）

后端默认 `public_origin` 是 `http://127.0.0.1:8000`；从 Vite 页面发出的 POST Origin 为 `http://127.0.0.1:5173`。必须在**后端进程**启动配置中设置：

```sh
INVITEFLOW_PUBLIC_ORIGIN=http://127.0.0.1:5173
INVITEFLOW_COOKIE_SECURE=false
```

`cookie_secure=false` **仅用于本地 HTTP 开发**。生产必须使用 HTTPS、`INVITEFLOW_COOKIE_SECURE=true`，并将 `INVITEFLOW_PUBLIC_ORIGIN` 设为用户访问的 HTTPS 站点 origin（不带路径）。前端不伪造 `Origin`、不读取 HttpOnly cookie，不在源码写入账户、密码、数据库 URL 或 session secret。

后端还需要自己的数据库、迁移、会话密钥与管理员账户配置，依照后端既有说明操作；本前端不自动配置这些内容。未配置数据库或 Schema 时 `/readyz` 的 503 显示为「未就绪」，不当作健康；`/healthz` 成功不等于业务或数据库已可用。

## 接口契约

所有 API 请求使用浏览器同源地址 `/api/v1` 与 `credentials: 'same-origin'`，不提供跨域 API 地址输入。请求不缓存，禁止跟随 API 重定向，默认超时 15 秒，无自动重试。

| 场景 | 请求 | 处理 |
| --- | --- | --- |
| 能力 | `GET /api/v1/capabilities` | 验证 `service/version/supported_roles/implemented/reserved_hooks`；仅显示实际数组数量，不渲染任意字段 |
| 存活 / 就绪 | `GET /healthz`、`GET /readyz` | 分别显示状态，503 显示未就绪；不渲染原始诊断 |
| 用户会话 | `POST /api/v1/public/sessions`，JSON `{}` | 用户明确点击创建；不静默创建 |
| 用户恢复 / 退出 | `GET /api/v1/public/session`、`POST /api/v1/public/logout` | 刷新或进入页面恢复；退出带用户 CSRF |
| 管理员登录 | `POST /api/v1/staff/login`，`{username,password}` | 真正调用服务端；提交结束清空密码，不保存凭据 |
| 管理员恢复 / 退出 | `GET /api/v1/staff/session`、`POST /api/v1/staff/logout` | 刷新或进入页面恢复；退出带管理员 CSRF |
| 用户 CDK | `POST /api/v1/claims/batches`，`{codes}` | 行首尾 trim、忽略空行、去重、保留大小写与首次出现顺序；1–100 条 |
| 自己的申请 | `GET /api/v1/claims/{uuid}` | 验证 UUID，只投影白名单状态，不显示其他响应字段；归属由后端判定 |
| 管理员批次 | `POST /api/v1/admin/cdk-batches` | `{quantity,max_uses,expires_at}`；前两者 1–1000 整数；可选本地到期时间转 UTC，空值为 null |
| 管理员对账 | `POST /api/v1/admin/claims/{uuid}/reconcile` | `{reason}`，trim 后 1–500 字符 |

会话响应严格验证 `{status:'ok',data:{role,actor_id,csrf_token}}`。用户与管理员 CSRF 分开保存在 API client 内存中，展示层仅接收 `role/actor_id`，不接收 CSRF。登录和创建会话不要求 CSRF；其余 POST 缺少对应角色令牌会在发送前拒绝。业务命令带 `crypto.randomUUID()` 生成的 `Idempotency-Key`，登录/创建会话/退出不带业务幂等键。浏览器需要现代安全上下文（loopback 开发地址或生产 HTTPS）。

401 清理对应角色的内存会话；不清理另一角色。403 显示静态权限/Origin/令牌提示，可手动重新读取会话。不把网络故障当作登录失败。退出请求失败不会声称服务端 cookie 已删除。并发恢复使用代次保护，旧 401 不会清掉较新的会话。

## 数据与未完成业务的边界

- **没有 localStorage/sessionStorage 写入**：CSRF、邀请码、密码、Claim UUID 与业务结果只在内存。邀请码可以手工清空；退出或会话失效会清空该用户表单，切换路由卸载组件也会丢弃表单内容。语言选择同样不持久化。
- 不展示后端任意 `error.message`、`error.hook`、HTML 或原始 JSON。根据 HTTP 状态映射固定双语错误文案；后端未知字段不展示。
- 501 永远是「未接入业务 Hook」而非成功。未来若收到 **202**，仅显示“已接收，不代表业务完成”，并可展示经过 UUID 校验的 `data.operation_id`。没有自动轮询或业务完成推断。
- Claim 状态只识别 `pending/queued/processing/awaiting_confirmation/confirmed/completed/failed/cancelled/expired`；缺失或未知状态显示“未提供可识别状态”。这不是状态机，也不会推断可操作权限。
- 确认、重试、跟进是**明确禁用的占位按钮**。即使能力列表未来变化，也需要先实现并验证对应状态/操作契约，才应启用；目前不发送这些业务请求。
- 界面分区和按钮禁用只改善使用体验，**不是后端鉴权**。服务端必须独立验证 user/admin、Claim 所有权、CSRF、Origin 和幂等规则。
- 每次新的手工业务提交生成新幂等键；不自动重试未知结果。超时后先查询已有 Claim 或联系管理员核查，不要盲目重复提交。

## 构建与部署

`npm run build` 先执行 strict 类型检查，再生成 `dist/` 静态文件。生产需要由已有 Web 服务提供 `dist/`，对前端路径（例如 `/admin`）配置 SPA fallback 到 `index.html`；`/api/v1/*`、`/healthz`、`/readyz` 必须优先代理到后端，不能被 SPA fallback 吞掉。Vite 开发代理不进入生产包。仓库现提供 `frontend/Dockerfile`、`nginx.conf` 与根目录 `compose.yaml`，配置 SPA fallback 和同源 API 代理；具体启动方式和生产边界见 `../docs/DEPLOYMENT.md`。本轮验证只使用临时本地实例，不代表已部署公网服务。

## 测试说明与限制

`npm test` 运行 Vitest + happy-dom：

- API：同源 credentials、Origin 不伪造、内存 CSRF 与角色隔离、恢复、退出、401、403、503、超时、幂等键、敏感错误隔离、响应验证。
- 工具：多行 CDK 解析、UUID、数量/到期时间、状态/操作 ID 的安全投影。
- 组件：用户创建/退出会话与 CDK 表单；管理员实际登录/退出表单、发卡/对账参数；501 无成功提示、202 有限确认、未知状态、禁用占位、双语切换与健康状态。

Vitest 的 60 项是**受控 fetch 响应的单元/组件测试**。另有 `e2e/foundation.spec.ts` 的 2 项 Playwright 测试，已在真实本地 API + 独立 PostgreSQL + Chromium 环境通过：Cookie 的 HttpOnly、同源代理、无 CSRF 拒绝、刷新恢复、用户/管理员退出、业务 501 提示，以及 390px 宽度无横向溢出。未声称多浏览器兼容或像素级视觉回归。

先按后端说明启动隔离测试数据库、迁移并创建**仅用于测试**的管理员，再启动 API :8000 和 Vite :5173：

```bash
npx playwright install chromium
export INVITEFLOW_TEST_ADMIN_USERNAME=your-test-admin
read -r -s -p 'Test-only administrator password: ' INVITEFLOW_TEST_ADMIN_PASSWORD
export INVITEFLOW_TEST_ADMIN_PASSWORD
npx playwright test
```

测试配置固定指向 loopback :5173；密码未提供时管理员用例会明确跳过。请勿拿生产凭据或线上数据联调。CI 的 browser job 会创建一次性账号并运行两项测试；CI 配置已提交，但尚未推送触发远端执行。
