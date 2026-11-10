# 急先锋 · 应急资源调度推演平台

面向园区、企业、学校、医院和应急演练单位的初赛提交版。用户可以维护物资库、上传库存快照、配置灾情需求，由四个智能体生成多套可比较的调度方案，并通过独立核验完成退回重算。

> 本项目使用演练数据，不接入真实灾害监测系统，不替代真实应急指挥。

## 已实现能力

- 物资主数据：内置常用物资、手动新增、CSV/XLSX 批量导入、别名与单位换算字段
- 库存快照：从物资库选择物资上传库存，每次上传形成独立版本
- 场景推演：暴雨洪涝、高温保障两个预置场景，可调整物资、需求量、优先级和截止时间
- 多方案生成：极速直送、成本集约、韧性均衡三套方案
- 独立核验：道路封闭、库存、车辆趟次、时效和需求覆盖检查
- 退回重算：第一轮主通道方案被核验退回，第二轮改走备用通道后重新核验
- 实时运行轨迹：FastAPI SSE 推送调度、需求、RAG、匹配、核验、重规划和交付事件，支持 Last-Event-ID/after 断线续传
- Agent 编排中心：五核心模块、四核心 Agent 契约、双核验投票支持 Agent、工具注册、DAG 与条件回环可视化
- 评估中心：独立贪心基线、六场景分布、重复计算一致性、1/5/10/20 并发、平台队列压测、30 秒持续压测、6 类故障注入与 Wilson 95% 区间
- 华为云：四个角色均可接入真实 AgentArts `/invocations`；未配置时自动使用本地四智能体回退
- Agent 注册表：动态注册、版本切换；配置端点时调用 AgentArts，未配置端点时自动使用离线角色执行器
- 跨运行记忆：PostgreSQL/SQLite 持久化置信度、阻断历史、成功修复策略和推荐方案；只作为允许策略集合内的 tie-break
- A2A Agent 通信：请求/应答/广播消息持久化，SSE 实时展示 `a2a.message`，通道异常时自动回退 blackboard
- 能力画像调度：按任务能力标签、历史成功率、平均耗时和当前负载计算匹配分，并保留无历史时的优先级回退
- 修复经验库：按问题代码与场景沉淀历史修复策略，只向 Prompt 注入高成功率参考，不改变硬规则
- 置信度与 Prompt 证据：需求置信度按可解释因子计算，反馈循环生成 `v1.4+adaptive.rN` Prompt 补丁并进入下一轮上下文
- 可视化与代码化工作流：节点卡片、拖拽连线、往返 JSON 同步、可替换主干 DAG、工作流内 loop/tool 节点、条件/并行/投票/子工作流，以及 3 个可实例化模板
- 可上传知识库：多格式上传、自动分块、可替换远程 embedding（离线哈希回退，未配置时明确不宣称语义检索）+ BM25 + 确定性重排；返回页码/章节/字符区间引用定位
- 七项 AI 决策证据：需求假设、P0-P3 相邻调整、修复策略、已核验方案推荐、LLM 权重建议、核验解释和交付摘要；每项都有安全边界与确定性等价能力
- 量化评估：道路中断、正常条件、库存缺口、无可用车辆、西部走廊和南部环线共 6 个场景、120 个默认样本，其中 2 套独立路网；另含 30 秒持续压测、1/5/10/20 并发、平台队列压测和故障注入
- 端到端并发基准：`backend/benchmark/run_perf_benchmark.py` 对 1/5/10/20 任务执行两轮一致性对比，并同时统计单 Agent 贪心基线的安全率、P95、得分和工具调用数
- AgentArts 运行观测：HTTP 连接复用、任务完成即返回、匹配与核验复核并行；本机近期完整运行约 32–47 秒，仅作观测值，不作为 SLA
- 平台级运行调度：PostgreSQL/SQLite 持久化队列、动态优先级、并发上限、租约心跳、重启恢复和幂等提交
- 库存一致性：审批时原子预留 `locked_quantity`，并发审批不会重复承诺同一批库存，并支持释放预留
- 失败重放：AgentArts/OSRM 失败写入持久化重试队列，指数退避，耗尽后进入 dead-letter
- 安全与审计：登录、四类角色、失败锁定和操作审计，审批人及库存预留写入运行产物
- 交付结果：方案对比、调拨明细、评分、风险提示、执行清单和打印报告
- 演练兜底：本地确定性编排不依赖外网，AgentArts 适配器可单独接入
- 真实运营事件：手工确认事件，预留 Webhook 接入
- 资源导入：物资、库存、仓库、安置点、车辆、路线 CSV/XLSX 导入
- 车辆位置：GPS 可选，支持批量位置接口和车辆位置历史
- 执行闭环：审批后生成运输任务并支持任务状态跟踪
- 免费地图：Leaflet + 无 Key 兼容瓦片默认配置，可切换 OpenStreetMap/自建瓦片，不依赖收费地图 API

