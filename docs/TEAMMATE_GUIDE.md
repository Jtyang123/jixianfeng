# 急先锋队友上手说明

## 1. 项目是什么

急先锋是赛道一“Agent 编排创新应用”的可运行原型，面向应急资源调度。系统不是单一聊天机器人，而是让四个职责隔离的智能体协作完成：

1. 需求研判
2. 资源匹配
3. 独立核验
4. 方案交付

## 2. GitHub 仓库

```text
https://github.com/Jtyang123/jixianfeng
```

当前稳定版本：以 GitHub `main` 分支最新提交为准

GitHub 中包含：

- FastAPI 后端
- Vue 前端
- 四个核心 Agent 契约、双核验支持 Agent 与本地执行器
- AgentArts 调用适配
- Agent 注册、发现与版本管理
- 测试、文档、一键安装脚本
- GitHub Actions 配置

## 3. 最快启动方式

### Windows

```powershell
git clone https://github.com/Jtyang123/jixianfeng.git
cd jixianfeng
.\setup.cmd
.\start.cmd
```

打开：

```text
http://127.0.0.1:8010
```

首次运行默认使用 SQLite、演练数据和本地智能体回退，不要求华为云 Key。

### Linux / macOS

```bash
git clone https://github.com/Jtyang123/jixianfeng.git
cd jixianfeng
chmod +x setup.sh start.sh
./setup.sh
./start.sh
```

## 4. 四个 Agent 各自负责什么

| Agent | 职责 | 不允许做什么 |
|---|---|---|
| `demand_agent` A01 | 把灾情、人口、物资选择转成结构化需求 | 不查询库存、不分配仓库、不生成路线 |
| `matching_agent` A02 | 根据库存、车辆、路网和权重生成三套方案 | 不修改库存、不标记核验通过、不直接派车 |
| `verifier_agent` A03 | 独立检查道路、库存、载重、时效和覆盖 | 不修改、补全或润色候选方案 |
| `delivery_agent` A04 | 汇总已通过核验的事实生成交付包 | 不改数字、不删除风险、不把未通过方案标为通过 |

四个 Agent 使用结构化产物交接，不共享无限聊天历史。

## 5. 演示流程

1. 进入“调度工作台”。
2. 选择“武汉暴雨洪涝应急演练”。
3. 检查物资需求和高级权重。
4. 点击“启动多智能体推演”。
5. 观察 A01 需求研判。
6. 观察 A02 生成极速、成本、韧性三套方案。
7. 观察 A03 独立核验并退回第一轮方案。
8. 观察问题分类重算后重新核验。
9. 查看方案对比、核验记录和交付包。
10. 点击人工审批，生成运输任务。
11. 进入“智能体中心”查看 Agent 注册、版本和量化评估。
12. 进入“运行记录”查看完整事件轨迹。

## 6. 关键文件

- `backend/app/orchestrator.py`：多 Agent 主流程、SSE 事件、反馈重算
- `backend/app/agents.py`：需求、方案、核验和交付算法
- `backend/app/agentarts.py`：华为云 AgentArts 调用适配
- `backend/app/registry.py`：Agent 注册、版本和运行指标
- `backend/app/scheduler.py`：动态任务调度与 Agent 定义
- `frontend/src/components/AgentRail.vue`：四个 Agent 的运行状态
- `frontend/src/views/Evaluation.vue`：量化评估和真实云端指标
- `docs/AGENT_DESIGN.md`：四个 Agent 的输入输出与权限边界

## 7. GitHub 上没有的内容

为了保证安全，以下内容不会提交到 GitHub：

- `.env` 中的真实 API Key
- AgentArts 入站 Key
- AMap Key
- 本机 PostgreSQL 业务数据
- 管理员密码文件
- 本地导入的库存、车辆和事件数据
- `node_modules`、前端构建产物和本地数据库

因此，队友克隆后得到的是**完全相同的代码和文档**，但不会自动拥有你本机的私有配置和数据库数据。

## 8. 如何获得与你一致的外部服务能力

需要私下传递或由每名队员自行配置：

```env
AGENTARTS_ENABLED=true
AGENTARTS_AGENT_ENDPOINTS_JSON=...
AGENTARTS_AGENT_KEYS_JSON=...
AMAP_WEB_SERVICE_KEY=...
DATABASE_URL=...
```

请不要把真实密钥提交到 Git，也不要发送到公开群聊。

如果只需要演示项目，使用默认 SQLite + 本地智能体回退即可。

## 9. 验证项目是否正常

后端测试：

```powershell
cd backend
pytest -q
```

前端构建：

```powershell
cd frontend
npm run build
```

当前基线：后端 `113 passed, 3 skipped`、前端 `10 passed`，前端生产构建通过。

## 10. 协作约定

1. 修改前先执行 `git pull origin main`。
2. 新功能使用独立分支。
3. 不提交 `.env`、数据库、密钥和本地截图。
4. 提交前运行测试和前端构建。
5. 涉及 Agent 输出、核验规则或指标口径的修改，需要同步更新文档。
