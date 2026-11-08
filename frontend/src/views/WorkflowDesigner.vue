<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { CheckCircle2, GitBranch, Play, Plus, RefreshCw, Save, ShieldCheck, Trash2 } from '@lucide/vue'
import { api } from '../api'

const props = defineProps({ user: { type: Object, default: null } })
const workflows = ref([])
const selectedId = ref('post-verification-review')
const loading = ref(true)
const saving = ref(false)
const validating = ref(false)
const running = ref(false)
const error = ref('')
const message = ref('')
const validation = ref(null)
const runResult = ref(null)
const canvasRef = ref(null)
const visualNodes = ref([])
const visualError = ref('')
const dragging = ref(null)
const connecting = ref(null)
const testContext = ref(JSON.stringify({
  scenario: { name: '工作流试运行场景' },
  demand: { items: [] },
}, null, 2))
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
    enabled: false,
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

const visualEdges = computed(() => {
  const byId = new Map(visualNodes.value.map((node) => [node.id, node]))
  const edges = []
  for (const node of visualNodes.value) {
    for (const dependency of node.depends_on || []) {
      const source = byId.get(dependency)
      if (!source) continue
      const x1 = source.x + 184
      const y1 = source.y + 42
      const x2 = node.x
      const y2 = node.y + 42
      const bend = Math.max(60, Math.abs(x2 - x1) * 0.45)
      edges.push({
        id: `${source.id}-${node.id}`,
        path: `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`,
      })
    }
  }
  return edges
})

const connectionPath = computed(() => {
  if (!connecting.value) return ''
  const source = connecting.value.source
  const x1 = source.x + 184
  const y1 = source.y + 42
  const x2 = connecting.value.x
  const y2 = connecting.value.y
  const bend = Math.max(40, Math.abs(x2 - x1) * 0.4)
  return `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`
})

function syncVisualNodes(text) {
  try {
    const definition = JSON.parse(text)
    if (!Array.isArray(definition.nodes)) throw new Error('节点列表不存在')
    visualNodes.value = definition.nodes.map((node, index) => ({
      ...node,
      depends_on: Array.isArray(node.depends_on) ? node.depends_on : [],
      x: Number(node.ui?.x ?? 48 + (index % 4) * 230),
      y: Number(node.ui?.y ?? 54 + Math.floor(index / 4) * 130),
    }))
    visualError.value = ''
  } catch (exc) {
    visualNodes.value = []
    visualError.value = `画布无法解析 JSON：${exc.message}`
  }
}

watch(() => form.value.json, syncVisualNodes, { immediate: true })

function canvasPoint(event) {
  const rect = canvasRef.value.getBoundingClientRect()
  return {
    x: Math.max(12, Math.min(940, event.clientX - rect.left + canvasRef.value.scrollLeft)),
    y: Math.max(12, Math.min(480, event.clientY - rect.top + canvasRef.value.scrollTop)),
  }
}

function writeNodePositions() {
  try {
    const definition = parseDefinition()
    const positions = new Map(visualNodes.value.map((node) => [node.id, { x: Math.round(node.x), y: Math.round(node.y) }]))
    definition.nodes = definition.nodes.map((node) => ({ ...node, ui: positions.get(node.id) || node.ui }))
    form.value.json = JSON.stringify(definition, null, 2)
  } catch (exc) {
    visualError.value = exc.message
  }
}

function startNodeDrag(node, event) {
  if (!isAdmin.value || event.button !== 0) return
  const point = canvasPoint(event)
  dragging.value = { id: node.id, dx: point.x - node.x, dy: point.y - node.y }
  document.addEventListener('pointermove', moveNode)
  document.addEventListener('pointerup', stopNodeDrag, { once: true })
}

function moveNode(event) {
  if (!dragging.value) return
  const node = visualNodes.value.find((item) => item.id === dragging.value.id)
  if (!node) return
  const point = canvasPoint(event)
  node.x = Math.max(12, Math.min(900, point.x - dragging.value.dx))
  node.y = Math.max(12, Math.min(440, point.y - dragging.value.dy))
}

