<script setup>
import { computed, onActivated, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  AlertTriangle, Check, CheckCircle2, ChevronDown, Clock3, FileDown,
  Gauge, MapPinned, PackageOpen, Play, Plus, RotateCcw, Route,
  ShieldCheck, SlidersHorizontal, Trash2, Truck, Wallet, X,
} from '@lucide/vue'
import AgentRail from '../components/AgentRail.vue'
import MapCanvas from '../components/MapCanvas.vue'
import { api, formatNumber } from '../api'
import { mergeRunEvent, runEventsUrl } from '../utils/runEvents.js'

defineProps({ dashboard: { type: Object, default: () => ({}) } })
const emit = defineEmits(['refresh-dashboard', 'navigate'])

const materials = ref([])
const scenarios = ref([])
const operationalEvents = ref([])
const runSource = ref('demo')
const selectedEventId = ref('')
const network = ref({})
const inventoryData = ref({ summary: [], snapshot: null })
const selectedScenarioId = ref('rainstorm-ii')
const demandItems = ref([])
const weights = ref({ speed: 38, cost: 27, risk: 35 })
const loading = ref(true)
const running = ref(false)
const runId = ref('')
const events = ref([])
const runResult = ref(null)
const selectedPlanId = ref('')
const assignmentMaterialFilter = ref('all')
const resultTab = ref('detail')
const error = ref('')
const approved = ref(false)
const dispatchTasks = ref([])
const materialToAdd = ref('')
let eventSource = null
let statusPoller = null
let loadedOnce = false

const selectedOperationalEvent = computed(() => operationalEvents.value.find((item) => item.id === selectedEventId.value))
const selectedScenario = computed(() => {
  if (runSource.value === 'event' && selectedOperationalEvent.value) {
    const event = selectedOperationalEvent.value
    return {
      id: `event-${event.id}`,
      name: event.title,
      description: event.description || '人工确认的运营事件',
      event_type: event.event_type,
      severity: event.severity,
      location: event.location,
      lat: event.lat,
      lng: event.lng,
      affected_population: event.affected_population,
      closed_routes: event.closed_routes || [],
      risk_notes: event.description || '',
    }
  }
  return scenarios.value.find((item) => item.id === selectedScenarioId.value)
})
const materialMap = computed(() => Object.fromEntries(materials.value.map((item) => [item.id, item])))
const materialByCode = computed(() => Object.fromEntries(materials.value.map((item) => [item.code, item])))
const availableOptions = computed(() => materials.value.filter((item) => !demandItems.value.some((row) => row.material_id === item.id)))
const finalReports = computed(() => runResult.value?.final_verification?.reports || [])
const selectedReport = computed(() => finalReports.value.find((item) => item.plan_id === selectedPlanId.value) || finalReports.value[0])
const runDemandMaterials = computed(() => {
  const seen = new Map()
  for (const item of runResult.value?.demand?.items || []) {
    if (!seen.has(item.material_id)) {
      seen.set(item.material_id, { material_id: item.material_id, name: item.material_name, unit: item.base_unit })
    }
  }
  return [...seen.values()]
})
const cheapestPlanId = computed(() => {
  return [...finalReports.value].sort((a, b) => a.metrics.total_cost - b.metrics.total_cost)[0]?.plan_id || ''
})
const fastestPlanId = computed(() => {
  return [...finalReports.value].sort((a, b) => a.metrics.max_eta_hours - b.metrics.max_eta_hours)[0]?.plan_id || ''
})
const filteredAssignments = computed(() => {
  const rows = selectedReport.value?.assignments || []
  return assignmentMaterialFilter.value === 'all'
    ? rows
    : rows.filter((item) => String(item.material_id) === String(assignmentMaterialFilter.value))
})
function selectPlan(planId) {
  selectedPlanId.value = planId
  assignmentMaterialFilter.value = 'all'
}
const selectedPlan = computed(() => selectedReport.value || null)
const firstRound = computed(() => runResult.value?.rounds?.[0])
const secondRound = computed(() => runResult.value?.rounds?.[1])
const closedRoutes = computed(() => runResult.value?.closed_routes || selectedScenario.value?.closed_routes || [])
const demandSummary = computed(() => demandItems.value.map((item) => {
  const material = materialMap.value[item.material_id]
  return { ...item, name: material?.name || '未知物资', unit: material?.base_unit || '' }
}))
const totalWeight = computed(() => demandItems.value.reduce((sum, item) => {
  const material = materialMap.value[item.material_id]
  return sum + Number(item.quantity || 0) * Number(material?.unit_weight_kg || 0)
}, 0))
const canRun = computed(() => demandItems.value.length > 0 && !running.value && (runSource.value === 'demo' ? Boolean(selectedScenario.value) : Boolean(selectedEventId.value)))
const workflowStep = computed(() => {
  if (approved.value) return 4
  if (runResult.value || running.value) return 3
  if (demandItems.value.length) return 2
  return 1
})
const workflowSteps = [
  { id: 1, label: '选择事件', hint: '演练或真实事件' },
  { id: 2, label: '配置需求', hint: '物资、数量与时限' },
  { id: 3, label: '智能匹配', hint: '生成并独立核验' },
  { id: 4, label: '审批执行', hint: '确认方案与库存' },
]

