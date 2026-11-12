const FIELD_LABELS = {
  title: '事件标题',
  event_type: '事件类型',
  severity: '事件等级',
  location: '影响区域',
  affected_population: '影响人口',
  closed_routes: '封闭路线',
  name: '名称',
  region: '区域或地址',
  capacity: '仓库容量',
  lat: '纬度',
  lng: '经度',
  warehouse_id: '所属仓库',
  material_id: '物资',
  quantity: '数量',
  batch_no: '批次号',
  expiry_date: '有效期',
  address: '安置点地址',
  plate_no: '车牌号',
  vehicle_type: '车型',
  capacity_kg: '车辆载重',
  code: '编码',
  base_unit: '基本单位',
  priority: '优先级',
  deadline_hours: '处理时限',
  username: '用户名',
  display_name: '显示名称',
  password: '密码',
  current_password: '当前密码',
  new_password: '新密码',
  role: '角色',
  event: '事件信息',
  demand_items: '物资需求',
  weights: '调度权重',
  status: '状态',
  assignee: '负责人',
  note: '备注',
  task: '任务内容',
  session_id: '会话编号',
}

const STATUS_MESSAGES = {
  400: '提交内容不符合要求',
  401: '登录已失效或请求未授权',
  403: '没有权限执行该操作',
  404: '请求的资源不存在',
  405: '不支持当前请求方法',
  409: '请求与当前数据状态冲突',
  422: '提交内容格式不正确',
  429: '操作过于频繁，请稍后再试',
  500: '服务器内部错误，请稍后重试',
  502: '外部服务暂时不可用，请稍后重试',
  503: '服务暂时不可用，请稍后重试',
}

function translateErrorMessage(message) {
  let text = String(message || '').replace(/^Value error,\s*/i, '').trim()
  const translations = [
    [/Input should be greater than or equal to ([\d.-]+)/i, '不能小于 $1'],
    [/Input should be greater than ([\d.-]+)/i, '必须大于 $1'],
    [/Input should be less than or equal to ([\d.-]+)/i, '不能大于 $1'],
    [/Input should be less than ([\d.-]+)/i, '必须小于 $1'],
    [/String should have at least (\d+) characters/i, '至少需要 $1 个字符'],
    [/String should have at most (\d+) characters/i, '不能超过 $1 个字符'],
    [/(?:List|Array) should have at least (\d+) items/i, '至少需要 $1 项'],
    [/(?:List|Array) should have at most (\d+) items/i, '不能超过 $1 项'],
    [/Field required/i, '为必填项'],
    [/Input should be a valid integer/i, '必须是整数'],
    [/Input should be a valid number/i, '必须是有效数字'],
    [/Input should be a valid boolean/i, '必须是有效的是或否'],
    [/JSON decode error/i, '请求内容不是合法 JSON'],
    [/Input should be a valid list/i, '必须是列表'],
    [/Input should be a valid dictionary or object/i, '必须是对象'],
  ]
  for (const [pattern, replacement] of translations) {
    if (pattern.test(text)) return text.replace(pattern, replacement)
  }
  if (/[A-Za-z]/.test(text) && !/[\u4e00-\u9fff]/.test(text)) return '填写内容不符合要求'
  return text || '填写内容不符合要求'
}

function describeErrorDetail(detail, status) {
  if (typeof detail === 'string' && detail) return translateErrorMessage(detail)
  if (Array.isArray(detail)) {
    return detail.map((item) => {
      const field = (item.loc || []).filter((part) => !['body', 'query', 'path'].includes(part) && !String(part).match(/^\d+$/)).pop()
      const label = FIELD_LABELS[field] || '参数'
      const message = translateErrorMessage(item.msg || '填写内容不符合要求')
      return `${label}：${message}`
    }).join('；')
  }
  if (detail && typeof detail === 'object') {
    if (Array.isArray(detail.errors)) {
      return detail.errors.map((item) => `${item.field ? `${item.field}：` : ''}${translateErrorMessage(item.message)}`).join('；')
    }
    return translateErrorMessage(detail.message || '提交内容不符合要求')
  }
  return STATUS_MESSAGES[status] || `请求失败（${status}）`
}

