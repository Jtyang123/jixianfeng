<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { CheckCircle2, GitBranch, Layers3, Play, Plus, RefreshCw, Save, ShieldCheck, Trash2 } from '@lucide/vue'
import { api } from '../api'
import { addWorkflowNode, applyVisualPositions, computeLoopZones, connectWorkflowNodes, findNodeByVisualId, flattenVisualNodes } from '../utils/workflowCanvas.js'

const props = defineProps({ user: { type: Object, default: null } })
const workflows = ref([])
const templates = ref([])
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
const agents = ref([])
const tools = ref([])
const showNodeForm = ref(false)
const nodeError = ref('')
const nodeDraft = ref({
  id: '', type: 'agent', agent_id: '', tool_id: '', workflow_id: '', loop_id: '',
  condition_field: 'event.ready', condition_value: 'true',
})
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
    const [workflowRows, agentRows, toolRows, templateRows] = await Promise.all([
      api.workflows(), api.agents(), api.tools(), api.workflowTemplates(),
    ])
    workflows.value = workflowRows
    agents.value = agentRows
    tools.value = toolRows
    templates.value = templateRows
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
  const byId = new Map(visualNodes.value.map((node) => [node.visualId, node]))
  const edges = []
  for (const node of visualNodes.value) {
    for (const dependency of node.depends_on || []) {
      const source = byId.get(node.scopePath ? `${node.scopePath}/${dependency}` : dependency)
      if (!source) continue
      const x1 = source.x + 184
      const y1 = source.y + 42
      const x2 = node.x
      const y2 = node.y + 42
      const bend = Math.max(60, Math.abs(x2 - x1) * 0.45)
      edges.push({ id: `${source.visualId}-${node.visualId}`, path: `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}` })
    }
  }
  return edges
})

const loopZones = computed(() => computeLoopZones(visualNodes.value))
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
    visualNodes.value = flattenVisualNodes(definition.nodes)
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
    applyVisualPositions(definition.nodes, visualNodes.value)
    form.value.json = JSON.stringify(definition, null, 2)
  } catch (exc) { visualError.value = exc.message }
}
function startNodeDrag(node, event) {
  if (!isAdmin.value || event.button !== 0) return
  const point = canvasPoint(event)
  dragging.value = { visualId: node.visualId, dx: point.x - node.x, dy: point.y - node.y }
  document.addEventListener('pointermove', moveNode)
  document.addEventListener('pointerup', stopNodeDrag, { once: true })
}

function moveNode(event) {
  if (!dragging.value) return
  const node = visualNodes.value.find((item) => item.visualId === dragging.value.visualId)
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
  if (!connecting.value || connecting.value.source.visualId === target.visualId) {
    cancelConnection()
    return
  }
  if (connecting.value.source.scopePath !== target.scopePath) {
    visualError.value = '只能在同一层级或同一个循环体内建立依赖'
    cancelConnection()
    return
  }
  try {
    const definition = parseDefinition()
    const connected = connectWorkflowNodes(definition, connecting.value.source.visualId, target.visualId)
    form.value.json = JSON.stringify(connected.definition, null, 2)
    message.value = `已连接 ${connected.source.id} → ${connected.target.id}，请重新校验 DAG`
  } catch (exc) {
    visualError.value = exc.message
  } finally {
    cancelConnection()
  }
}

const loopOptions = computed(() => visualNodes.value.filter((node) => node.type === 'loop'))

function openNodeForm() {
  nodeError.value = ''
  nodeDraft.value = {
    id: '', type: 'agent', agent_id: agents.value[0]?.id || '', tool_id: tools.value[0]?.id || '',
    workflow_id: workflows.value[0]?.id || '', loop_id: '', condition_field: 'event.ready', condition_value: 'true',
  }
  showNodeForm.value = true
}

