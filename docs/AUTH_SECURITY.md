# 急先锋登录、权限与审计

## 访问方式

- 本地地址：`http://127.0.0.1:8010`
- 初始管理员：`admin`
- 初始密码文件：`data/bootstrap-admin-password.txt`
- 首次登录后请通过右上角钥匙按钮修改密码，并删除初始密码文件。

## 角色权限

| 角色 | 读取业务数据 | 维护资源/创建事件/启动调度 | 管理账号 | 查看审计 |
|---|---:|---:|---:|---:|
| viewer 只读观察员 | 是 | 否 | 否 | 否 |
| dispatcher 调度员 | 是 | 是 | 否 | 否 |
| verifier 核验员 | 是 | 否 | 否 | 否 |
| admin 系统管理员 | 是 | 是 | 是 | 是 |

## 安全机制

- 密码使用 PBKDF2-SHA256 加盐哈希保存。
- 登录会话使用 HttpOnly、SameSite=Lax Cookie。
- 登录失败连续 5 次后锁定 15 分钟。
- 修改密码后撤销该账号所有已登录会话。
- 写操作自动写入审计日志。
- 启用登录后，外部事件/GPS 接口必须配置 `INTEGRATION_API_KEY`。
- 响应包含 `X-Frame-Options`、`X-Content-Type-Options`、`Referrer-Policy` 和 `Permissions-Policy`。

## 凭据管理约定

- 仓库内**不出现任何明文密码**：`.env.example` 的 `AUTH_BOOTSTRAP_PASSWORD` 留空，`.env` 与 `frontend/.env` 均被 `.gitignore` 覆盖。
- 首次启动时若密码为空，后端随机生成 18 字节口令并写入 `data/bootstrap-admin-password.txt`，登录后应立即改密并删除该文件。
- 登录页**默认不预填任何凭据**。如确需在评审机上一键进入，可在构建前于 `frontend/.env`（已被忽略）设置 `VITE_DEMO_USERNAME` / `VITE_DEMO_PASSWORD`，凭据只会进 `dist` 产物，不进入源码与仓库；提交前请清空。
- 提供提交包时请用专用的演示口令，不要复用公共账号密码；正式部署必须重新设置密码并清空注入项。

## 生产环境要求

```env
AUTH_REQUIRED=true
AUTH_COOKIE_SECURE=true
INTEGRATION_API_KEY=<随机长字符串>
```

公网部署必须使用 HTTPS；否则 `AUTH_COOKIE_SECURE=true` 时浏览器不会发送登录 Cookie。
