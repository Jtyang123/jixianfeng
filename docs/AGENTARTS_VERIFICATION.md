# 华为云 AgentArts 联调记录

> 四角色调用、配置诊断和安全合并代码已完成。四个角色均已配置真实端点，覆盖率为 `4/4`；创建、发布与验证步骤见 `docs/AGENTARTS_4_AGENT_SETUP.md`。

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

## AgentArts 决策闭环验证

- 运行编号：`RUN-20261107-125518-B469`
- 四个 Agent 调用模式均为 `agentarts`
- A02 推荐方案：`PLAN-2-SPEED`
- 最终交付推荐方案：`PLAN-2-SPEED`
- `agent_decision.applied=true`
- 说明：AgentArts 只能从已通过确定性硬规则核验的方案中选择，不能绕过安全约束

## 注册 Agent 直接调用验证

- 接口：`POST /api/agents/{agent_id}/invoke`
- 权限：仅系统管理员
- 验证 Agent：`demand_agent`
- 会话：`REGISTRY-INVOKE-002`
- 返回模式：`agentarts`
- SSE 事件数：7
- 安全边界：未激活端点返回 400，云端异常返回 502，非管理员返回 403
- 审计：成功调用写入 `agent_invoked` 审计记录

## AgentArts 调用加速验证

- 运行编号：`RUN-20261107-164556-52C2`
- 端到端耗时：28,848 ms
- AgentArts 模式：四个 Agent 全部为 `agentarts`
- A02 与 A03 调用：均在 16:46:11 发起，均在 16:46:18 返回
- 最终核验：`pass`，评分 100
- AgentArts 方案推荐：已应用
- 优化机制：复用 HTTP 连接、任务完成即停止等待、资源匹配与独立核验云端复核并行

## 完整云端运行指标

评估接口只把以下运行计入 `runtime_evidence.fully_cloud`：四个 Agent 的最终 `agentarts_trace` 全部为 `agentarts`。任何本地回退、未完成或混合模式运行都不会混入该项。

截至 2026-11-07 的验收快照：

- 完整云端运行：6 次
- 成功运行：6 次
- 端到端平均耗时：69,500 ms
- 最新完整云端运行：28,848 ms
- 端到端 P95：97,000 ms
- AgentArts 调用成功率：100%
- 本地回退：0 次

平均值包含优化前的历史慢样本，因此同时保留最新一次耗时。该样本量用于证明真实调用链路可运行，不作为大规模生产性能基准。
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

四个角色工作流已分别部署，并通过 `AGENTARTS_AGENT_ENDPOINTS_JSON` 与 `AGENTARTS_AGENT_KEYS_JSON` 管理端点和各自入站 Key。重新部署时应复用相同输入输出契约，并在管理页执行一次直接调用测试后再切换版本。

## 安全说明

- 不在前端暴露 AgentArts API Key
- 不把 `.env` 打包进提交包
- AgentArts 返回内容只作为解释和研判产物，确定性规则仍保留最终否决权