const statusText = computed(() => {
  if (running.value) return `推演进行中 · ${events.value.length} 个事件`
  if (runResult.value?.delivery?.status === 'ready') return approved.value ? '人工审批已通过' : '方案已生成，等待人工审批'
  if (runResult.value?.delivery?.status === 'human_escalation') return '核验未通过，已转人工研判'
  return '等待启动推演'
})

const materialShortages = computed(() => {
  const totals = new Map()
  for (const row of demandItems.value) {
    totals.set(row.material_id, (totals.get(row.material_id) || 0) + Number(row.quantity || 0))
  }
  return [...totals.entries()]
    .map(([materialId, required]) => {
      const material = materialOf(materialId)
      const available = stockOf(materialId)
      return {
        material_id: materialId,
        name: material.name || `物资 ${materialId}`,
        unit: material.base_unit || '',
        required,
        available,
        shortage: Math.max(0, required - available),
      }
    })
    .filter((item) => item.shortage > 0)
})
function materialOf(id) { return materialMap.value[id] || {} }
function stockOf(id) {
  return inventoryData.value.summary?.find((item) => item.material_id === id)?.total_quantity || 0
}
function switchRunSource(source) {
  if (source === 'demo') {
    const scenarioId = selectedScenarioId.value || scenarios.value[0]?.id
    if (scenarioId) applyScenario(scenarioId)
    return
  }
  const eventId = selectedEventId.value || operationalEvents.value[0]?.id
  if (eventId) {
    applyOperationalEvent(eventId)
    return
  }
  runSource.value = 'event'
  demandItems.value = []
  runResult.value = null
  selectedPlanId.value = ''
}
function applyScenario(id) {
  runSource.value = 'demo'
  selectedScenarioId.value = id
  const scenario = scenarios.value.find((item) => item.id === id)
  if (!scenario) return
  demandItems.value = scenario.default_demand
    .map((item) => {
      const material = materials.value.find((candidate) => candidate.code === item.material_code)
      if (!material) return null
      return {
        material_id: material.id,
        quantity: item.quantity,
        priority: item.priority,
        deadline_hours: item.deadline_hours,
      }
    })
    .filter(Boolean)
  runResult.value = null
  events.value = []
  selectedPlanId.value = ''
  error.value = ''
}

function applyOperationalEvent(id) {
  runSource.value = 'event'
  selectedEventId.value = id
  const event = operationalEvents.value.find((item) => item.id === id)
  demandItems.value = (event?.demand_items || [])
    .map((item) => {
      const material = materialByCode.value[item.material_code]
      if (!material) return null
      return {
        material_id: material.id,
        quantity: Number(item.quantity || 0),
        priority: item.priority || material.default_priority || 'P1',
        deadline_hours: Number(item.deadline_hours || 12),
      }
    })
    .filter((item) => item && item.quantity > 0)
  runResult.value = null
  events.value = []
  selectedPlanId.value = ''
  error.value = ''
}

function addDemand() {
  if (!materialToAdd.value) return
  const material = materialMap.value[Number(materialToAdd.value)]
  if (!material) return
  demandItems.value.push({
    material_id: material.id,
    quantity: 1000,
    priority: material.default_priority || 'P1',
    deadline_hours: 8,
  })
  materialToAdd.value = ''
}

function removeDemand(index) { demandItems.value.splice(index, 1) }

function updateWeights() {
  const total = Number(weights.value.speed) + Number(weights.value.cost) + Number(weights.value.risk)
  return {
    speed: total ? weights.value.speed / total : 0.34,
    cost: total ? weights.value.cost / total : 0.33,
    risk: total ? weights.value.risk / total : 0.33,
  }
}

async function refreshOperationalEvents(preferredEventId = '') {
  try {
    const rows = await api.events()
    const previousId = selectedEventId.value
    operationalEvents.value = rows
    if (preferredEventId && rows.some((item) => item.id === preferredEventId)) {
      applyOperationalEvent(preferredEventId)
      return
    }
    if (previousId && rows.some((item) => item.id === previousId)) return
    if (runSource.value === 'event') {
      if (rows.length) applyOperationalEvent(rows[0].id)
      else {
        selectedEventId.value = ''
        demandItems.value = []
        runResult.value = null
      }
    }
  } catch (_) {}
}

