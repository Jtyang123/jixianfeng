<script setup>
import { computed, onMounted, ref } from 'vue'
import {
  Bot, BookOpen, BrainCircuit, CheckCircle2, Cpu, Database, GitBranch,
  Layers3, Network, RefreshCw, RotateCcw, ShieldCheck, Sparkles, Wrench,
} from 'lucide-vue-next'
import { api } from '../api'

defineProps({ user: { type: Object, default: null } })

const manifest = ref(null)
const loading = ref(true)
const error = ref('')
const checking = ref(false)
const checkResult = ref(null)

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
  const check = checkResult.value?.checks?.find((item) => item.agent_id === agentId)
  if (!check) return ''
  return check.ok ? 'OK' : 'FAILED'
}
const moduleCards = computed(() => [
  { icon: Bot, title: 'Agent 定义与配置', detail: `${manifest.value?.agents?.length || 0} 个角色、工具白名单、记忆和版本契约`, tag: 'ONLINE' },
  { icon: GitBranch, title: '工作流编排', detail: '并行入口、条件分支、核验门控与 PDCA 回环', tag: 'DAG' },
  { icon: Cpu, title: '智能调度与执行', detail: '能力加权优先级、并行批次、运行态状态机', tag: 'SCHEDULED' },
  { icon: ShieldCheck, title: '结果验证与监控', detail: '独立核验、确定性硬规则、SSE 全量事件', tag: 'GUARDED' },
  { icon: RotateCcw, title: '反馈循环与优化', detail: '结构化问题回传，最多两轮重算后转人工', tag: 'SELF-LOOP' },
])

async function load() {
  loading.value = true
  try { manifest.value = await api.manifest() } catch (exc) { error.value = exc.message } finally { loading.value = false }
}
async function checkConnection() {
  checking.value = true
  try { checkResult.value = await api.checkAgentArts() } catch (exc) { checkResult.value = { status: 'error', error: exc.message } } finally { checking.value = false }
}
onMounted(load)
</script>

<template>
  <div class="studio-page">
    <section class="studio-hero">
      <div>
        <div class="eyebrow">AGENT HARNESS / PLATFORM CONTROL</div>
        <h2>Agent 编排中心</h2>
        <p>围绕赛题一五个核心模块构建：定义、编排、调度、执行验证、反馈优化。</p>
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
          <div><div class="eyebrow">WORKFLOW GRAPH</div><h2>可视化工作流与反馈回路</h2><p>主链路顺序执行，需求与 RAG 检索并行，核验条件分支驱动退回复算。</p></div>
          <div class="workflow-actions"><span class="workflow-chip"><GitBranch :size="14" />CONDITION + LOOP</span><button class="secondary-action" :disabled="checking" @click="checkConnection"><RefreshCw :size="14" :class="{ spin: checking }" />检测 AgentArts</button></div>
        </div>
        <div class="workflow-canvas">
          <div class="workflow-row">
            <div class="flow-node flow-node--trigger"><span>TRIGGER</span><strong>事件确认</strong></div>
            <div class="flow-arrow">→</div>
            <div class="flow-parallel">
              <div class="flow-node flow-node--agent"><span>A01</span><strong>需求研判</strong></div>
              <div class="flow-node flow-node--tool"><span>RAG</span><strong>规则检索</strong></div>
            </div>
            <div class="flow-arrow">→</div>
            <div class="flow-node flow-node--agent"><span>A02</span><strong>资源匹配</strong></div>
            <div class="flow-arrow">→</div>
            <div class="flow-node flow-node--verify"><span>A03</span><strong>独立核验</strong></div>
            <div class="flow-arrow">→</div>
            <div class="flow-node flow-node--decision"><span>GATE</span><strong>通过？</strong></div>
          </div>
          <div class="loop-branch">
            <div class="loop-line loop-line--fail"><span>FAIL → 结构化反馈</span></div>
            <div class="flow-node flow-node--loop"><RotateCcw :size="15" /><strong>退回重算</strong><small>最多 2 轮</small></div>
            <div class="loop-line loop-line--return"><span>RETURN TO A02</span></div>
          </div>
          <div class="pass-branch">
            <div class="flow-line" />
            <span class="flow-pass-label">PASS</span>
            <div class="flow-node flow-node--human"><span>HUMAN</span><strong>人工审批</strong></div>
            <div class="flow-arrow">→</div>
            <div class="flow-node flow-node--delivery"><span>A04</span><strong>方案交付</strong></div>
          </div>
        </div>
      </section>
      <div v-if="checkResult" class="agentarts-check" :class="{ 'agentarts-check--warn': checkResult.status !== 'ok' }">
        <strong>AgentArts 连接检测：{{ checkResult.status }}</strong>
        <span>{{ checkResult.enabled ? '端点已配置，可执行 /invocations 调用' : '尚未配置 AGENTARTS_BASE_URL / API_KEY，当前使用本地确定性回退' }}</span>
      </div>

      <section class="content-card agentarts-coverage-card">
        <div class="workflow-head"><div><div class="eyebrow">AGENTARTS ROLE COVERAGE</div><h2>四智能体云端接入状态</h2><p>每个角色独立配置工作流端点；未配置的角色继续使用本地确定性执行器，不会中断调度。</p></div><span class="workflow-chip">{{ agentCoverage }} CONFIGURED</span></div>
        <div class="agentarts-coverage-grid">
          <article v-for="agent in manifest.agents" :key="agent.id" class="agentarts-role-card" :class="{ 'agentarts-role-card--ready': configuredAgentIds.includes(agent.id) }">
            <div><span class="mono">{{ agent.code }}</span><strong>{{ agent.name }}</strong></div>
            <span class="agentarts-role-state">{{ agentConnectionLabel(agent.id) }}</span>
            <small v-if="agentCheckState(agent.id)">检测：{{ agentCheckState(agent.id) }}</small>
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

      <div class="studio-lower-grid">
        <section class="content-card registry-card">
          <div class="workflow-head"><div><div class="eyebrow">TOOL REGISTRY</div><h2>工具与 MCP 接口注册</h2></div><Wrench :size="20" /></div>
          <div class="registry-list">
            <div v-for="tool in manifest.tools" :key="tool.id" class="registry-item">
              <div class="registry-icon"><Wrench :size="15" /></div>
              <div><strong>{{ tool.name }}</strong><span>{{ tool.description }}</span></div>
              <em>{{ tool.protocol }}</em>
            </div>
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

      <section class="content-card score-alignment">
        <div class="workflow-head"><div><div class="eyebrow">RUBRIC ALIGNMENT</div><h2>初赛评分标准对齐</h2></div><Layers3 :size="20" /></div>
        <div class="alignment-grid">
          <div v-for="item in manifest.score_alignment" :key="item.item" class="alignment-item">
            <span>{{ item.score }}<small>分</small></span>
            <div><strong>{{ item.item }}</strong><p>{{ item.evidence }}</p></div>
          </div>
        </div>
      </section>
    </template>
  </div>
</template>