async function request(path, options = {}) {
  let response
  try {
    response = await fetch(path, options)
  } catch (_) {
    throw new Error('无法连接服务器，请检查服务是否启动或网络连接是否正常')
  }
  if (!response.ok) {
    let detail = STATUS_MESSAGES[response.status] || `请求失败（${response.status}）`
    try {
      const payload = await response.json()
      detail = describeErrorDetail(payload.detail || payload.message, response.status)
    } catch (_) {}
    const error = new Error(detail)
    error.status = response.status
    if (response.status === 401 && path !== '/api/auth/login') window.dispatchEvent(new CustomEvent('farmmatch:unauthorized'))
    throw error
  }
  return response.json()
}
export const api = {
  me: () => request('/api/auth/me'),
  login: (payload) => request('/api/auth/login', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }),
  logout: () => request('/api/auth/logout', { method: 'POST' }),
  changePassword: (payload) => request('/api/auth/change-password', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }),
  users: () => request('/api/auth/users'),
  createUser: (payload) => request('/api/auth/users', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }),
  updateUser: (id, payload) => request(`/api/auth/users/${id}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }),
  deleteUser: (id) => request(`/api/auth/users/${id}`, { method: 'DELETE' }),
  knowledgeDocuments: () => request('/api/knowledge/documents'),
  knowledgeChunks: (id) => request(`/api/knowledge/documents/${id}/chunks`),
  searchKnowledge: (query, top = 4) => request(`/api/knowledge/search?q=${encodeURIComponent(query)}&top_k=${top}`),
  uploadKnowledge: (file, category) => {
    const formData = new FormData()
    formData.append('file', file)
    formData.append('category', category || '其他')
    return request('/api/knowledge/documents', { method: 'POST', body: formData })
  },
  deleteKnowledge: (id) => request(`/api/knowledge/documents/${id}`, { method: 'DELETE' }),  auditLogs: (limit = 200) => request(`/api/auth/audit-logs?limit=${limit}`),
  tools: () => request('/api/tools'),
  saveTool: (id, payload) => request(`/api/tools/${encodeURIComponent(id)}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }),
  deleteTool: (id) => request(`/api/tools/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  invokeTool: (id, argumentsValue = {}) => request(`/api/tools/${encodeURIComponent(id)}/invoke`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ arguments: argumentsValue }),
  }),
  workflows: () => request('/api/workflows'),
  workflowTemplates: () => request('/api/workflow-templates'),
  instantiateWorkflowTemplate: (id, payload = {}) => request(`/api/workflow-templates/${encodeURIComponent(id)}/instantiate`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }),
  memory: (agentId = '') => request(`/api/memory${agentId ? `?agent_id=${encodeURIComponent(agentId)}` : ''}`),
  deleteMemory: (agentId, memoryKey) => request(`/api/memory/${encodeURIComponent(agentId)}/${encodeURIComponent(memoryKey)}`, { method: 'DELETE' }),
  workflow: (id) => request(`/api/workflows/${encodeURIComponent(id)}`),
  workflowRun: (id) => request(`/api/workflow-runs/${encodeURIComponent(id)}`),
  runWorkflow: (id, context, maxWorkers = 4) => request(`/api/workflows/${encodeURIComponent(id)}/run`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ context, max_workers: maxWorkers }),
  }),
  saveWorkflow: (id, payload) => request(`/api/workflows/${encodeURIComponent(id)}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }),
  validateWorkflow: (definition) => request('/api/workflows/validate', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ definition }),
  }),
  deleteWorkflow: (id) => request(`/api/workflows/${encodeURIComponent(id)}`, { method: 'DELETE' }),  agents: () => request('/api/agents'),
  discoverAgents: (role = '') => request(`/api/agents/discover${role ? `?role=${encodeURIComponent(role)}` : ''}`),
  registerAgent: (payload) => request('/api/agents', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }),
  addAgentVersion: (id, payload) => request(`/api/agents/${encodeURIComponent(id)}/versions`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }),
  activateAgentVersion: (id, version) => request(`/api/agents/${encodeURIComponent(id)}/versions/${encodeURIComponent(version)}/activate`, { method: 'POST' }),
  invokeAgent: (id, payload) => request(`/api/agents/${encodeURIComponent(id)}/invoke`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }),
  dashboard: () => request('/api/dashboard'),
  systemStatus: () => request('/api/system/status'),
  checkAgentArts: (force = false) => request('/api/agentarts/check' + (force ? '?force=true' : '')),
  manifest: () => request('/api/platform/manifest'),
  benchmark: () => request('/api/evaluation/benchmark'),
  benchmarkReport: () => request('/api/benchmark/report'),
  llmCalls: (runId = '', limit = 50) => request(`/api/llm/calls?${new URLSearchParams({ ...(runId ? { run_id: runId } : {}), limit }).toString()}`),
  runStages: (runId) => request(`/api/runs/${encodeURIComponent(runId)}/stages`),
  rootCauses: (days = 7) => request(`/api/runs/root-causes?days=${encodeURIComponent(days)}`),
  a2a: (runId, limit = 100) => request(`/api/a2a/messages?run_id=${encodeURIComponent(runId)}&limit=${encodeURIComponent(limit)}`),
  latestDiagnostics: () => request('/api/evaluation/diagnostics'),
  runDiagnostics: () => request('/api/evaluation/diagnostics', { method: 'POST' }),
  materials: (search = '', category = '') => {
    const params = new URLSearchParams()
    if (search) params.set('search', search)
    if (category) params.set('category', category)
    return request(`/api/materials?${params}`)
  },
  categories: () => request('/api/materials/categories'),
  createMaterial: (payload) => request('/api/materials', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }),
  inventory: () => request('/api/inventory'),
  createInventoryEntry: (payload) => request('/api/inventory/entries', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }),
  updateInventoryEntry: (id, payload) => request(`/api/inventory/entries/${id}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }),
  deleteInventoryEntry: (id) => request(`/api/inventory/entries/${id}`, { method: 'DELETE' }),
  network: () => request('/api/network'),
  vehicles: () => request('/api/vehicles'),
  createWarehouse: (payload) => request('/api/warehouses', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }),
  deleteWarehouse: (id, force = true) => request(`/api/warehouses/${encodeURIComponent(id)}?force=${force}`, { method: 'DELETE' }),
  createDestination: (payload) => request('/api/destinations', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }),
  deleteDestination: (id, force = true) => request(`/api/destinations/${encodeURIComponent(id)}?force=${force}`, { method: 'DELETE' }),
  createVehicle: (payload) => request('/api/vehicles', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }),
  deleteVehicle: (id) => request(`/api/vehicles/${encodeURIComponent(id)}`, { method: 'DELETE' }),
routeGeometry: (ids) => request(`/api/routes/geometry?ids=${encodeURIComponent(ids.join(',') )}`),
  generateRoutes: (payload) => request('/api/routes/generate', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }),
  deleteRoute: (id) => request(`/api/routes/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  deleteEvent: (id, force = true) => request(`/api/events/${encodeURIComponent(id)}?force=${force}`, { method: 'DELETE' }),
  readiness: () => request('/api/readiness'),
  events: () => request('/api/events'),
  geocode: (address) => request(`/api/geocode?address=${encodeURIComponent(address)}`),
  createEvent: (payload) => request('/api/events', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }),
  tasks: (runId = '') => request(`/api/tasks${runId ? `?run_id=${encodeURIComponent(runId)}` : ''}`),
  scenarios: () => request('/api/scenarios'),
  startRun: (payload) => request('/api/runs', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }),
  getRun: (id) => request(`/api/runs/${id}`),
  listRuns: () => request('/api/runs'),
  queueMetrics: () => request('/api/queue/metrics'),
  retryJobs: (status = '') => request(`/api/retry-jobs${status ? `?status=${encodeURIComponent(status)}` : ''}`),
  replayRetryJob: (id) => request(`/api/retry-jobs/${encodeURIComponent(id)}/replay`, { method: 'POST' }),
  inventoryReservations: () => request('/api/inventory/reservations'),
  releaseReservation: (id) => request(`/api/runs/${id}/release-reservation`, { method: 'POST' }),
  approveRun: (id, note = '') => request(`/api/runs/${id}/approve`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ note }),
  }),
  uploadMaterials: (file) => {
    const form = new FormData()
    form.append('file', file)
    return request('/api/materials/import', { method: 'POST', body: form })
  },
  uploadInventory: (file) => {
    const form = new FormData()
    form.append('file', file)
    return request('/api/inventory/import', { method: 'POST', body: form })
  },
  uploadResource: (kind, file) => {
    const form = new FormData()
    form.append('file', file)
    return request(`/api/${kind}/import`, { method: 'POST', body: form })
  },
  updateTask: (id, payload) => request(`/api/tasks/${id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }),
}

export function formatNumber(value, digits = 0) {
  return Number(value || 0).toLocaleString('zh-CN', { maximumFractionDigits: digits })
}

export function formatTime(value) {
  if (!value) return '--'
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}
