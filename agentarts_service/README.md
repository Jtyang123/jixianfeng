# FarmMatch AgentArts Runtime

该目录用于把四个 FarmMatch Agent 部署到华为云 AgentArts。

## 1. 安装

```bash
pip install -r requirements.txt
```

## 2. 配置模型

```bash
set MODEL_BASE_URL=https://你的模型服务/v1
set MODEL_API_KEY=你的密钥
set MODEL_NAME=模型名称
```

## 3. 本地运行

```bash
python app.py
```

AgentArts SDK 会暴露：

- `POST /invocations`
- `GET /ping`
- `WS /ws`

## 4. 部署到 AgentArts

按官方 SDK 使用 `agentarts` CLI/平台部署，并在部署后把服务地址配置到 FastAPI：

```env
AGENTARTS_ENABLED=true
AGENTARTS_BASE_URL=https://你的AgentArts运行地址
AGENTARTS_API_KEY=华为云鉴权值
AGENTARTS_INVOKE_PATH=/invocations
```

也可以为四个角色分别配置端点：

```json
{
  "demand_agent": "https://.../demand/invocations",
  "matching_agent": "https://.../matching/invocations",
  "verifier_agent": "https://.../verifier/invocations",
  "delivery_agent": "https://.../delivery/invocations"
}
```

放入 `AGENTARTS_AGENT_ENDPOINTS_JSON`。

> 未配置 AgentArts 时，FarmMatch FastAPI 仍以本地确定性模式运行，不会中断演示。