function addNode() {
  nodeError.value = ''
  const draft = nodeDraft.value
  const id = draft.id.trim()
  if (!/^[A-Za-z0-9_-]{2,40}$/.test(id)) {
    nodeError.value = '节点 ID 需为 2–40 位字母、数字、下划线或短横线'
    return
  }
  try {
    const definition = parseDefinition()
    const result = addWorkflowNode(definition, draft, visualNodes.value)
    const scopePath = result.scopePath
    form.value.json = JSON.stringify(definition, null, 2)
    showNodeForm.value = false
    validation.value = null
    message.value = `已新增节点 ${id}${scopePath ? `（循环 ${scopePath}）` : ''}`
  } catch (exc) {
    nodeError.value = exc.message
  }
}
function loadCoreTemplate() {
  form.value.name = '默认主干工作流'
  form.value.description = '需求研判 → 资源匹配 → 独立核验，可替换核验节点或插入扩展节点。'
  form.value.stage = 'core'
  form.value.json = JSON.stringify({
    nodes: [
      { id: 'A01', type: 'agent', agent_id: 'demand_agent', depends_on: [], priority: 96, ui: { x: 28, y: 80 } },
      { id: 'INVENTORY_CHECK', type: 'tool', tool_id: 'inventory_query', arguments: {}, depends_on: ['A01'], priority: 94, ui: { x: 250, y: 80 } },
      {
        id: 'SOLVE_LOOP', type: 'loop', depends_on: ['INVENTORY_CHECK'], priority: 92, max_iterations: 3,
        until: { field: 'verification.verdict', operator: 'eq', value: 'pass' }, on_exhausted: 'human_escalation',
        ui: { x: 472, y: 80 },
        body: { nodes: [
          { id: 'A02', type: 'agent', agent_id: 'matching_agent', depends_on: [], priority: 88 },
          { id: 'A03', type: 'agent', agent_id: 'verifier_agent', depends_on: ['A02'], priority: 99 },
          { id: 'REWORK', type: 'agent', agent_id: 'rework_policy', depends_on: ['A03'], priority: 97, when: { field: 'verification.verdict', operator: 'eq', value: 'rework' } },
        ] },
      },
    ],
  }, null, 2)
  validation.value = null
  runResult.value = null
  message.value = '已载入主干模板，可拖动节点或从输出口拖线到另一节点输入口'
}
async function useTemplate(template) {
  if (!isAdmin.value) return
  const requestedName = window.prompt('实例化后的工作流名称', `${template.name} · 实例`)
  if (requestedName === null) return
  try {
    const created = await api.instantiateWorkflowTemplate(template.id, { name: requestedName || undefined })
    await load()
    selectWorkflow(created)
    message.value = `模板已实例化为 ${created.id}，可继续修改并发布`
  } catch (exc) {
    error.value = exc.message
  }
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
        <div class="eyebrow">VISUAL + CODE WORKFLOW DESIGN</div>
        <h2>可视化与代码化工作流设计器</h2>
        <p>通过节点画布或 JSON 定义 Agent、工具、条件、循环、并行与子工作流。主干和扩展工作流都可在同一引擎中试运行。</p>
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
        <div class="workflow-head"><div><div class="eyebrow">REUSABLE TEMPLATES</div><h2>可复用模板库</h2><p>模板为只读种子，实例化后生成可编辑、可发布的工作流。</p></div><Layers3 :size="18" /></div>
        <div v-if="templates.length" class="workflow-template-list">
          <article v-for="template in templates" :key="template.id">
            <div><strong>{{ template.name }}</strong><small>{{ template.id }} · {{ template.stage }}</small></div>
            <p>{{ template.description }}</p>
            <button class="secondary-action" :disabled="!isAdmin" @click="useTemplate(template)"><Plus :size="12" />实例化</button>
          </article>
        </div>
        <div v-else class="empty-table"><Layers3 :size="24" /><strong>暂无模板</strong><span>管理员初始化后将显示内置模板。</span></div>
        <div class="workflow-head workflow-list-divider"><div><div class="eyebrow">WORKFLOW VERSIONS</div><h2>已发布工作流</h2></div><GitBranch :size="18" /></div>
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
          <div class="workflow-canvas-toolbar"><span>节点画布</span><small>循环体已展开；拖动节点调整位置，从输出口拖线到输入口建立依赖</small><button v-if="isAdmin" class="secondary-action" @click="openNodeForm"><Plus :size="13" />新增节点</button></div>
          <div v-if="showNodeForm" class="workflow-node-form">
            <label><span>节点 ID</span><input v-model="nodeDraft.id" placeholder="A06" /></label>
            <label><span>类型</span><select v-model="nodeDraft.type"><option value="agent">Agent</option><option value="tool">工具</option><option value="condition">条件</option><option value="subworkflow">子工作流</option><option value="loop">循环</option></select></label>
            <label><span>加入位置</span><select v-model="nodeDraft.loop_id"><option value="">主工作流</option><option v-for="loop in loopOptions" :key="loop.visualId" :value="loop.visualId">循环体 {{ loop.visualId }}</option></select></label>
            <label v-if="nodeDraft.type === 'agent'"><span>Agent</span><select v-model="nodeDraft.agent_id"><option v-for="agent in agents" :key="agent.id" :value="agent.id">{{ agent.name }} · {{ agent.id }}</option></select></label>
            <label v-if="nodeDraft.type === 'tool'"><span>工具</span><select v-model="nodeDraft.tool_id"><option v-for="tool in tools" :key="tool.id" :value="tool.id">{{ tool.name }} · {{ tool.id }}</option></select></label>
            <label v-if="nodeDraft.type === 'subworkflow'"><span>工作流</span><select v-model="nodeDraft.workflow_id"><option v-for="workflow in workflows" :key="workflow.id" :value="workflow.id">{{ workflow.name }}</option></select></label>
            <label v-if="nodeDraft.type === 'condition'"><span>条件字段</span><input v-model="nodeDraft.condition_field" placeholder="verification.verdict" /></label>
            <label v-if="nodeDraft.type === 'condition'"><span>等于值</span><input v-model="nodeDraft.condition_value" placeholder="pass" /></label>
            <div class="workflow-node-form-actions"><span v-if="nodeError" class="tool-form-error">{{ nodeError }}</span><button class="secondary-action" @click="showNodeForm = false">取消</button><button class="primary-action" @click="addNode">添加节点</button></div>
          </div>
          <div ref="canvasRef" class="workflow-canvas">
            <div v-for="zone in loopZones" :key="zone.id" class="workflow-loop-zone" :style="{ left: `${zone.x}px`, top: `${zone.y}px`, width: `${zone.width}px`, height: `${zone.height}px` }"><span>LOOP · {{ zone.label }}</span></div>
            <svg class="workflow-canvas-edges" viewBox="0 0 960 520" preserveAspectRatio="none">
              <defs><marker id="workflow-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z" fill="currentColor" /></marker></defs>
              <path v-for="edge in visualEdges" :key="edge.id" :d="edge.path" class="workflow-edge" marker-end="url(#workflow-arrow)" />
              <path v-if="connectionPath" :d="connectionPath" class="workflow-edge workflow-edge--active" />
            </svg>
            <div v-for="node in visualNodes" :key="node.visualId" class="workflow-node-card" :class="{ 'workflow-node-card--dragging': dragging?.visualId === node.visualId, 'workflow-node-card--loop-child': node.scopePath }" :style="{ left: `${node.x}px`, top: `${node.y}px` }" @pointerdown.stop="startNodeDrag(node, $event)">
              <span class="workflow-node-port workflow-node-port--input" title="输入依赖" @pointerup.stop="finishConnection(node, $event)" />
              <div class="workflow-node-type">{{ node.type }}<em v-if="node.scopePath"> · 循环内</em></div>
              <strong>{{ node.node_id }}</strong>
              <small>{{ node.agent_id || node.tool_id || node.workflow_id || node.until?.field || '条件节点' }}</small>
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
