<script setup>
import { computed, onMounted, ref } from 'vue'
import { Activity, GitBranch, Plus, RefreshCw, ServerCog, ShieldCheck, UserCog } from '@lucide/vue'
import { api } from '../api'

const props = defineProps({ user: { type: Object, default: null } })
const agents = ref([])
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const message = ref('')
const invoking = ref(false)
const invokeForm = ref({ agent_id: '', task: '{\n  "query": "health check"\n}' })
const invokeResult = ref(null)
const isAdmin = computed(() => props.user?.role === 'admin')
const registerForm = ref({ id: '', name: '', role: '', description: '', capabilities: '', version: 'v1', endpoint: '', model: '', prompt_version: 'v1', activate: true })
const versionForm = ref({ agent_id: '', version: '', endpoint: '', model: '', prompt_version: '', activate: true })

async function load() {
  loading.value = true
  error.value = ''
  try {
    agents.value = await api.agents()
    if (!versionForm.value.agent_id && agents.value.length) versionForm.value.agent_id = agents.value[0].id
    if (!invokeForm.value.agent_id && agents.value.length) invokeForm.value.agent_id = agents.value[0].id
  } catch (exc) { error.value = exc.message } finally { loading.value = false }
}

async function registerAgent() {
  saving.value = true
  error.value = ''
  message.value = ''
  try {
    const payload = { ...registerForm.value, capabilities: registerForm.value.capabilities.split(',').map((x) => x.trim()).filter(Boolean) }
    const created = await api.registerAgent(payload)
    message.value = `Agent ${created.name} 已注册并激活版本 ${created.active_version}`
    registerForm.value = { id: '', name: '', role: '', description: '', capabilities: '', version: 'v1', endpoint: '', model: '', prompt_version: 'v1', activate: true }
    await load()
  } catch (exc) { error.value = exc.message } finally { saving.value = false }
}

async function addVersion() {
  saving.value = true
  error.value = ''
  message.value = ''
  try {
    const updated = await api.addAgentVersion(versionForm.value.agent_id, {
      version: versionForm.value.version, endpoint: versionForm.value.endpoint,
      model: versionForm.value.model, prompt_version: versionForm.value.prompt_version,
      activate: versionForm.value.activate,
    })
    message.value = `Agent ${updated.name} 已新增版本 ${versionForm.value.version}`
    versionForm.value = { agent_id: versionForm.value.agent_id, version: '', endpoint: '', model: '', prompt_version: '', activate: true }
    await load()
  } catch (exc) { error.value = exc.message } finally { saving.value = false }
}

async function invokeAgent() {
  invoking.value = true
  error.value = ''
  invokeResult.value = null
  try {
    const task = JSON.parse(invokeForm.value.task)
    invokeResult.value = await api.invokeAgent(invokeForm.value.agent_id, { task })
  } catch (exc) {
    error.value = exc instanceof SyntaxError ? '测试任务必须是合法 JSON' : exc.message
  } finally {
    invoking.value = false
  }
}

async function activate(agent, version) {
  error.value = ''
  try {
    await api.activateAgentVersion(agent.id, version)
    message.value = `Agent ${agent.name} 已切换到版本 ${version}`
    await load()
  } catch (exc) { error.value = exc.message }
}

function loadLabel(metric) {
  if (!metric || !metric.sample_count) return '暂无运行样本'
  return `负载 ${metric.load_score} · 平均 ${metric.average_latency_ms}ms · 成功率 ${(metric.success_rate * 100).toFixed(1)}%`
}

