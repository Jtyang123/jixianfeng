# 急先锋 3 分钟演示分镜

适用版本：`llm-planner` 主运行时。正式演示前先运行 `python -m benchmark.demo_preflight`，确认推荐模式为 `llm-planner`，并记录 AgentArts 四条延迟与最近一次运行耗时。

核心口径：AgentArts 四角色在线是“云端可替换能力”的证据，不是本次 Planner 主链路；本次主链路由 LLM Planner 规划确定性工具，空 `agentarts_trace` 是预期行为。

## 时间轴

| 时间 | 动作 | 预期画面 | 耗时 | 风险与替代方案 |
| --- | --- | --- | --- | --- |
| 0-20s | 打开 AgentStudio，指向 AgentArts 检测区域。默认展示 `demo_preflight` 已缓存的 `4/4` 在线和四条延迟；若缓存缺失且预留了 10-25s，再点一次“检测 AgentArts”。旁白：这是云端可替换证据，不是主链路。 | 四个 Agent 在线，四条延迟逐条可见；页面显示“上次检测 X 分钟前（缓存）”或“检测中…”。 | 20s；真检测可能 8-25s | 网络抖动或配额紧张时直接切到预检截图；不要在正式计时内等待第二次重试。 |
| 20-60s | 进入调度工作台，选择 `rainstorm-ii`，强调“不要选 `heatwave-iii`，其人工研判率约 46%”。展示需求、资源快照和权重。 | 场景名称、受灾人口、封闭道路、物资需求和“启动多智能体推演”按钮清晰可见。 | 40s | 若误选 `heatwave-iii`，立即切回 `rainstorm-ii`；不要现场解释历史统计。 |
| 60-120s | 点击“启动多智能体推演”。依次指向 Planner 轨迹、工具调用、调度明细表和六阶段耗时条。 | 事件流出现 `llm.thinking`、工具步骤、`scheduler.decided`；右侧 AgentRail 进入运行态；阶段条显示 demand / retrieval / matching / verification / a2a / delivery。 | 60s，目标单轮约 40-50s，按事件流讲解 | LLM 超时会在 15s 超时、预算 45s 内自动重试一次；若仍失败，展示确定性回退并说明安全基线不中断。 |
| 120-135s | 点击“审批通过”，观察状态转为 `approved`，进入执行任务页确认库存预留成功及释放 TTL。 | 审批状态从 `awaiting_approval` 变为 `approved`；页面显示库存预留成功、任务已生成、TTL 时间可见。 | 15s | 若按钮不可用，说明运行还不是 `awaiting_approval`；不要重复点击，先检查运行状态。 |
| 135-165s | 展开核验退回链路，指向 `rework_directive` 和 `rounds`：首轮因封闭道路被拒，第二轮排除封闭路线后通过。 | 首轮“独立核验退回”，第二轮“自适应修正”，`rounds >= 2`，最终评分和交付状态为通过。 | 30s | 若本次场景未触发退回，切到已录制的 `rainstorm-ii` 运行，不现场重复刷场景。 |
| 165-180s | 打开运行详情中的“A2A 协商”和“记忆使用”折叠区。 | 显示 sender→receiver、message_type、状态；记忆区显示本次注入经验和写入键。 | 15s | 若本次没有 A2A 消息，说明“未触发跨仓协商”，展示记忆面板即可，不临时修改数据。 |

审批说明：`awaiting_approval` 是正常终态，前提是 `final_verification.verdict=pass`，不是失败；不点“审批通过”则运行会一直停在这一步，不会自动生成执行任务和库存预留。

## 必须强调的三档运行时

- `llm-planner`：Planner 决定工具与顺序，核心四角色本地执行；AgentArts 只参与 `post_verification` 扩展审查。
- `agentarts`：LLM 关闭，四个核心角色调用云端 `/invocations`，没有 Planner 轨迹。
- `local`：AgentArts 关闭，全链路本地确定性执行。

## 不要现场点的按钮

1. **知识库自由文本检索**：当前锁定 `hash-fallback`，现场临时改写成近义句可能返回空；只使用预置查询或已有检索截图。
2. **AgentArts“检测/重新检测”按钮**：会真实发起 4 次推理，耗时数秒并消耗云端配额；默认使用 `demo_preflight` 缓存结果或录屏。只有缓存缺失且已预留 10-25s 时才点一次。
3. **任何一次新的 benchmark**：完整基准是分钟级任务，不进入 3 分钟演示；只展示已有报告或 `demo_preflight` 摘要。

## dry-run 落到 needs_human 怎么办

直接切到场景列表中 `human_ratio` 最低的备份场景重跑一次。讲解词改为：“系统识别到道路全封闭，主动转为人工研判，不做假通过。”这是安全能力展示，不是演示失败。不要现场修改路线数据强行改成 pass。

## 兜底切换

- Planner 连续重试后仍失败：保持页面，说明系统已自动回退到确定性安全基线，继续展示核验与交付。
- AgentArts 检测超时：使用预检输出中的四条历史延迟和 `online_rate=1.0`，不重复点击。
- 现场运行未触发自循环：切到最近一次 `rainstorm-ii` 成功运行，展示 `rounds` 和 `rework_directive`。