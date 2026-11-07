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
- 实时运行轨迹：FastAPI SSE 推送调度、需求、RAG、匹配、核验、重规划和交付事件
- Agent 编排中心：五核心模块、四 Agent 契约、工具注册、DAG 与条件回环可视化
- 评估中心：基线对比、截断指标、重复计算一致性、需求覆盖和评分维度映射
- 华为云：四个角色均可接入真实 AgentArts `/invocations`；未配置时自动使用本地四智能体回退
- Agent 注册表：动态注册、能力发现、版本发布、在线切换和激活端点直接调用测试
- AgentArts 决策闭环：匹配智能体的推荐从已核验方案中影响最终排序
- 量化评估：单 Agent 基线与多 Agent 闭环各重复 20 次，统计安全率和 P95 耗时；另单列四个 Agent 全部走 AgentArts 的真实运行证据
- AgentArts 调用加速：HTTP 连接复用、任务完成即返回、资源匹配与独立核验云端复核并行；最新完整云端运行 28.8 秒
- 安全与审计：登录、四类角色、失败锁定和操作审计
- 交付结果：方案对比、调拨明细、评分、风险提示、执行清单和打印报告
- 演练兜底：本地确定性编排不依赖外网，AgentArts 适配器可单独接入
- 真实运营事件：手工确认事件，预留 Webhook 接入
- 资源导入：物资、库存、仓库、安置点、车辆、路线 CSV/XLSX 导入
- 车辆位置：GPS 可选，支持批量位置接口和车辆位置历史
- 执行闭环：审批后生成运输任务并支持任务状态跟踪
- 免费地图：Leaflet + 无 Key 兼容瓦片默认配置，可切换 OpenStreetMap/自建瓦片，不依赖收费地图 API

## 技术栈

- 后端：FastAPI、Pydantic、PostgreSQL 18（SQLite 离线回退）
- 前端：Vue 3、Vite、Leaflet、高德兼容真实地图瓦片
- 智能体：四个职责隔离的 Agent 契约 + 本地执行器 + 华为云 AgentArts SDK `/invocations` 适配
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
- 系统截图：`docs/screenshots/`

如需重新生成 HTML、PDF 与 PPT：

```powershell
pip install -r tools\requirements.txt
python tools\build_submission.py
```

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
LLM_BASE_URL=https://your-llm-endpoint/v1
LLM_API_KEY=your-key
LLM_MODEL=your-model
```

AgentArts SDK 运行时源码位于 `agentarts_service/`，使用官方 `POST /invocations` 和 `GET /ping` 契约。部署后可通过 `GET /api/agentarts/check` 检查连接。未配置 LLM/AgentArts 时使用确定性的本地回退，不影响库存、规划、核验和回环演示。四个 Agent 的角色、输入输出和工具权限见 `docs/AGENT_DESIGN.md`。

重要原则：库存、路线、载重、时效和核验规则由确定性代码计算；AgentArts 负责理解、编排与解释，不能替代硬规则审批。

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
- `POST /api/agents`
- `POST /api/agents/{agent_id}/versions`
- `POST /api/agents/{agent_id}/versions/{version}/activate`
- `POST /api/agents/{agent_id}/invoke`
- `GET /api/evaluation/benchmark`
- `GET /api/materials`
- `POST /api/materials`
- `POST /api/materials/import`
- `GET /api/inventory`
- `POST /api/inventory/import`
- `GET /api/scenarios`
- `POST /api/runs`
- `GET /api/runs/{run_id}`
- `GET /api/runs/{run_id}/events`
- `POST /api/runs/{run_id}/approve`
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
```

测试覆盖两轮核验闭环、四智能体编排、AgentArts 适配、动态 Agent 注册、注册 Agent 直接调用权限与失败边界、仅全云端运行的指标过滤、认证鉴权、资源 CRUD、路线生成和 API 主流程。当前结果：24 passed，1 条第三方弃用警告。另已使用当前提交导出的干净源码完成一键安装、前端构建、独立端口启动、完整调度和审批出单验收。

## 边界说明

- 当前事件、仓库、库存、车辆、路线均为演示数据。
- 当前定位为方案编制、应急演练和辅助决策。
- 不包含真实灾害自动发现、真实库存接口和真实车辆派遣。
- 如用于生产，需要接入授权的物资台账、路网、运力和应急指挥系统，并增加身份认证、权限、审计和运维体系。