## 技术栈

- 后端：FastAPI、Pydantic、PostgreSQL 18（SQLite 离线回退；CI 使用真实 PostgreSQL service 执行迁移、`SKIP LOCKED` 和并发预留守恒测试）
- 前端：Vue 3、Vite、Leaflet、高德兼容真实地图瓦片
- 智能体：四个核心 Agent 契约 + 双核验支持 Agent + 本地执行器 + 华为云 AgentArts SDK `/invocations` 适配
- 实时通信：Server-Sent Events
- 数据上传：CSV / XLSX

## 零配置运行

默认使用 SQLite、演练数据和本地智能体回退，不需要华为云、高德 Key 或 PostgreSQL 也能运行。

### Windows

```powershell
git clone https://github.com/Jtyang123/jixianfeng.git
cd jixianfeng
.\setup.cmd
.\start.cmd
```

### Linux/macOS

```bash
chmod +x setup.sh start.sh
./setup.sh
./start.sh
```

### Docker

```bash
docker compose up --build -d
```

访问：`http://127.0.0.1:8010`。

安装要求：Python 3.10+、Node.js 18+、npm；Docker Compose 可选。`setup` 会自动创建 `.venv`、安装依赖、构建前端，并在缺少 `.env` 时从 `.env.example` 创建。

未配置华为云 AgentArts 时使用本地四智能体执行器；未配置高德时手工填写经纬度；未配置 PostgreSQL 时使用 SQLite。

> 为保护账号安全，云端 API Key、管理员密码和本地业务数据库不会上传 GitHub。下载者填写自己的 Key 后即可获得相同的云端能力；不填写时使用本地回退。

## 可选外部服务

编辑 `.env` 可启用 AgentArts、高德地址解析、PostgreSQL 和自建 OSRM。任何 `.env` 和 API Key 都不得提交到 Git。

## 推荐演示流程

1. 进入“调度工作台”，加载“武汉暴雨洪涝应急演练”。
2. 展示物资选择器、需求量、优先级和截止时间。
3. 调整“时效 / 成本 / 安全”权重，点击“启动多智能体推演”。
4. 观察需求研判、资源匹配、独立核验、退回重算和方案交付实时事件。
5. 第一轮三套方案因主通道封闭被退回。
6. 第二轮自动排除封闭路线，改用备用通道并生成三套可比较方案。
7. 切换方案查看成本、最长时效、需求覆盖、核验评分和调拨明细。
8. 点击“人工审批通过”，再导出打印报告。
9. 进入“数据中心”查看物资库、库存上传和 PostgreSQL 快照版本。
10. 进入“智能体中心”查看 Agent 契约、DAG 工作流、真实地图与量化评估。

## 数据导入

模板下载地址：

- 物资库模板：`/api/materials/template`
- 库存模板：`/api/inventory/template`

物资库支持字段：物资编码、名称、别名、分类、规格、基本单位、包装单位、换算率、储存条件、保质期、冷链、危险品、单件重量和默认优先级。

库存上传要求仓库已存在、物资已存在于物资库。每次有效上传都会创建新的库存快照，历史运行不会被覆盖。

## 提交材料

- 技术方案书：`docs/方案书.md`（7438 个中文字符）
- PDF 方案书：`docs/急先锋_项目方案书.pdf`
- 讲解 PPT：`docs/急先锋_项目讲解PPT.pptx`（15 页）
- 演示脚本：`docs/DEMO_SCRIPT.md`
- 答辩问答：`docs/答辩问答.md`
- 评分标准对齐：`docs/评分标准对齐清单.md`
- Agent 设计：`docs/AGENT_DESIGN.md`
- AgentArts 联调记录：`docs/AGENTARTS_VERIFICATION.md`
- 队友上手说明：`docs/TEAMMATE_GUIDE.md`
- 系统截图：`docs/screenshots/`

如需重新生成 HTML、PDF 与 PPT：

```powershell
pip install -r tools\requirements.txt
python tools\build_submission.py
```

## 两种运行模式

