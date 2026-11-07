const FIELD_LABELS = {
  title: '事件标题',
  location: '影响区域',
  affected_population: '影响人口',
  name: '仓库名称',
  region: '仓库地址',
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
  capacity_kg: '载重',
}

function describeErrorDetail(detail, status) {
  if (typeof detail === 'string' && detail) return detail
  if (Array.isArray(detail)) {
    return detail.map((item) => {
      const field = (item.loc || []).filter((part) => !['body', 'query', 'path'].includes(part)).join('.')
      const label = FIELD_LABELS[field] || field
      let message = item.msg || '填写内容不符合要求'
      if (message.includes('at least 2 characters')) message = '至少需要填写 2 个字符'
      if (message.includes('at most 160 characters')) message = '不能超过 160 个字符'
      if (message.includes('greater than or equal to 0')) message = '不能小于 0'
      return `${label ? `${label}：` : ''}${message}`
    }).join('；')
  }
  if (detail && typeof detail === 'object') return detail.message || '提交内容不符合要求'
  return `请求失败 (${status})`
}

async function request(path, options = {}) {
  const response = await fetch(path, options)
  if (!response.ok) {
    let detail = `请求失败 (${response.status})`
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
  auditLogs: (limit = 200) => request(`/api/auth/audit-logs?limit=${limit}`),
  agents: () => request('/api/agents'),
  discoverAgents: (role = '') => request(`/api/agents/discover${role ? `?role=${encodeURIComponent(role)}` : ''}`),
  registerAgent: (payload) => request('/api/agents', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }),
  addAgentVersion: (id, payload) => request(`/api/agents/${encodeURIComponent(id)}/versions`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }),
  activateAgentVersion: (id, version) => request(`/api/agents/${encodeURIComponent(id)}/versions/${encodeURIComponent(version)}/activate`, { method: 'POST' }),
  invokeAgent: (id, payload) => request(`/api/agents/${encodeURIComponent(id)}/invoke`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }),
  dashboard: () => request('/api/dashboard'),
  systemStatus: () => request('/api/system/status'),
  checkAgentArts: () => request('/api/agentarts/check'),
  manifest: () => request('/api/platform/manifest'),
  benchmark: () => request('/api/evaluation/benchmark'),
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
