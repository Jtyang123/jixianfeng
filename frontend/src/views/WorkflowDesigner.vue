<script setup>
import { computed, onMounted, ref } from 'vue'
import { CheckCircle2, GitBranch, Plus, RefreshCw, Save, ShieldCheck, Trash2 } from '@lucide/vue'
import { api } from '../api'

const props = defineProps({ user: { type: Object, default: null } })
const workflows = ref([])
const selectedId = ref('post-verification-review')
const loading = ref(true)
const saving = ref(false)
const validating = ref(false)
const error = ref('')
const message = ref('')
const validation = ref(null)
const form = ref({
  name: '核验后扩展审查',
  description: '在独立核验通过后运行动态注册 Agent 或子工作流。',
  stage: 'post_verification',
  enabled: true,
  json: JSON.stringify({
    nodes: [
      { id: 'A05', type: 'agent', agent_id: 'risk_extension', depends_on: [], priority: 80, on_error: 'continue' },
    ],
  }, null, 2),
})
const isAdmin = computed(() => props.user?.role === 'admin')

async function load() {
  loading.value = true
  error.value = ''
  try {
    workflows.value = await api.workflows()
  } catch (exc) {
    error.value = exc.message
  } finally {
    loading.value = false
  }
}

function newWorkflow() {
  selectedId.value = 'post-verification-review'
  form.value = {
    name: '核验后扩展审查',
    description: '在独立核验通过后运行动态注册 Agent 或子工作流。',
    stage: 'post_verification',
    enabled: true,
    json: JSON.stringify({
      nodes: [
        { id: 'A05', type: 'agent', agent_id: 'risk_extension', depends_on: [], priority: 80, on_error: 'continue' },
      ],
    }, null, 2),
  }
  validation.value = null
  message.value = ''
}

function selectWorkflow(item) {
  selectedId.value = item.id
  form.value = {
    name: item.name,
    description: item.description,
    stage: item.stage,
    enabled: item.enabled,
    json: JSON.stringify(item.definition, null, 2),
  }
  validation.value = null
  message.value = ''
  error.value = ''
}

function parseDefinition() {
  try {
    return JSON.parse(form.value.json)
  } catch (_) {
    throw new Error('工作流 JSON 格式不正确')
  }
}

async function validate() {
  validating.value = true
  error.value = ''
  message.value = ''
  try {
    validation.value = await api.validateWorkflow(parseDefinition())
    message.value = `校验通过，共 ${validation.value.execution_order.length} 个节点`
  } catch (exc) {
    validation.value = null
    error.value = exc.message
  } finally {
    validating.value = false
  }
}

async function save() {
  if (!isAdmin.value) return
  saving.value = true
  error.value = ''
  message.value = ''
  try {
    const saved = await api.saveWorkflow(selectedId.value, {
      name: form.value.name,
      description: form.value.description,
      stage: form.value.stage,
      enabled: form.value.enabled,
      definition: parseDefinition(),
    })
    message.value = `工作流已保存为 v${saved.version}`
    await load()
  } catch (exc) {
    error.value = exc.message
  } finally {
    saving.value = false
  }
}

async function removeWorkflow(item) {
  if (!isAdmin.value) return
  if (!window.confirm(`确认删除工作流“${item.name}”？`)) return
  try {
    await api.deleteWorkflow(item.id)
    if (selectedId.value === item.id) newWorkflow()
    await load()
    message.value = '工作流已删除'
  } catch (exc) { error.value = exc.message }
}

onMounted(load)
</script>

