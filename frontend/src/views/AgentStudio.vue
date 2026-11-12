<script setup>
import { computed, onMounted, ref } from 'vue'
import {
  Bot, BookOpen, BrainCircuit, CheckCircle2, Cpu, Database, GitBranch, History,
  Layers3, Network, RefreshCw, RotateCcw, ShieldCheck, Sparkles, Trash2, Wrench,
} from '@lucide/vue'
import { api } from '../api'
import WorkflowDesigner from './WorkflowDesigner.vue'

const props = defineProps({ user: { type: Object, default: null } })

const manifest = ref(null)
const memories = ref([])
const loading = ref(true)
const error = ref('')
const checking = ref(false)
const checkResult = ref(null)
const toolSaving = ref(false)
const toolMessage = ref('')
const toolError = ref('')
const toolForm = ref({
  id: 'delivery-note-plugin',
  name: '交付备注插件',
  kind: 'http',
  endpoint: 'https://example.com/tool',
  method: 'POST',
  description: '通过标准 HTTP API 扩展交付能力',
  schema: JSON.stringify({ type: 'object', properties: { note: { type: 'string' } } }, null, 2),
})
const isAdmin = computed(() => props.user?.role === 'admin')
const agentIcons = [BrainCircuit, Network, ShieldCheck, CheckCircle2]
const runtimeOK = computed(() => manifest.value?.llm?.enabled)
const agentartsOK = computed(() => manifest.value?.agentarts?.enabled)
const configuredAgentIds = computed(() => manifest.value?.agentarts?.configured_agents || [])
const configuredAgentKeyIds = computed(() => manifest.value?.agentarts?.agent_keys_configured || [])
const missingAgentIds = computed(() => manifest.value?.agentarts?.missing_agents || [])
const agentCoverage = computed(() => manifest.value?.agentarts?.agent_coverage || '0/4')
function agentConnectionLabel(agentId) {
  if (configuredAgentIds.value.includes(agentId) && configuredAgentKeyIds.value.includes(agentId)) return 'AgentArts · 专用 Key'
  if (configuredAgentIds.value.includes(agentId)) return '端点已配置 · 全局 Key'
  return '本地确定性回退'
}
function agentCheckState(agentId) {
  return checkResult.value?.checks?.find((item) => item.agent_id === agentId) || null
}
function agentCheckLabel(agentId) {
  const check = agentCheckState(agentId)
  if (!check) return ''
  const latency = Number(check.latency_ms || 0).toFixed(0)
  return check.ok ? `云端在线（${latency}ms）` : `本地降级（${check.error_type || 'unknown'} ${latency}ms）`
}
const moduleCards = computed(() => [
  { icon: Bot, title: 'Agent 定义与配置', detail: `${manifest.value?.agents?.length || 0} 个角色、工具白名单、记忆和版本契约`, tag: 'ONLINE' },
  { icon: GitBranch, title: '工作流编排', detail: '主干/扩展 DAG、工作流 loop、tool 节点、投票与子流程', tag: 'DAG' },
  { icon: Cpu, title: '智能调度与执行', detail: '能力加权优先级、并行批次、运行态状态机', tag: 'SCHEDULED' },
  { icon: ShieldCheck, title: '结果验证与监控', detail: '独立核验、确定性硬规则、SSE 全量事件', tag: 'GUARDED' },
  { icon: RotateCcw, title: '反馈循环与优化', detail: '工作流内 loop、AI 策略选择、最多三轮后转人工', tag: 'SELF-LOOP' },
])

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [platformManifest, memoryRows] = await Promise.all([api.manifest(), api.memory()])
    manifest.value = platformManifest
    memories.value = memoryRows
  } catch (exc) { error.value = exc.message } finally { loading.value = false }
}
function memoryValue(row) {
  const value = row?.value
  if (typeof value === 'string') return value
  return JSON.stringify(value || {}, null, 0)
}
async function removeMemory(row) {
  if (!isAdmin.value || !window.confirm(`确认删除 ${row.agent_id} 的记忆键“${row.memory_key}”？`)) return
  try {
    await api.deleteMemory(row.agent_id, row.memory_key)
    memories.value = memories.value.filter((item) => !(item.agent_id === row.agent_id && item.memory_key === row.memory_key))
  } catch (exc) { error.value = exc.message }
}
function cacheAgeLabel() {
  const seconds = Math.max(0, Number(checkResult.value?.cache_age_seconds || 0))
  if (seconds < 60) return `${Math.round(seconds)} 秒`
  return `${Math.floor(seconds / 60)} 分钟`
}
async function checkConnection(force = false) {
  checking.value = true
  try { checkResult.value = await api.checkAgentArts(force) } catch (exc) { checkResult.value = { status: 'error', error: exc.message } } finally { checking.value = false }
}
async function registerTool() {
  if (!isAdmin.value) return
  toolSaving.value = true
  toolError.value = ''
  toolMessage.value = ''
  try {
    const schema = JSON.parse(toolForm.value.schema)
    await api.saveTool(toolForm.value.id, {
      name: toolForm.value.name,
      kind: toolForm.value.kind,
      endpoint: toolForm.value.endpoint,
      method: toolForm.value.method,
      description: toolForm.value.description,
      input_schema: schema,
      config: {},
      enabled: true,
    })
    toolMessage.value = `工具 ${toolForm.value.id} 已注册`
    await load()
  } catch (exc) {
    toolError.value = exc.message
  } finally {
    toolSaving.value = false
  }
}