async function handleEventsChanged(event) {
  await refreshOperationalEvents(event?.detail?.eventId || '')
}
async function refreshResourceData() {
  try {
    const [materialRows, scenarioRows, networkRows, inventoryRows] = await Promise.all([
      api.materials(), api.scenarios(), api.network(), api.inventory(),
    ])
    materials.value = materialRows
    scenarios.value = scenarioRows
    network.value = networkRows
    inventoryData.value = inventoryRows
  } catch (_) {}
}
async function loadData() {
  loading.value = true
  error.value = ''
  try {
    const [materialRows, scenarioRows, eventRows, networkRows, inventoryRows, readinessData] = await Promise.all([
      api.materials(), api.scenarios(), api.events(), api.network(), api.inventory(), api.readiness(),
    ])
    materials.value = materialRows
    scenarios.value = scenarioRows
    operationalEvents.value = eventRows
    network.value = networkRows
    inventoryData.value = inventoryRows
    if (eventRows.length || readinessData.mode === 'operational') {
      if (eventRows.length) applyOperationalEvent(eventRows[0].id)
      else runSource.value = 'event'
    } else {
      applyScenario(scenarioRows.find((item) => item.id === 'rainstorm-ii')?.id || scenarioRows[0]?.id || 'rainstorm-ii')
    }
  } catch (exc) {
    error.value = exc.message
  } finally {
    loading.value = false
    loadedOnce = true
  }
}

function stopStatusPolling() {
  if (statusPoller) {
    clearInterval(statusPoller)
    statusPoller = null
  }
}

function connectEvents(id) {
  if (eventSource) eventSource.close()
  stopStatusPolling()
  eventSource = new EventSource(runEventsUrl(id, events.value))
  eventSource.onmessage = (message) => {
    const event = JSON.parse(message.data)
    events.value = mergeRunEvent(events.value, event)
    if (['awaiting.approval', 'human.escalation', 'run.failed'].includes(event.type)) {
      setTimeout(() => finishRun(id), 350)
    }
  }
  eventSource.onerror = () => {
    if (!running.value) eventSource?.close()
  }
  statusPoller = setInterval(async () => {
    if (!running.value) return
    try {
      const latest = await api.getRun(id)
      if (['awaiting_approval', 'approved', 'needs_human', 'failed', 'rejected'].includes(latest.status)) {
        await finishRun(id)
      }
    } catch (_) {}
  }, 2000)
}

async function finishRun(id) {
  if (!running.value) return
  try {
    const [run, taskRows] = await Promise.all([api.getRun(id), api.tasks(id)])
    runResult.value = run.result
    dispatchTasks.value = taskRows
    running.value = false
    approved.value = false
    const recommended = run.result?.delivery?.recommended_plan?.plan_id
    selectedPlanId.value = recommended || run.result?.final_verification?.reports?.[0]?.plan_id || ''
    emit('refresh-dashboard')
  } catch (exc) {
    error.value = exc.message
    running.value = false
  } finally {
    stopStatusPolling()
    eventSource?.close()
  }
}

async function startRun() {
  if (!canRun.value) return
  error.value = ''
  approved.value = false
  runResult.value = null
  events.value = []
  selectedPlanId.value = ''
  resultTab.value = 'detail'
  running.value = true
  try {
    const response = await api.startRun({
      scenario_id: runSource.value === 'event' ? 'manual' : selectedScenarioId.value,
      event_id: runSource.value === 'event' ? selectedEventId.value : null,
      event: {
        type: selectedScenario.value.event_type,
        severity: selectedScenario.value.severity,
        location: selectedScenario.value.location,
        affected_population: selectedScenario.value.affected_population,
        closed_route_ids: selectedScenario.value.closed_routes,
        note: selectedScenario.value.risk_notes,
      },
      demand_items: demandItems.value,
      weights: updateWeights(),
    })
    runId.value = response.run_id
    connectEvents(response.run_id)
  } catch (exc) {
    error.value = exc.message
    running.value = false
  }
}

async function approveRun() {
  if (!runId.value) return
  try {
    const result = await api.approveRun(runId.value, '现场值班负责人确认调度方案')
    approved.value = true
    dispatchTasks.value = result.tasks || []
  } catch (exc) { error.value = exc.message }
}

async function updateTaskStatus(task, status) {
  try {
    const updated = await api.updateTask(task.id, { status, assignee: task.assignee || '调度值班员' })
    const index = dispatchTasks.value.findIndex((item) => item.id === task.id)
    if (index >= 0) dispatchTasks.value[index] = updated
  } catch (exc) { error.value = exc.message }
}

function openReport() {
  if (runId.value) window.open(`/api/runs/${runId.value}/report`, '_blank')
}

function handleRunShortcut(event) {
  if ((event.ctrlKey || event.metaKey) && event.key === 'Enter' && canRun.value) {
    event.preventDefault()
    startRun()
  }
}