onMounted(load)
</script>
<template>
  <div class="registry-page">
    <section class="content-card registry-hero">
      <div class="workflow-head">
        <div><div class="eyebrow">DYNAMIC AGENT REGISTRY</div><h2>Agent 注册、发现与版本管理</h2><p>注册 Agent 能力、发布和切换版本，并按最近运行样本查看延迟、成功率与负载。</p></div>
        <button class="secondary-action" @click="load"><RefreshCw :size="14" />刷新注册表</button>
      </div>
    </section>

    <div v-if="message" class="import-result">{{ message }}</div>
    <div v-if="error" class="inline-error">{{ error }}</div>

    <section class="registry-agent-grid">
      <article v-for="agent in agents" :key="agent.id" class="registry-agent-card">
        <div class="registry-agent-head"><div class="registry-agent-icon"><ServerCog :size="18" /></div><div><span class="mono">{{ agent.id }}</span><h3>{{ agent.name }}</h3></div><span class="warehouse-state">{{ agent.enabled ? '已启用' : '已停用' }}</span></div>
        <p>{{ agent.description }}</p>
        <div class="registry-agent-meta"><span>当前版本 <strong>{{ agent.active_version || '未激活' }}</strong></span><span>端点 {{ agent.endpoint_configured ? '已配置' : '本地回退' }}</span></div>
        <div class="registry-load"><Activity :size="14" /><span>{{ loadLabel(agent.metrics) }}</span></div>
        <div class="registry-version-list">
          <div v-for="version in agent.versions" :key="version.id" class="registry-version-row">
            <span>{{ version.version }}</span><small>{{ version.model || '未填写模型' }} · {{ version.status }}</small>
            <button v-if="isAdmin && version.status !== 'active'" class="record-edit" @click="activate(agent, version.version)">激活</button>
          </div>
        </div>
        <div class="tool-pills"><span v-for="cap in agent.capabilities" :key="cap">{{ cap }}</span></div>
      </article>
    </section>

    <template v-if="isAdmin">
      <section class="content-card registry-form-card">
        <div class="workflow-head"><div><div class="eyebrow">REGISTER AGENT</div><h2>注册新 Agent</h2></div><UserCog :size="20" /></div>
        <form class="registry-form-grid" @submit.prevent="registerAgent">
          <label><span>Agent ID *</span><input v-model="registerForm.id" placeholder="例如 research_agent" /></label>
          <label><span>名称 *</span><input v-model="registerForm.name" placeholder="例如 科研检索智能体" /></label>
          <label><span>角色/用途 *</span><input v-model="registerForm.role" placeholder="例如 文献检索" /></label>
          <label><span>能力（逗号分隔）</span><input v-model="registerForm.capabilities" placeholder="检索,摘要,引用核验" /></label>
          <label class="full-span"><span>描述</span><input v-model="registerForm.description" placeholder="说明职责、工具权限和边界" /></label>
          <label><span>初始版本 *</span><input v-model="registerForm.version" placeholder="v1" /></label>
          <label><span>调用端点</span><input v-model="registerForm.endpoint" placeholder="https://.../invocations" /></label>
          <label><span>模型</span><input v-model="registerForm.model" placeholder="DeepSeek-V4.1-Flash" /></label>
          <label><span>Prompt 版本</span><input v-model="registerForm.prompt_version" placeholder="prompt-v1" /></label>
          <div class="registry-form-actions full-span"><span><ShieldCheck :size="14" />新 Agent 默认启用</span><button class="primary-action" :disabled="saving"><Plus :size="14" />注册 Agent</button></div>
        </form>
      </section>

      <section class="content-card registry-form-card">
        <div class="workflow-head"><div><div class="eyebrow">VERSION MANAGEMENT</div><h2>新增或切换 Agent 版本</h2></div><GitBranch :size="20" /></div>
        <form class="registry-form-grid" @submit.prevent="addVersion">
          <label><span>Agent *</span><select v-model="versionForm.agent_id"><option v-for="agent in agents" :key="agent.id" :value="agent.id">{{ agent.name }}（{{ agent.id }}）</option></select></label>
          <label><span>版本 *</span><input v-model="versionForm.version" placeholder="v2" /></label>
          <label><span>端点</span><input v-model="versionForm.endpoint" placeholder="https://.../invocations" /></label>
          <label><span>模型</span><input v-model="versionForm.model" placeholder="模型名称" /></label>
          <label><span>Prompt 版本</span><input v-model="versionForm.prompt_version" placeholder="prompt-v2" /></label>
          <label class="check-label"><input v-model="versionForm.activate" type="checkbox" />创建后立即激活</label>
          <div class="registry-form-actions full-span"><span><GitBranch :size="14" />版本切换会立即影响后续运行</span><button class="primary-action" :disabled="saving || !versionForm.agent_id"><Plus :size="14" />新增版本</button></div>
        </form>
      </section>

      <section class="content-card registry-form-card">
        <div class="workflow-head"><div><div class="eyebrow">AGENT INVOCATION TEST</div><h2>直接测试已注册 Agent</h2><p>使用激活版本的 /invocations 端点执行一个小任务，验证注册结果确实可调用。</p></div><ServerCog :size="20" /></div>
        <form class="registry-form-grid" @submit.prevent="invokeAgent">
          <label><span>Agent *</span><select v-model="invokeForm.agent_id"><option v-for="agent in agents" :key="agent.id" :value="agent.id">{{ agent.name }}（{{ agent.id }}）</option></select></label>
          <label class="full-span"><span>任务 JSON *</span><textarea v-model="invokeForm.task" rows="4" placeholder='{"query":"health check"}' /></label>
          <div class="registry-form-actions full-span"><span>调用结果仅用于注册验证，不会修改业务数据</span><button class="primary-action" :disabled="invoking || !invokeForm.agent_id"><Activity :size="14" />{{ invoking ? '调用中...' : '执行测试' }}</button></div>
        </form>
        <div v-if="invokeResult" class="agent-invoke-result"><strong>调用成功</strong><pre>{{ JSON.stringify(invokeResult, null, 2) }}</pre></div>
      </section>
    </template>
  </div>
</template>