### 默认模式（未配置 LLM）

默认运行时使用确定性主干工作流：工作流引擎负责顺序、并行、条件、循环和子工作流；工具注册表提供库存、路网、车辆和知识检索；扩展 Agent 与跨运行记忆参与上下文和风险补充；AgentArts 在配置后负责云端复核和方案推荐。该模式不依赖外网，适合离线演示和复现实验。

### LLM ReAct 模式（配置真实 LLM 后）

配置 `LLM_ENABLED=true`、真实 `LLM_API_KEY` 和模型地址后，Planner 负责决定“调用哪些工具、按什么顺序调用、结果是否符合预期、是否重新规划”；库存、方案、成本、ETA、核验和交付数字仍由确定性工具计算。该模式下 AgentArts 不参与本次运行，避免两条推理链重复决策。

> 基准测试与压测数据均在默认模式下产生；LLM ReAct 模式的延迟、成功率和调用次数需要单独统计，不能与默认模式数据混合引用。

四角色固定主干只是默认模式中的安全执行链，不代表平台只有这一种运行时。

### A2A、调度与经验库接口

- `GET /api/a2a/messages?run_id=<运行ID>&limit=100`：按运行查询 A2A 消息历史。消息包含 `message_id`、`sender`、`receiver`、`message_type`、`content`、`reply_to`、`correlation_id`、`timestamp`、`status`。
- SSE 事件 `a2a.message`：推送请求、应答和广播消息；`a2a.fallback`：A2A 不可用时说明已回退 blackboard，主流程继续。
- 能力调度配置：`AGENT_TASK_CAPABILITY_MAP_JSON` 可覆盖任务类型到能力标签的映射；系统按“能力匹配度 × 0.4 + 历史成功率 × 0.3 + (1 - 当前负载) × 0.3”排序，无历史数据时回退现有优先级。
- 修复经验库复用 `agent_memory`，键为 `repair_experience:<场景>:<问题代码>:<策略>`；仅向 Prompt 注入成功率不低于 0.6 的参考片段，库存、路线、运力、时效与核验结论不变。
- `GET /api/benchmark/report`：返回最近一次端到端并发基准 JSON；无报告时返回 `status=empty` 和运行命令提示。
- 独立运行基准：`cd backend && ../.venv/Scripts/python.exe -m benchmark.run_perf_benchmark`。默认输出到 `data/benchmark_latest.json`，可用 `PERF_BENCHMARK_REPORT` 覆盖路径。

## AgentArts 接入

默认 `AGENT_RUNTIME=local`，保证断网演示稳定。

如需接入华为云 AgentArts：

```env
AGENT_RUNTIME=agentarts
AGENTARTS_ENABLED=true
AGENTARTS_BASE_URL=https://your-agentarts-runtime
AGENTARTS_API_KEY=your-key
AGENTARTS_INVOKE_PATH=/invocations
# 可选：四个角色分别部署时配置 JSON
AGENTARTS_AGENT_ENDPOINTS_JSON={"demand_agent":"https://.../invocations"}

# 可选：OpenAI-compatible LLM 推理层
LLM_ENABLED=true
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_API_KEY=sk-xxx
LLM_MODEL=deepseek-chat
```

AgentArts SDK 运行时源码位于 `agentarts_service/`，使用官方 `POST /invocations` 和 `GET /ping` 契约。部署后可通过 `GET /api/agentarts/check` 检查连接。未配置 LLM/AgentArts 时使用确定性的本地回退，不影响库存、规划、核验和回环演示。四个核心 Agent 的角色、输入输出和工具权限见 `docs/AGENT_DESIGN.md`。

外部 HTTP/MCP 工具必须显式配置可信域名；系统默认阻止回环、内网、链路本地和保留地址，避免 SSRF：

```env
TOOL_ALLOWED_HOSTS=tool.example.com,mcp.example.com
TOOL_ALLOW_PRIVATE_HOSTS=false
TOOL_HTTP_TIMEOUT=20
MCP_PROTOCOL_VERSION=2024-11-05
```

重要原则：库存、路线、载重、时效和核验规则由确定性代码计算；AgentArts 负责理解、编排、策略选择与解释，不能替代硬规则审批。系统把“确定性规则 = 安全内核，LLM = 安全边界内的推理与编排层”作为主动架构决策，并在决策矩阵中逐项列出本地确定性等价能力。