onMounted(() => {
  window.addEventListener('farmmatch:events-changed', handleEventsChanged)
  window.addEventListener('keydown', handleRunShortcut)
  loadData()
})
onActivated(async () => {
  if (loadedOnce) await Promise.all([refreshOperationalEvents(), refreshResourceData()])
})
onBeforeUnmount(() => {
  stopStatusPolling()
  eventSource?.close()
  window.removeEventListener('farmmatch:events-changed', handleEventsChanged)
  window.removeEventListener('keydown', handleRunShortcut)
})
</script>
<template>
  <div class="workbench-page">
    <div v-if="loading" class="loading-panel">
      <div class="loading-orbit" />
      <strong>正在加载资源快照与智能体编排协议</strong>
    </div>

    <template v-else>
      <div class="workbench-command-strip">
        <div class="command-steps" aria-label="调度流程">
          <div v-for="step in workflowSteps" :key="step.id" class="command-step" :class="{ 'command-step--active': workflowStep === step.id, 'command-step--done': workflowStep > step.id }">
            <span class="command-step-index"><Check v-if="workflowStep > step.id" :size="12" /><template v-else>{{ step.id }}</template></span>
            <div><strong>{{ step.label }}</strong><small>{{ step.hint }}</small></div>
          </div>
        </div>
        <div class="command-context">
          <span class="command-context-chip"><span class="live-dot" />{{ runSource === 'event' ? '真实事件模式' : '演练场景模式' }}</span>
          <span class="command-context-copy">{{ inventoryData.snapshot?.label || '正在读取库存快照' }} · {{ inventoryData.snapshot?.row_count || 0 }} 条记录</span>
        </div>
      </div>

      <div class="workbench-grid">
        <section class="control-panel">
          <div class="panel-heading">
            <div>
              <div class="eyebrow">MISSION INPUT</div>
              <h2>推演参数</h2>
            </div>
            <span class="step-chip">01</span>
          </div>

          <div class="source-toggle">
            <button :class="{ active: runSource === 'demo' }" @click="switchRunSource('demo')">演练场景</button>
            <button :class="{ active: runSource === 'event' }" @click="switchRunSource('event')">真实事件</button>
          </div>

          <template v-if="runSource === 'demo'">
            <label class="field-label">演练场景</label>
            <div class="select-wrap">
              <select v-model="selectedScenarioId" @change="applyScenario(selectedScenarioId)">
                <option v-for="scenario in scenarios" :key="scenario.id" :value="scenario.id">{{ scenario.name }}</option>
              </select>
              <ChevronDown :size="16" />
            </div>
          </template>
          <template v-else>
            <label class="field-label">已确认真实事件</label>
            <div class="select-wrap">
              <select :value="selectedEventId" @change="applyOperationalEvent($event.target.value)">
                <option value="">请选择已确认事件</option>
                <option v-for="event in operationalEvents" :key="event.id" :value="event.id">{{ event.title }} · {{ event.severity }}</option>
              </select>
              <ChevronDown :size="16" />
            </div>
            <p v-if="!operationalEvents.length" class="event-empty-hint">尚无真实事件，请先到“数据中心 → 真实事件”录入。</p>
            <p v-else-if="selectedOperationalEvent && !selectedOperationalEvent.affected_population" class="event-empty-hint">该事件未填写影响人口，系统将按已录入的物资需求计算。</p>
          </template>

          <div class="scenario-brief" v-if="selectedScenario">
            <div class="scenario-title">
              <span class="severity-badge">{{ selectedScenario.severity }}</span>
              <strong>{{ selectedScenario.event_type }}</strong>
            </div>
            <p>{{ selectedScenario.description }}</p>
            <div class="scenario-meta">
              <span><MapPinned :size="14" />{{ selectedScenario.location }}</span>
              <span><PackageOpen :size="14" />{{ selectedScenario.affected_population ? `${formatNumber(selectedScenario.affected_population)} 人` : '人口待补录' }}</span>
              <span><Route :size="14" />{{ selectedScenario.closed_routes.length }} 条封闭主通道</span>
            </div>
          </div>

          <div class="section-divider"><span>物资需求</span><span>{{ demandItems.length }} 类</span></div>
          <div class="demand-list">
            <div v-for="(row, index) in demandItems" :key="row.material_id" class="demand-row">
              <div class="demand-main">
                <div class="material-avatar">{{ materialOf(row.material_id).name?.slice(0, 1) }}</div>
                <div>
                  <strong>{{ materialOf(row.material_id).name }}</strong>
                  <small>{{ materialOf(row.material_id).category }} · 库存 {{ formatNumber(stockOf(row.material_id)) }} {{ materialOf(row.material_id).base_unit }}</small>
                </div>
                <button class="row-delete" title="移除" @click="removeDemand(index)"><X :size="14" /></button>
              </div>
              <div class="demand-inputs">
                <label><span>需求量</span><input v-model.number="row.quantity" type="number" min="1" /><em>{{ materialOf(row.material_id).base_unit }}</em></label>
                <label><span>优先级</span>
                  <select v-model="row.priority"><option>P0</option><option>P1</option><option>P2</option></select>
                </label>
                <label><span>时限</span><input v-model.number="row.deadline_hours" type="number" min="1" /><em>h</em></label>
              </div>
            </div>
          </div>

          <div class="add-demand">
            <select v-model="materialToAdd">
              <option value="">从物资库选择物资...</option>
              <option v-for="material in availableOptions" :key="material.id" :value="material.id">
                {{ material.name }} · {{ material.base_unit }}
              </option>
            </select>
            <button class="add-square-action" :disabled="!materialToAdd" @click="addDemand"><Plus :size="17" /></button>
          </div>

          <div v-if="materialShortages.length" class="shortage-panel shortage-panel--preflight">
            <AlertTriangle :size="18" />
            <div>
              <strong>库存总量预检发现缺口</strong>
              <p v-for="item in materialShortages" :key="item.material_id">
                {{ item.name }}：需求 {{ formatNumber(item.required) }}，总可用 {{ formatNumber(item.available) }}，缺口 {{ formatNumber(item.shortage) }} {{ item.unit }}
              </p>
              <p>总可用量满足不代表一定能交付，系统仍会继续检查仓库分布、路线、时效和车辆运力。</p>
            </div>
          </div>

          <details class="advanced-settings">
            <summary>
              <div><strong>高级调度偏好</strong><span>时效 {{ weights.speed }}% · 成本 {{ weights.cost }}% · 安全 {{ weights.risk }}%</span></div>
              <ChevronDown :size="15" />
            </summary>
            <div class="weight-list">
              <label v-for="key in ['speed', 'cost', 'risk']" :key="key" class="weight-row">
                <span class="weight-name">{{ { speed: '时效', cost: '成本', risk: '安全' }[key] }}</span>
                <input v-model.number="weights[key]" type="range" min="0" max="100" />
                <strong>{{ weights[key] }}%</strong>
              </label>
            </div>
            <div class="data-footnote">
              <span class="data-dot" />
              <div><strong>数据快照</strong><span>{{ inventoryData.snapshot?.label }} · {{ inventoryData.snapshot?.row_count }} 条记录</span></div>
            </div>
          </details>
          <div v-if="error" class="inline-error"><AlertTriangle :size="16" />{{ error }}</div>
          <button class="primary-run" :disabled="!canRun" @click="startRun">
            <Play :size="18" fill="currentColor" />
            <span>{{ running ? '智能体推演中...' : '启动多智能体推演' }}</span>
            <kbd>Ctrl ↵</kbd>
          </button>
        </section>

        <div class="center-stack">
          <MapCanvas :network="network" :plan="selectedPlan" :closed-routes="closedRoutes" :running="running" :event-location="runSource === 'event' ? selectedOperationalEvent : selectedScenario" :inventory="inventoryData.items || []" />
          <div class="map-status-strip">
            <div class="status-main">
              <span class="status-pulse" :class="{ 'status-pulse--live': running }" />
              <div><strong>{{ statusText }}</strong><span>{{ runId ? `运行编号 ${runId}` : '数据已就绪，可启动场景推演' }}</span></div>
            </div>
            <div class="status-metrics">
              <span><PackageOpen :size="15" />{{ demandItems.length }} 类物资</span>
              <span><Truck :size="15" />{{ formatNumber(totalWeight, 1) }} kg</span>
              <span><Clock3 :size="15" />云端推理通常 45–90 秒</span>
            </div>
          </div>
        </div>

        <AgentRail :events="events" :running="running" />
      </div>

      <section v-if="runResult" class="result-section">
        <div class="result-header">
          <div>
            <div class="eyebrow">MULTI-AGENT OUTPUT</div>
            <h2>多方案核验与交付</h2>
          </div>
          <div class="result-actions">
            <button class="secondary-action" @click="openReport"><FileDown :size="16" />导出报告</button>
            <button v-if="runResult.delivery.status === 'ready' && !approved" class="approve-action" @click="approveRun"><CheckCircle2 :size="16" />人工审批通过</button>
            <span v-else class="approved-chip"><Check :size="14" />已审批</span>
          </div>
        </div>

        <div class="loop-metrics-strip">
          <div><span>闭环轮次</span><strong>{{ runResult.loop_metrics.attempts }}</strong></div>
          <div><span>首轮硬阻断</span><strong>{{ runResult.loop_metrics.blockers_found }}</strong></div>
          <div><span>修正后评分</span><strong>{{ runResult.loop_metrics.final_score }}</strong></div>
          <div><span>评分提升</span><strong class="metric-positive">+{{ runResult.loop_metrics.score_gain }}</strong></div>
          <div><span>RAG 证据</span><strong>{{ runResult.retrieval.length }}</strong></div>
          <div><span>AgentArts 决策</span><strong class="metric-mode">{{ runResult.agent_decision?.applied ? '已采纳' : '规则回退' }}</strong></div>
        </div>

        <div class="closure-strip" :class="{ 'closure-strip--risk': runResult.delivery.status !== 'ready' }">
          <template v-for="(round, index) in runResult.rounds" :key="round.attempt">
            <div class="closure-step">
              <span class="closure-num">{{ String(index + 1).padStart(2, '0') }}</span>
              <div>
                <strong>{{ index === 0 ? '首轮生成' : '自适应修正' }}</strong>
                <small v-if="index === 0">{{ round.plans?.length || 0 }} 套候选方案</small>
                <small v-else>{{ round.rework_directive?.categories?.join(' / ') || '约束修正' }} · {{ round.plans?.length || 0 }} 套</small>
              </div>
            </div>
            <div class="closure-arrow" :class="{ 'closure-arrow--risk': round.verification?.verdict === 'rework' }">
              <RotateCcw :size="17" />{{ round.verification?.verdict === 'rework' ? '独立核验退回' : '核验通过' }}
            </div>
          </template>
          <div class="closure-step">
            <span class="closure-num">{{ String(runResult.rounds.length + 1).padStart(2, '0') }}</span>
            <div><strong>{{ runResult.delivery.status === 'ready' ? '方案交付' : '人工研判' }}</strong><small>{{ finalReports.length }} 套方案进入对比</small></div>
          </div>
        </div>

        <div class="plan-grid">
          <button v-for="report in finalReports" :key="report.plan_id" class="plan-card"
            :class="{ 'plan-card--active': selectedPlan?.plan_id === report.plan_id, 'plan-card--rejected': !report.approved }"
            @click="selectPlan(report.plan_id)">
            <div class="plan-topline">
              <span class="plan-tag">{{ report.tag }}</span>
              <span class="risk-score" :class="{ 'risk-score--warn': report.verification.score < 80 }">
                {{ report.verification.score }}<small>分</small>
              </span>
            </div>
            <h3>{{ report.name }}</h3>
            <p v-if="report.policy_note" class="plan-policy-note">{{ report.policy_note }}</p>
            <div class="plan-metrics">
              <div :class="{ 'plan-metric--best': report.plan_id === cheapestPlanId }"><span>预估成本 <em v-if="report.plan_id === cheapestPlanId">最低</em></span><strong>¥{{ formatNumber(report.metrics.total_cost) }}</strong></div>
              <div :class="{ 'plan-metric--best': report.plan_id === fastestPlanId }"><span>最长时效 <em v-if="report.plan_id === fastestPlanId">最快</em></span><strong>{{ report.metrics.max_eta_hours }}<small>h</small></strong></div>
              <div><span>需求覆盖</span><strong>{{ (report.metrics.coverage * 100).toFixed(1) }}<small>%</small></strong></div>
            </div>
            <div class="plan-foot">
              <span><Truck :size="13" />{{ report.metrics.vehicle_trips }} 趟</span>
              <span><Gauge :size="13" />{{ report.metrics.warehouse_count }} 个仓库</span>
              <span>{{ report.metrics.assignment_count }} 条分配</span>
              <span class="plan-verdict" :class="{ 'plan-verdict--pass': report.approved }">{{ report.approved ? '已通过' : '未通过' }}</span>
            </div>
          </button>
        </div>

        <div v-if="selectedReport?.unmet?.length" class="shortage-panel">
          <AlertTriangle :size="18" />
          <div>
            <strong>资源缺口导致方案未通过</strong>
            <p v-for="item in selectedReport.unmet.slice(0, 5)" :key="`${item.material_id}-${item.destination_id}`">
              {{ item.destination_name }}：{{ item.material_name }} 缺 {{ formatNumber(item.quantity) }} {{ item.unit }}
            </p>
            <p v-if="selectedReport.unmet.length > 5">另有 {{ selectedReport.unmet.length - 5 }} 条缺口，请查看完整核验记录。</p>
          </div>
        </div>

        <div class="detail-shell" v-if="selectedReport">
          <div class="detail-tabs">
            <button :class="{ active: resultTab === 'detail' }" @click="resultTab = 'detail'">调拨明细</button>
            <button :class="{ active: resultTab === 'verify' }" @click="resultTab = 'verify'">核验记录</button>
            <button :class="{ active: resultTab === 'delivery' }" @click="resultTab = 'delivery'">交付包</button>
            <span class="detail-id mono">{{ selectedReport.plan_id }}</span>
          </div>

          <div v-if="resultTab === 'detail'" class="detail-content">
            <div class="detail-summary-row">
              <div><Wallet :size="17" /><span>总成本</span><strong>¥{{ formatNumber(selectedReport.metrics.total_cost) }}</strong></div>
              <div><Clock3 :size="17" /><span>最长时效</span><strong>{{ selectedReport.metrics.max_eta_hours }}h</strong></div>
              <div><ShieldCheck :size="17" /><span>核验评分</span><strong>{{ selectedReport.verification.score }}</strong></div>
              <div><PackageOpen :size="17" /><span>分配条目</span><strong>{{ selectedReport.assignments.length }}</strong></div>
            </div>
            <div class="assignment-toolbar">
              <div class="demand-material-strip"><span>本次需求物资</span><em v-for="material in runDemandMaterials" :key="material.material_id">{{ material.name }}</em></div>
              <label class="assignment-filter"><span>筛选物资</span><select v-model="assignmentMaterialFilter"><option value="all">全部物资（{{ selectedReport.assignments.length }} 条）</option><option v-for="material in runDemandMaterials" :key="material.material_id" :value="String(material.material_id)">{{ material.name }}</option></select></label>
            </div>
            <div class="table-wrap assignment-table-wrap">
              <table class="data-table compact-table">
                <thead><tr><th>仓库</th><th>安置点</th><th>物资</th><th>数量</th><th>运输路线</th><th>时效</th></tr></thead>
                <tbody>
                  <tr v-for="item in filteredAssignments" :key="`${item.group_id}-${item.material_id}`">
                    <td>{{ item.warehouse_name }}</td><td>{{ item.destination_name }}</td><td><strong>{{ item.material_name }}</strong></td>
                    <td><strong>{{ formatNumber(item.quantity) }}</strong> {{ item.base_unit }}</td>
                    <td><span class="route-chip"><Route :size="12" />{{ item.route_name.split('→')[1] }}</span></td>
                    <td>{{ item.route_eta_hours }}h</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>

          <div v-if="resultTab === 'verify'" class="detail-content verify-content">
            <div class="check-grid">
              <div v-for="check in selectedReport.verification.checks" :key="check.name" class="check-card"
                :class="`check-card--${check.status}`">
                <span>{{ check.status === 'pass' ? 'PASS' : check.status === 'warn' ? 'WARN' : 'FAIL' }}</span>
                <strong>{{ check.name }}</strong><small>{{ check.value || '规则校验' }}</small>
              </div>
            </div>
            <div v-if="selectedReport.verification.blockers.length" class="issue-list">
              <article v-for="issue in selectedReport.verification.blockers" :key="issue.code" class="issue-item issue-item--blocker">
                <AlertTriangle :size="17" /><div><strong>{{ issue.message }}</strong><span>{{ issue.code }} · {{ issue.evidence }}</span></div>
              </article>
            </div>
            <div v-else class="verification-pass"><CheckCircle2 :size="22" /><div><strong>独立核验通过</strong><span>未发现道路、库存、运力或时效硬阻断项。</span></div></div>
            <div v-if="selectedReport.verification.warnings.length" class="issue-list">
              <article v-for="issue in selectedReport.verification.warnings" :key="issue.code" class="issue-item">
                <AlertTriangle :size="17" /><div><strong>{{ issue.message }}</strong><span>{{ issue.evidence }}</span></div>
              </article>
            </div>
            <div class="rag-evidence">
              <div class="rag-evidence-head"><span>RAG 检索证据</span><em>{{ runResult.retrieval.length }} 条 · 已进入 Agent 上下文</em></div>
              <div v-for="doc in runResult.retrieval" :key="doc.id" class="rag-evidence-item">
                <span class="mono">{{ doc.id }}</span>
                <div><strong>{{ doc.title }}</strong><small>{{ doc.excerpt }}</small><small v-if="doc.citation" class="evidence-locator">引用定位：{{ doc.citation.locator }} · {{ doc.retrieval?.embedding_backend || 'hash' }}</small></div>
                <em>{{ doc.score }}</em>
              </div>
            </div>
            <div class="evidence-line"><span>证据链</span><em v-for="ref in runResult.final_verification.evidence_refs" :key="ref">{{ ref }}</em></div>
          </div>

          <div v-if="resultTab === 'delivery' && runResult.delivery.status === 'ready'" class="detail-content delivery-content">
            <div class="delivery-summary">
              <ShieldCheck :size="22" />
              <div><strong>{{ runResult.delivery.summary }}</strong><p>{{ runResult.delivery.disclaimer }}</p></div>
            </div>
            <div v-if="runResult.extension_reviews?.length" class="extension-review-block">
              <h4>动态扩展 Agent 审查</h4>
              <div v-for="review in runResult.extension_reviews" :key="review.agent_id" class="extension-review-row">
                <div><strong>{{ review.agent_name }}</strong><span>{{ review.agent_id }} · {{ review.mode === 'agentarts' ? 'AgentArts' : '本地回退' }}</span></div>
                <p>{{ review.parsed?.summary || review.parsed?.rationale || review.text || '扩展审查已完成' }}</p>
              </div>
            </div>
            <div class="delivery-grid" style="margin-bottom: 14px;">
              <div class="delivery-block">
                <h4>需求置信度证据 · {{ ((runResult.demand?.confidence || 0) * 100).toFixed(0) }}%</h4>
                <ul>
                  <li v-for="(value, key) in (runResult.demand?.confidence_factors || {})" :key="key">{{ key }}：{{ (Number(value) * 100).toFixed(1) }}%</li>
                  <li>计算方法：{{ runResult.demand?.confidence_method || '确定性因子加权' }}</li>
                </ul>
              </div>
              <div class="delivery-block">
                <h4>Prompt 自适应证据</h4>
                <ul>
                  <li>当前匹配 Prompt：{{ runResult.active_prompt_versions?.matching_agent || 'v1.4' }}</li>
                  <li v-for="item in (runResult.prompt_adaptations || [])" :key="`${item.active_version}-${item.selection_policy}`">{{ item.active_version }} · {{ item.source_issue_codes.join('、') }} → {{ item.instructions.join('；') }}</li>
                  <li v-if="!runResult.prompt_adaptations?.length">本轮未触发反馈补丁，使用基础 Prompt。</li>
                </ul>
              </div>
            </div>
            <div v-if="runResult.ai_decision_matrix?.length" class="table-wrap" style="margin-bottom: 14px;">
              <table class="data-table"><thead><tr><th>编号</th><th>层级</th><th>AI 决策</th><th>模型职责</th><th>来源</th><th>安全边界</th><th>降级策略</th><th>本地确定性等价能力</th></tr></thead><tbody>
                <tr v-for="item in runResult.ai_decision_matrix" :key="item.id"><td><strong>{{ item.id }}</strong></td><td><span class="plan-tag">{{ item.level }}</span></td><td><strong>{{ item.decision }}</strong></td><td>{{ item.ai_role }}</td><td><span class="inventory-status" :class="item.applied ? 'inventory-status--available' : 'inventory-status--expired'">{{ item.applied ? '已生效' : '已降级' }} · {{ item.source }}</span></td><td>{{ item.boundary }}</td><td>{{ item.fallback }}</td><td>{{ item.local_equivalent }}</td></tr>
              </tbody></table>
            </div>
            <div class="delivery-grid">
              <div v-if="runResult.agent_decision" class="delivery-block">
                <h4>AI 决策证据</h4>
                <ul>
                  <li>决策来源：{{ runResult.agent_decision.source === 'agentarts' ? '华为云 AgentArts' : '确定性规则回退' }}</li>
                  <li>推荐方案：{{ runResult.agent_decision.recommended_plan_id || '未返回有效方案' }}</li>
                  <li>推荐理由：{{ runResult.agent_decision.rationale }}</li>
                  <li>安全边界：AI 只能推荐已通过独立核验的方案，不能覆盖道路、库存、运力和时效硬规则。</li>
                </ul>
              </div>
              <div class="delivery-block">
                <h4>风险与现场提示</h4>
                <ul><li v-for="alert in runResult.delivery.risk_alerts" :key="alert">{{ alert }}</li></ul>
              </div>
              <div class="delivery-block">
                <h4>执行前检查</h4>
                <ol><li v-for="action in runResult.delivery.actions" :key="action">{{ action }}</li></ol>
              </div>
            </div>
            <div v-if="dispatchTasks.length" class="dispatch-task-block">
              <h4>执行任务跟踪</h4>
              <div v-for="task in dispatchTasks" :key="task.id" class="dispatch-task-row">
                <div><strong>{{ task.title }}</strong><span>{{ task.assignee || '待分配' }} · {{ task.payload.route_name || '' }}</span></div>
                <select :value="task.status" @change="updateTaskStatus(task, $event.target.value)">
                  <option value="pending">待执行</option><option value="assigned">已分配</option>
                  <option value="in_progress">执行中</option><option value="completed">已完成</option>
                  <option value="failed">异常</option><option value="cancelled">取消</option>
                </select>
              </div>
            </div>
            <div class="timeline-table">
              <div v-for="item in runResult.delivery.timeline.slice(0, 6)" :key="`${item.warehouse}-${item.destination}`" class="timeline-row">
                <span class="timeline-time">{{ item.departure.slice(11, 16) }}</span>
                <i />
                <div><strong>{{ item.warehouse }} → {{ item.destination }}</strong><span>{{ item.route }} · {{ item.vehicle_trips }} 趟</span></div>
                <em>ETA {{ item.arrival.slice(11, 16) }}</em>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section v-else-if="!running" class="preflight-section">
        <div class="preflight-icon"><SlidersHorizontal :size="24" /></div>
        <div><h3>预案参数已装载</h3><p>选择物资并调整目标权重，启动后将实时展示四个智能体的交接、核验退回与修正过程。</p></div>
        <div class="preflight-steps">
          <span>需求标准化</span><i /><span>多目标匹配</span><i /><span>独立核验</span><i /><span>方案交付</span>
        </div>
      </section>
    </template>
  </div>
</template>