function stopNodeDrag() {
  document.removeEventListener('pointermove', moveNode)
  dragging.value = null
  writeNodePositions()
}

function startConnection(node, event) {
  if (!isAdmin.value || event.button !== 0) return
  event.preventDefault()
  const point = canvasPoint(event)
  connecting.value = { source: node, x: point.x, y: point.y }
  document.addEventListener('pointermove', moveConnection)
  document.addEventListener('pointerup', cancelConnection, { once: true })
}

function moveConnection(event) {
  if (!connecting.value) return
  const point = canvasPoint(event)
  connecting.value.x = point.x
  connecting.value.y = point.y
}

function cancelConnection() {
  document.removeEventListener('pointermove', moveConnection)
  connecting.value = null
}

function finishConnection(target, event) {
  event.preventDefault()
  event.stopPropagation()
  if (!connecting.value || connecting.value.source.id === target.id) {
    cancelConnection()
    return
  }
  try {
    const definition = parseDefinition()
    const targetNode = definition.nodes.find((node) => node.id === target.id)
    if (targetNode) {
      const dependencies = new Set(targetNode.depends_on || [])
      dependencies.add(connecting.value.source.id)
      targetNode.depends_on = [...dependencies]
      form.value.json = JSON.stringify(definition, null, 2)
      message.value = `已连接 ${connecting.value.source.id} → ${target.id}，请重新校验 DAG`
    }
  } catch (exc) {
    visualError.value = exc.message
  } finally {
    cancelConnection()
  }
}