<template>
  <div class="workflow-designer-page">
    <section class="content-card workflow-designer-head">
      <div>
        <div class="eyebrow">CODE-BASED WORKFLOW DESIGN</div>
        <h2>代码化工作流设计器</h2>
        <p>通过 JSON 定义 Agent 节点、依赖关系、并行分支、条件节点、重试和子工作流。发布后会进入核验后的扩展编排阶段。</p>
      </div>
      <div class="workflow-designer-actions">
        <button class="secondary-action" @click="load"><RefreshCw :size="14" />刷新</button>
        <button v-if="isAdmin" class="primary-action" @click="newWorkflow"><Plus :size="14" />新建工作流</button>
      </div>
    </section>

    <div v-if="error" class="inline-error">{{ error }}</div>
    <div v-if="message" class="import-result"><CheckCircle2 :size="16" />{{ message }}</div>

    <div class="workflow-designer-grid">
      <section class="content-card workflow-list-card">
        <div class="workflow-head"><div><div class="eyebrow">WORKFLOW VERSIONS</div><h2>已发布工作流</h2></div><GitBranch :size="18" /></div>
        <div v-if="loading" class="loading-panel"><div class="loading-orbit" /><strong>正在加载工作流</strong></div>
        <div v-else class="workflow-definition-list">
          <button v-for="item in workflows" :key="item.id" :class="{ active: selectedId === item.id }" @click="selectWorkflow(item)">
            <span><strong>{{ item.name }}</strong><small>{{ item.id }} · v{{ item.version }}</small></span>
            <em>{{ item.stage }}</em>
          </button>
          <div v-if="!workflows.length" class="empty-table"><GitBranch :size="26" /><strong>暂无已发布工作流</strong><span>新建并保存第一个工作流。</span></div>
        </div>
      </section>

      <section class="content-card workflow-editor-card">
        <div class="workflow-editor-meta">
          <label><span>工作流 ID</span><input v-model="selectedId" :disabled="!isAdmin" /></label>
          <label><span>名称</span><input v-model="form.name" :disabled="!isAdmin" /></label>
          <label><span>阶段</span><select v-model="form.stage" :disabled="!isAdmin"><option value="post_verification">核验后扩展审查</option><option value="standalone">独立工作流</option></select></label>
          <label class="check-label"><input v-model="form.enabled" type="checkbox" :disabled="!isAdmin" />启用工作流</label>
          <label class="full-span"><span>说明</span><input v-model="form.description" :disabled="!isAdmin" /></label>
        </div>
        <label class="workflow-json-label"><span>工作流定义 JSON</span><textarea v-model="form.json" rows="22" spellcheck="false" :readonly="!isAdmin" /></label>
        <div class="workflow-editor-actions">
          <button class="secondary-action" :disabled="validating" @click="validate"><ShieldCheck :size="14" />{{ validating ? '校验中...' : '校验 DAG' }}</button>
          <button v-if="isAdmin" class="primary-action" :disabled="saving" @click="save"><Save :size="14" />{{ saving ? '保存中...' : '保存并发布' }}</button>
        </div>
      </section>
    </div>

    <section v-if="workflows.length" class="content-card workflow-manage-card">
      <div class="workflow-head"><div><div class="eyebrow">PUBLISHED VERSIONS</div><h2>版本管理</h2></div></div>
      <div class="workflow-version-rows">
        <div v-for="item in workflows" :key="item.id"><strong>{{ item.name }}</strong><span>{{ item.id }} · v{{ item.version }} · {{ item.enabled ? '启用' : '停用' }}</span><button v-if="isAdmin" class="record-delete" @click="removeWorkflow(item)"><Trash2 :size="12" />删除</button></div>
      </div>
    </section>

    <section v-if="validation" class="content-card workflow-preview-card">
      <div class="workflow-head"><div><div class="eyebrow">TOPOLOGICAL PREVIEW</div><h2>拓扑执行顺序</h2></div><CheckCircle2 :size="18" /></div>
      <div class="workflow-order-list">
        <article v-for="(nodeId, index) in validation.execution_order" :key="nodeId">
          <span>{{ index + 1 }}</span><strong>{{ nodeId }}</strong>
          <small>{{ validation.definition.nodes.find((node) => node.id === nodeId)?.type }}</small>
        </article>
      </div>
    </section>
  </div>
</template>
