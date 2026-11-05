# 网页发布部署说明

## 一、容器化启动

```bash
cp .env.example .env
# 修改 POSTGRES_PASSWORD 和模型配置
docker compose up -d --build
```

访问 `http://服务器IP:8000`。

## 二、华为云 ECS 部署

1. 创建 Ubuntu 22.04/24.04 ECS，开放 80、443、8000 端口。
2. 安装 Docker 与 Docker Compose。
3. 将代码上传到服务器。
4. 配置 `.env`。
5. 执行 `docker compose up -d --build`。
6. 使用 Nginx 反向代理 `127.0.0.1:8000`。
7. 绑定域名并配置 HTTPS 证书。

## 三、生产数据库

Docker Compose 已配置 PostgreSQL 18。正式环境建议使用华为云 RDS for PostgreSQL 或 GaussDB：

```env
DATABASE_URL=postgresql://用户名:密码@数据库地址:5432/farmmatch
```

应用会自动建表并初始化演练数据。生产上线前应关闭演示数据初始化或通过管理员导入真实数据。

## 四、地图

前端默认使用高德兼容瓦片。正式商业部署建议使用已授权的高德/华为地图服务，并配置：

```env
VITE_MAP_PROVIDER=amap
VITE_MAP_TILE_URL=你的授权瓦片地址
VITE_MAP_ATTRIBUTION=地图版权声明
```

## 五、AgentArts

1. 在华为云 AgentArts 创建智能体或工作流。
2. 使用 `agentarts_service/` 部署运行时服务。
3. 将运行地址和鉴权信息写入 `.env`。
4. 调用 `GET /api/agentarts/check` 验证。
5. 运行一次完整推演，确认事件流出现 `agentarts.called` 和 `agentarts.completed`。

## 六、上线前检查

- 修改默认密码
- 配置 HTTPS
- 配置备份
- 配置日志和监控
- 配置 AgentArts/LLM 密钥
- 关闭调试端口
- 确认数据授权和隐私声明
