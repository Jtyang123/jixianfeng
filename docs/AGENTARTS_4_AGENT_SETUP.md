# 急先锋 AgentArts 四智能体接入指南

## 当前状态

- 代码已支持四个角色分别调用 AgentArts。
- 四个角色均已配置真实 AgentArts 运行时端点。
- 当前覆盖率：`4/4`。
- A02/A03/A04 使用运行时专用 API Key，A01 使用全局 Key。
- 未配置的角色自动使用本地确定性执行器，不会中断调度。
- 云端返回只作为解释和补充意见；库存、ETA、成本、载重和核验硬规则仍由本地代码决定。

## 三个待创建工作流

在华为云 AgentArts 中使用现有 `farmmatch-demand-v1` 作为模板，分别创建：

| 角色 | 建议工作流名称 | 系统提示词 |
|---|---|---|
| A02 资源匹配 | `A02-matching-agent-v1` | `docs/agentarts_prompts/farmmatch-matching-v1.system.txt` |
| A03 独立核验 | `A03-verifier-agent-v1` | `docs/agentarts_prompts/farmmatch-verifier-v1.system.txt` |
| A04 方案交付 | `A04-delivery-agent-v1` | `docs/agentarts_prompts/farmmatch-delivery-v1.system.txt` |

每个工作流保留与 `farmmatch-demand-v1` 相同的输入方式：接收 `query` 文本，调用 DeepSeek-V4.1-Flash，最后输出 `responseContent`。不要在工作流中直接查询数据库或修改业务数字。

## 配置端点

每发布一个工作流，复制其完整 `/invocations` 地址，然后运行：

```powershell
cd D:\tentative
.\configure_agentarts.cmd -Agent matching_agent -Endpoint "https://.../invocations"
```

再把 `matching_agent` 换成 `verifier_agent` 或 `delivery_agent`。配置向导会：

- 保留已有端点
- 保留其他环境变量
- 隐藏输入 API Key
- 自动更新 `AGENTARTS_AGENT_ENDPOINTS_JSON`
- 显示四角色覆盖率

配置完成后重启：

```powershell
.\start.cmd
```

打开“智能体中心”，点击“检测 AgentArts”，应显示 `4/4 CONFIGURED`。

## 调用约束

- `matching_agent` 只能解释方案策略和权衡，不能改写规划器返回的数字。
- `verifier_agent` 只能解释风险和修正方向；与本地硬规则冲突时保留本地结论，并记录冲突事件。
- `delivery_agent` 只能整理摘要和风险提示，不能删除风险或绕过人工审批。
- 任一云端端点异常时，系统记录 `agentarts.fallback` 并继续执行。