function loadCoreTemplate() {
  form.value.name = '默认主干工作流'
  form.value.description = '需求研判 → 资源匹配 → 独立核验，可替换核验节点或插入扩展节点。'
  form.value.stage = 'core'
  form.value.json = JSON.stringify({
    nodes: [
      { id: 'A01', type: 'agent', agent_id: 'demand_agent', depends_on: [], priority: 96, ui: { x: 48, y: 90 } },
      { id: 'A02', type: 'agent', agent_id: 'matching_agent', depends_on: ['A01'], priority: 88, ui: { x: 278, y: 90 } },
      { id: 'A03', type: 'agent', agent_id: 'verifier_agent', depends_on: ['A02'], priority: 99, ui: { x: 508, y: 90 } },
    ],
  }, null, 2)
  validation.value = null
  runResult.value = null
  message.value = '已载入主干模板，可拖动节点或从输出口拖线到另一节点输入口'
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

async function runWorkflow() {
  if (!isAdmin.value) return
  running.value = true
  error.value = ''
  message.value = ''
  runResult.value = null
  try {
    let context
    try {
      context = JSON.parse(testContext.value)
    } catch (_) {
      throw new Error('试运行上下文 JSON 格式不正确')
    }
    const saved = await save()
    if (!saved) throw new Error(error.value || '工作流保存失败')
    runResult.value = await api.runWorkflow(selectedId.value, context, 4)
    message.value = `试运行完成：${runResult.value.completed.length} 个节点执行成功`
  } catch (exc) {
    error.value = exc.message
  } finally {
    running.value = false
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
    return saved
  } catch (exc) {
    error.value = exc.message
    return null
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
        <button v-if="isAdmin" class="secondary-action" @click="loadCoreTemplate"><GitBranch :size="14" />载入主干模板</button><button v-if="isAdmin" class="primary-action" @click="newWorkflow"><Plus :size="14" />新建工作流</button>
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
          <label><span>阶段</span><select v-model="form.stage" :disabled="!isAdmin"><option value="core">主干工作流（高级）</option><option value="after_demand">需求研判后</option><option value="after_matching">资源匹配后</option><option value="after_verification">独立核验后</option><option value="post_verification">核验后扩展审查</option><option value="standalone">独立工作流</option></select></label>
          <label class="check-label"><input v-model="form.enabled" type="checkbox" :disabled="!isAdmin" />启用工作流</label>
          <label class="full-span"><span>说明</span><input v-model="form.description" :disabled="!isAdmin" /></label>
        </div>
        <div class="workflow-canvas-shell">
          <div class="workflow-canvas-toolbar"><span>节点画布</span><small>拖动卡片调整位置；从右侧输出口拖到另一节点左侧输入口建立依赖</small></div>
          <div ref="canvasRef" class="workflow-canvas">
            <svg class="workflow-canvas-edges" viewBox="0 0 960 520" preserveAspectRatio="none">
              <defs><marker id="workflow-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z" fill="currentColor" /></marker></defs>
              <path v-for="edge in visualEdges" :key="edge.id" :d="edge.path" class="workflow-edge" marker-end="url(#workflow-arrow)" />
              <path v-if="connectionPath" :d="connectionPath" class="workflow-edge workflow-edge--active" />
            </svg>
            <div v-for="node in visualNodes" :key="node.id" class="workflow-node-card" :class="{ 'workflow-node-card--dragging': dragging?.id === node.id }" :style="{ left: `${node.x}px`, top: `${node.y}px` }" @pointerdown.stop="startNodeDrag(node, $event)">
              <span class="workflow-node-port workflow-node-port--input" title="输入依赖" @pointerup.stop="finishConnection(node, $event)" />
              <div class="workflow-node-type">{{ node.type }}</div>
              <strong>{{ node.id }}</strong>
              <small>{{ node.agent_id || node.workflow_id || '条件节点' }}</small>
              <span class="workflow-node-port workflow-node-port--output" title="拖拽建立依赖" @pointerdown.stop="startConnection(node, $event)" />
            </div>
          </div>
          <div v-if="visualError" class="inline-error">{{ visualError }}</div>
        </div>        <label class="workflow-json-label"><span>工作流定义 JSON</span><textarea v-model="form.json" rows="22" spellcheck="false" :readonly="!isAdmin" /></label>
        <label class="workflow-json-label"><span>试运行上下文 JSON</span><textarea v-model="testContext" rows="8" spellcheck="false" :readonly="!isAdmin" /></label>
        <div class="workflow-editor-actions">
          <button class="secondary-action" :disabled="validating" @click="validate"><ShieldCheck :size="14" />{{ validating ? '校验中...' : '校验 DAG' }}</button>
          <button v-if="isAdmin" class="secondary-action" :disabled="running" @click="runWorkflow"><Play :size="14" />{{ running ? '试运行中...' : '保存并试运行' }}</button>
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

    <section v-if="runResult" class="content-card workflow-preview-card">
      <div class="workflow-head"><div><div class="eyebrow">WORKFLOW TEST RUN</div><h2>试运行结果</h2><p>任务已真实写入 workflow_runs 与 workflow_node_runs。</p></div><CheckCircle2 :size="18" /></div>
      <div class="diagnostic-summary">
        <article :class="runResult.status === 'success' ? 'diagnostic-pass' : 'diagnostic-fail'"><span>运行状态</span><strong>{{ runResult.status === 'success' ? '成功' : '失败' }}</strong><small>{{ runResult.run_id }}</small></article>
        <article><span>完成节点</span><strong>{{ runResult.metrics.completed_nodes }}</strong><small>跳过 {{ runResult.metrics.skipped_nodes }} · 失败 {{ runResult.metrics.failed_nodes }}</small></article>
        <article><span>峰值并发</span><strong>{{ runResult.metrics.peak_concurrency }}</strong><small>试运行最大 Worker 并发</small></article>
        <article><span>端到端耗时</span><strong>{{ runResult.metrics.end_to_end_ms }} ms</strong><small>{{ runResult.completed.join(' → ') }}</small></article>
      </div>
    </section>
  </div>
</template>
