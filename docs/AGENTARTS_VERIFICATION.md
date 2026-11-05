# 华为云 AgentArts 联调记录

> 四角色调用、配置诊断和安全合并代码已完成。四个角色均已配置真实端点，覆盖率为 `4/4`；三个待创建工作流的步骤见 `docs/AGENTARTS_4_AGENT_SETUP.md`。

## 当前状态

- 区域：西南-贵阳一（cn-southwest-2）
- 工作流：farmmatch-demand-v1
- 工作流 ID：7edadae1-3eff-4883-be56-f5bdcff36de1
- 版本：A01-demand-agent-v1
- 模型：DeepSeek-V4.1-Flash-1017
- 运行时：agent-arts-9c6e850b769645f5aab81d05dd375a59
- 入站认证：API Key 认证
- 鉴权 Header：Authorization: Bearer
- 调用协议：POST /invocations，返回 SSE 事件流
- 本地适配器：backend/app/agentarts.py
- 密钥管理：仅保存在未提交的 `.env`

## 调用验证

真实请求结果：

```text
HTTP 200
workflow_started
message
workflow_finished
end
```

SSE 解析结果：

- 事件数：7
- 最终输出：`responseContent`
- 返回内容为有效 JSON
- 需求研判结果已写入 `DemandPlan.agentarts_interpretation`
- 本地确定性数量、库存和路线计算未被模型覆盖

## 四智能体端到端验证

- 运行编号：`RUN-20261104-203420-4498`
- 状态：`awaiting_approval`
- 核验轮次：2
- 最终结论：`pass`
- AgentArts 调用：`demand_agent`、`matching_agent`、`verifier_agent`、`delivery_agent` 全部为 `agentarts` 模式
- 四个角色的云端结果均已写入最终产物
- 运行时专用 Key 通过 `AGENTARTS_AGENT_KEYS_JSON` 管理，不在前端暴露


- 运行编号：RUN-20261104-174349-9A1D
- 状态：awaiting_approval
- 核验轮次：2
- 首轮核验：退回
- 第二轮核验：通过
- 核验评分：100
- AgentArts 模式：agentarts
- SSE 事件数：7
- 模型输出长度：2342
- 模型假设数：5

## 双模式边界

- `demand_agent`：已调用真实华为云 AgentArts 工作流
- `matching_agent`：已真实调用 AgentArts
- `verifier_agent`：已真实调用 AgentArts
- `delivery_agent`：已真实调用 AgentArts

后续可复制相同工作流结构，分别创建：

- `farmmatch-matching-v1`
- `farmmatch-verifier-v1`
- `farmmatch-delivery-v1`

然后在 `AGENTARTS_AGENT_ENDPOINTS_JSON` 中增加对应端点。

## 安全说明

- 不在前端暴露 AgentArts API Key
- 不把 `.env` 打包进提交包
- AgentArts 返回内容只作为解释和研判产物，确定性规则仍保留最终否决权