答辩时必须主动说明：本项目不宣称“LLM 负责全部方案计算和核验判断”。LLM 的实际决策位是需求优先级、方案权重、允许策略内的修复选择和已核验方案推荐；道路、库存、载重、时效、评分和最终 verdict 由确定性规则执行。

## API

- `GET /api/health`
- `GET /api/dashboard`
- `GET /api/system/status`
- `GET /api/agentarts/check`
- `GET /api/readiness`
- `GET /api/events`
- `POST /api/events`
- `POST /api/integrations/events`
- `POST /api/integrations/vehicles/positions`
- `GET /api/vehicles`
- `GET /api/tasks`
- `PATCH /api/tasks/{task_id}`
- `GET /api/platform/manifest`
- `GET /api/agents`
- `GET /api/agents/discover`
- `GET /api/tools`
- `PUT /api/tools/{tool_id}`
- `POST /api/tools/{tool_id}/invoke`
- `POST /api/mcp`
- `GET /api/workflows`
- `GET /api/workflow-templates`
- `POST /api/workflow-templates/{template_id}/instantiate`
- `GET /api/memory`
- `DELETE /api/memory/{agent_id}/{memory_key}`
- `POST /api/workflows/validate`
- `PUT /api/workflows/{workflow_id}`
- `POST /api/workflows/{workflow_id}/run`
- `GET /api/workflow-runs/{run_id}`
- `GET /api/knowledge/documents`
- `POST /api/knowledge/documents`
- `GET /api/knowledge/search`
- `POST /api/agents`
- `POST /api/agents/{agent_id}/versions`
- `POST /api/agents/{agent_id}/versions/{version}/activate`
- `POST /api/agents/{agent_id}/invoke`
- `GET /api/evaluation/benchmark`
- `POST /api/evaluation/diagnostics`
- `GET /api/evaluation/diagnostics`
- `GET /api/materials`
- `POST /api/materials`
- `POST /api/materials/import`
- `GET /api/inventory`
- `POST /api/inventory/import`
- `GET /api/scenarios`
- `POST /api/runs`
- `GET /api/runs/{run_id}`
- `GET /api/runs/{run_id}/events`（支持 `Last-Event-ID` 与 `after`）
- `POST /api/runs/{run_id}/approve`（原子库存预留）
- `POST /api/runs/{run_id}/release-reservation`
- `GET /api/queue/metrics`
- `GET /api/retry-jobs`
- `POST /api/retry-jobs/{job_id}/replay`
- `GET /api/runs/{run_id}/report`

## 真实运营模式

默认 `FARMMATCH_MODE=demo`，加载演示场景。真实部署建议：

```env
FARMMATCH_MODE=operational
FARMMATCH_SEED_DEMO=false
INTEGRATION_API_KEY=设置一个外部接入密钥
```

运营模式使用同一个 PostgreSQL 数据库。新数据库不会自动写入演示物资，用户需要通过“数据中心”导入真实仓库、物资、库存、车辆、安置点和路线。

GPS 未接入不影响调度；车辆位置默认使用所属仓库位置。GPS 平台可通过以下接口推送位置：

```http
POST /api/integrations/vehicles/positions
X-Integration-Key: 你的接入密钥
```

## 公网部署

Dockerfile、PostgreSQL 编排和华为云 ECS 发布步骤见 `deploy/DEPLOYMENT.md`。

## 测试

```powershell
cd backend
pytest -q

cd ..\frontend
npm test
npm run build
```

后端测试覆盖持久化运行队列、租约恢复、幂等提交、库存并发预留、SSE 续传、失败重试/dead-letter、工作流内循环、tool 结果消费、MCP 握手、SSRF 防护、双核验投票、模板实例化、跨运行记忆、Prompt 自适应、LLM 权重决策、可替换 embedding 与引用定位、六场景基准和 30 秒持续压测。PostgreSQL CI 共执行 104 项测试；本机无 PostgreSQL 环境时结果为 `101 passed, 3 skipped`，1 条第三方弃用警告。前端包含 4 项画布纯函数测试和 6 项组件/重连/库存预留测试，`10 passed`，生产构建通过。正式服务已完成三运行并发提交、审批库存预留和两套独立路网验收。

## 边界说明

- 当前事件、仓库、库存、车辆、路线均为演示数据。
- 当前定位为方案编制、应急演练和辅助决策。
- 不包含真实灾害自动发现、真实库存接口和真实车辆派遣。
- 如用于生产，需要接入授权的物资台账、路网、运力和应急指挥系统，并增加身份认证、权限、审计和运维体系。