async function removeTool(tool) {
  if (!isAdmin.value || tool.kind === 'builtin') return
  if (!window.confirm(`确认删除工具“${tool.name}”？`)) return
  try {
    await api.deleteTool(tool.id)
    toolMessage.value = '工具已删除'
    await load()
  } catch (exc) { toolError.value = exc.message }
}
onMounted(load)
</script>

<template>
  <div class="studio-page">
    <section class="studio-hero">
      <div>
        <div class="eyebrow">AGENT HARNESS / PLATFORM CONTROL</div>
        <h2>Agent 编排中心</h2>
        <p>围绕赛题一五个核心模块构建。答辩口径：LLM 在确定性安全边界内负责需求优先级、权重、修复策略与方案推荐；方案计算、硬规则核验和评分由确定性内核执行。</p>
        <div class="studio-badges" v-if="manifest">
          <span><span class="live-dot" />{{ manifest.runtime }} runtime</span>
          <span><Database :size="13" />{{ manifest.knowledge.source_count }} 条 RAG 规则</span>
          <span><Wrench :size="13" />{{ manifest.tools.length }} 个注册工具</span>
          <span :class="{ 'studio-badge--warn': !runtimeOK }"><Sparkles :size="13" />LLM {{ runtimeOK ? 'ONLINE' : 'ADAPTER READY' }}</span>
          <span :class="{ 'studio-badge--warn': !agentartsOK || missingAgentIds.length }"><Network :size="13" />AgentArts {{ agentCoverage }}</span>
        </div>
      </div>
      <div class="studio-score-orbit">
        <div class="orbit-inner"><strong>5</strong><span>核心模块</span></div>
        <i class="orbit-a" /><i class="orbit-b" />
      </div>
    </section>

    <div v-if="error" class="inline-error">{{ error }}</div>
    <div v-if="loading" class="loading-panel"><div class="loading-orbit" /><strong>正在读取 Agent 契约与工作流定义</strong></div>

    <template v-else-if="manifest">
      <section class="module-grid">
        <article v-for="item in moduleCards" :key="item.title" class="module-card">
          <div class="module-icon"><component :is="item.icon" :size="19" /></div>
          <span class="module-tag">{{ item.tag }}</span>
          <h3>{{ item.title }}</h3>
          <p>{{ item.detail }}</p>
        </article>
      </section>

      <section class="content-card workflow-card">
        <div class="workflow-head">
          <div><div class="eyebrow">VISUAL WORKFLOW DESIGNER</div><h2>工作流可视化编排</h2><p>支持模板实例化、节点新增、拖拽定位、依赖连线、条件节点、循环节点、子工作流嵌套、版本发布和试运行。</p></div>
          <div class="workflow-actions"><span class="workflow-chip"><GitBranch :size="14" />EDITABLE DAG</span><button class="secondary-action" :disabled="checking" @click="checkConnection(Boolean(checkResult))"><RefreshCw :size="14" :class="{ spin: checking }" />{{ checking ? '检测中…' : (checkResult ? '重新检测' : '检测 AgentArts') }}</button></div>
        </div>
      </section>
      <WorkflowDesigner :user="user" />      <div v-if="checkResult" class="agentarts-check" :class="{ 'agentarts-check--warn': checkResult.status !== 'ok' }">
        <strong>AgentArts 连接检测：{{ checkResult.status }} · 在线率 {{ ((checkResult.online_rate || 0) * 100).toFixed(1) }}%</strong>
        <span v-if="checking">正在并行探测四个 AgentArts 端点，页面仍可继续操作…</span><span v-else-if="checkResult.cached">上次检测 {{ cacheAgeLabel() }}前（缓存） · {{ checkResult.checked_at || '—' }}</span><span v-else>{{ checkResult.enabled ? `端点已配置，检测时间 ${checkResult.checked_at || '—'}` : '尚未配置 AGENTARTS_BASE_URL / API_KEY，当前使用本地确定性回退' }}</span>
      </div>

      <section class="content-card agentarts-coverage-card">
        <div class="workflow-head"><div><div class="eyebrow">AGENTARTS ROLE COVERAGE</div><h2>四智能体云端接入状态</h2><p>每个角色独立配置工作流端点；未配置的角色继续使用本地确定性执行器，不会中断调度。</p></div><span class="workflow-chip">{{ agentCoverage }} CONFIGURED</span></div>
        <div class="agentarts-coverage-grid">
          <article v-for="agent in manifest.agents" :key="agent.id" class="agentarts-role-card" :class="{ 'agentarts-role-card--ready': configuredAgentIds.includes(agent.id) }">
            <div><span class="mono">{{ agent.code }}</span><strong>{{ agent.name }}</strong></div>
            <span class="agentarts-role-state">{{ agentConnectionLabel(agent.id) }}</span>
            <small v-if="agentCheckState(agent.id)">检测：{{ agentCheckLabel(agent.id) }}</small>
          </article>
        </div>
        <div v-if="missingAgentIds.length" class="agentarts-missing-note"><Cpu :size="14" /><span>待接入：{{ missingAgentIds.join('、') }}。代码与回退已就绪，配置对应 /invocations 地址后即可启用。</span></div>
      </section>

      <section class="agent-grid">
        <article v-for="(agent, index) in manifest.agents" :key="agent.id" class="agent-def-card">
          <div class="agent-def-head">
            <div class="agent-def-icon"><component :is="agentIcons[index]" :size="20" /></div>
            <div><span class="mono">{{ agent.code }} · {{ agent.version }}</span><h3>{{ agent.name }}</h3></div>
            <span class="online-chip"><span />{{ agent.status }}</span>
          </div>
          <p>{{ agent.role }}</p>
          <div class="agent-detail-line"><span>推理核心</span><strong>{{ agent.model }}</strong></div>
          <div class="agent-detail-line"><span>记忆</span><strong>{{ agent.memory }}</strong></div>
          <div class="tool-pills"><span v-for="tool in agent.tools" :key="tool">{{ tool }}</span></div>
          <div class="guardrail-box"><ShieldCheck :size="14" /><span v-for="rule in agent.guardrails" :key="rule">{{ rule }}</span></div>
        </article>
      </section>

      <div class="benchmark-note agent-registry-shortcut"><Network :size="14" /><span>动态注册、发现、负载与版本切换请使用顶部“Agent 注册与版本”页签。</span></div>

      <div class="studio-lower-grid">
        <section class="content-card registry-card">
          <div class="workflow-head"><div><div class="eyebrow">TOOL REGISTRY</div><h2>工具与 MCP 接口注册</h2></div><Wrench :size="20" /></div>
          <div class="registry-list">
            <div v-for="tool in manifest.tools" :key="tool.id" class="registry-item">
              <div class="registry-icon"><Wrench :size="15" /></div>
              <div><strong>{{ tool.name }}</strong><span>{{ tool.description }}</span></div>
              <em>{{ tool.protocol }}</em>
              <button v-if="isAdmin && tool.kind !== 'builtin'" class="record-delete" @click="removeTool(tool)">删除</button>
            </div>
          </div>
          <div v-if="isAdmin" class="tool-register-form">
            <div class="workflow-head"><div><div class="eyebrow">ADD TOOL AT RUNTIME</div><h3>现场注册工具</h3></div></div>
            <div class="tool-form-grid">
              <label><span>工具 ID</span><input v-model="toolForm.id" /></label>
              <label><span>名称</span><input v-model="toolForm.name" /></label>
              <label><span>类型</span><select v-model="toolForm.kind"><option value="http">HTTP API</option><option value="mcp">MCP</option></select></label>
              <label><span>方法</span><input v-model="toolForm.method" /></label>
              <label class="full-span"><span>Endpoint</span><input v-model="toolForm.endpoint" /></label>
              <label class="full-span"><span>说明</span><input v-model="toolForm.description" /></label>
            </div>
            <label class="workflow-json-label"><span>Function Calling Schema</span><textarea v-model="toolForm.schema" rows="7" spellcheck="false" /></label>
            <div class="workflow-editor-actions"><button class="primary-action" :disabled="toolSaving" @click="registerTool">{{ toolSaving ? '注册中...' : '注册工具' }}</button><span v-if="toolMessage" class="tool-form-message">{{ toolMessage }}</span><span v-if="toolError" class="tool-form-error">{{ toolError }}</span></div>
            <div class="benchmark-note"><ShieldCheck :size="14" /><span>MCP 服务地址：<code>/api/mcp</code>，支持 initialize、tools/list、tools/call。</span></div>
          </div>
        </section>

        <section class="content-card knowledge-card">
          <div class="workflow-head"><div><div class="eyebrow">RAG KNOWLEDGE BASE</div><h2>证据与知识来源</h2></div><BookOpen :size="20" /></div>
          <p class="knowledge-method">{{ manifest.knowledge.retrieval }} · 引用校验：{{ manifest.knowledge.citation_required ? '开启' : '关闭' }}</p>
          <div class="knowledge-list">
            <div v-for="doc in manifest.knowledge.documents" :key="doc.id" class="knowledge-item">
              <span class="knowledge-id mint">{{ doc.id }}</span>
              <div><strong>{{ doc.title }}</strong><small>{{ doc.category }} · {{ doc.source }}</small></div>
            </div>
          </div>
        </section>
      </div>

      <section class="content-card memory-card">
        <div class="workflow-head"><div><div class="eyebrow">CROSS-RUN MEMORY</div><h2>跨运行记忆</h2><p>记忆来自真实运行，只作为上下文和安全 tie-break，不覆盖库存、道路、运力、时效与核验硬规则。</p></div><History :size="20" /></div>
        <div v-if="memories.length" class="table-wrap">
          <table class="data-table"><thead><tr><th>Agent</th><th>记忆键</th><th>内容</th><th>置信度</th><th>来源运行</th><th>更新时间</th><th v-if="isAdmin">操作</th></tr></thead>
            <tbody><tr v-for="row in memories" :key="`${row.agent_id}-${row.memory_key}`"><td><strong>{{ row.agent_id }}</strong></td><td>{{ row.memory_key }}</td><td class="memory-value">{{ memoryValue(row) }}</td><td>{{ (row.confidence * 100).toFixed(0) }}%</td><td class="mono">{{ row.source_run_id || '历史种子' }}</td><td>{{ row.updated_at }}</td><td v-if="isAdmin"><button class="record-delete" @click="removeMemory(row)"><Trash2 :size="12" />删除</button></td></tr></tbody>
          </table>
        </div>
        <div v-else class="empty-table"><History :size="26" /><strong>暂无跨运行记忆</strong><span>完成一次成功推演后，系统会写入可审计的置信度、阻断历史、修复策略和推荐方案。</span></div>
      </section>

      <section class="content-card score-alignment">
        <div class="workflow-head"><div><div class="eyebrow">EVIDENCE MAPPING</div><h2>赛题能力证据映射</h2></div><Layers3 :size="20" /></div>
        <div class="alignment-grid">
          <div v-for="item in manifest.score_alignment" :key="item.item" class="alignment-item">
            <span><CheckCircle2 :size="18" /></span>
            <div><strong>{{ item.item }}</strong><p>{{ item.evidence }}</p></div>
          </div>
        </div>
      </section>
    </template>
  </div>
</template>
