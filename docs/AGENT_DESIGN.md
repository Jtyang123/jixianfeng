# 急先锋四智能体设计

## 总原则

1. 四个智能体通过版本化黑板产物交接，不共享无限聊天历史。
2. 库存、路线、车辆、成本和时效由确定性工具计算。
3. 核验智能体只接收原始数据、候选方案和规则，不接收生成智能体的内部推理。
4. 核验智能体没有修改方案的权限，只能退回、通过或转人工。
5. 最大重算两轮，超出后必须进入人工研判。

## A01 需求研判智能体

- 标识：`demand_agent`
- 输入：事件、安置点、人口、物资选择、需求量、优先级、截止时间
- 工具：需求分配、物资主数据查询、单位标准化
- 输出：`DemandPlan`
- 关键字段：物资、安置点、数量、基本单位、P0/P1/P2、deadline、assumptions、evidence_refs
- 禁止：查询库存分配、指定仓库、生成路线

## A02 资源匹配智能体

- 标识：`matching_agent`
- 输入：`DemandPlan`、库存快照、仓库、车辆、路线、目标权重
- 工具：库存查询、车辆查询、路线查询、多目标方案生成
- 输出：三套 `DispatchPlan`
- 方案目标：极速直送、成本集约、韧性均衡
- 禁止：修改库存、标记核验通过、直接派车

## A03 独立核验智能体

- 标识：`verifier_agent`
- 输入：原始需求、候选方案、原始库存、路线、车辆、事件封闭路线
- 工具：库存校验、载重校验、路线校验、时效校验、覆盖校验
- 输出：`VerificationReport`
- 结论：`pass / rework / human_escalation`
- 硬阻断：道路封闭、库存不足、需求缺口
- 警告：时效超限、车辆趟次不足
- 通过门槛：无硬阻断且评分不低于 80
- 禁止：编辑、补全或润色候选方案

## A04 方案交付智能体

- 标识：`delivery_agent`
- 输入：通过核验的方案、核验报告、数据快照
- 工具：比较、报告渲染、PDF/打印导出
- 输出：推荐方案、备选方案、风险提示、执行清单、数据依据、免责声明
- 禁止：修改数字、删除风险、把未通过方案标记为通过

## 状态机

```text
queued
  -> running
  -> demanding
  -> matching
  -> verifying
      -> pass -> awaiting_approval
      -> rework -> matching -> verifying
      -> still fail -> needs_human
  -> approved
  -> failed
```

## 两种运行模式

- **默认模式（未配置 LLM）**：确定性主干工作流运行 A01→A02→A03→门控→A04，工作流引擎、工具注册表、扩展 Agent、记忆写入和 AgentArts 云端复核均可使用。断网和 LLM 不可用时保持该模式。
- **LLM ReAct 模式（配置真实 LLM 后）**：Planner 决定工具集合、执行顺序、结果判断和重规划；A01-A04 的确定性工具仍负责需求拆分、方案计算、独立核验和交付事实。该模式下 AgentArts 不参与同一次运行。
- 数据读取步骤按初始计划确定性推进，仅在方案生成与核验不通过时请求模型判断，避免每步一次 LLM 调用的延迟与 token 开销。

基准测试与压测结果只代表默认模式。引用性能数字时必须注明运行模式，不得把默认模式和 LLM ReAct 模式的数据混在一起。
## A2A Agent 通信

- 消息字段：`message_id`、`sender`、`receiver`、`message_type`、`content`、`reply_to`、`correlation_id`、`timestamp`、`status`。
- 支持 `request`、`reply`、`broadcast` 三种消息；请求与应答通过 `correlation_id` 关联，回复通过 `reply_to` 指向请求消息。
- 消息同时进入内存队列和数据库 `a2a_messages`，`GET /api/a2a/messages` 可追溯历史；SSE 使用 `a2a.message` 推送。
- 当跨仓调拨协作需要 A02 补充信息时，A01 可发送请求并等待 A02 应答；异常时发送 `a2a.fallback` 并回退到黑板产物，不阻断主流程。

## 能力画像调度

任务能力映射由 `AGENT_TASK_CAPABILITY_MAP_JSON` 配置。调度候选分按以下公式计算：

```text
匹配分 = 能力标签匹配度 × 0.4 + 历史成功率 × 0.3 + (1 - 当前负载) × 0.3
```

历史数据来自运行记录和 `agent_performance`。有历史时优先选择匹配分高且负载低的 Agent；无历史时回退现有优先级。高负载候选会标记 `rate_limited`，但不会绕过核验门控。

## 修复经验库

- 存储复用 `agent_memory`，键为 `repair_experience:<场景>:<问题代码>:<策略>`。
- 记录字段包括策略描述、Prompt 片段、采纳次数、成功次数和成功率。
- 下一轮生成前检索当前阻断代码，只注入成功率不低于 0.6 的“历史有效经验”作为参考。
- 经验只影响 Prompt 上下文，不得修改库存、路线、运力、时效和核验 verdict。

## 端到端并发基准

`backend/benchmark/run_perf_benchmark.py` 在默认确定性模式下运行 1/5/10/20 个完整任务，每个档位重复两轮并比较关键字段一致性。每档统计成功率、平均耗时、P95、平均得分、LLM 调用次数和工具调用次数，并同时运行单 Agent 贪心基线。结果输出到 `data/benchmark_latest.json`，通过 `GET /api/benchmark/report` 读取。该基准是单进程 asyncio 任务级并发，不等同于多实例集群压测。

## AgentArts 映射

| 本地 Agent | AgentArts 配置建议 |
|---|---|
| `demand_agent` | 知识库 + 需求估算工具 + 结构化输出 |
| `matching_agent` | 库存/路线工具 + 方案生成器 + 目标权重 |
| `verifier_agent` | 只读工具白名单 + 更严格的输出 Schema |
| `delivery_agent` | 已核验产物 + 报告模板 |

本地与 AgentArts 使用同一份输入输出契约。真实接入前必须确认 AgentArts 当前项目的调用路径、鉴权方式与工具注册格式。
