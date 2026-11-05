from pathlib import Path

vue_path = Path('frontend/src/views/EventManager.vue')
vue = vue_path.read_text(encoding='utf-8').replace('\r\n', '\n')
old = '''async function createEvent() {
  saving.value = true
  error.value = ''
  message.value = ''
  try {
    const payload = {
      ...form.value,
      closed_routes: form.value.closed_routes_text.split(',').map((item) => item.trim()).filter(Boolean),
    }
    delete payload.closed_routes_text
    const event = await api.createEvent(payload)
'''
new = '''function validateEvent() {
  const title = form.value.title.trim()
  const location = form.value.location.trim()
  if (title.length < 2) {
    error.value = '事件标题至少填写 2 个字符，例如“江汉区暴雨洪涝应急事件”'
    return false
  }
  if (title.length > 160) {
    error.value = '事件标题不能超过 160 个字符'
    return false
  }
  if (location.length < 2) {
    error.value = '影响区域至少填写 2 个字符'
    return false
  }
  if (Number(form.value.affected_population || 0) < 0) {
    error.value = '影响人口不能小于 0'
    return false
  }
  return true
}

async function createEvent() {
  if (!validateEvent()) return
  saving.value = true
  error.value = ''
  message.value = ''
  try {
    const payload = {
      ...form.value,
      title: form.value.title.trim(),
      location: form.value.location.trim(),
      description: form.value.description.trim(),
      source: form.value.source.trim() || 'manual',
      closed_routes: form.value.closed_routes_text.split(',').map((item) => item.trim()).filter(Boolean),
    }
    delete payload.closed_routes_text
    const event = await api.createEvent(payload)
'''
if old not in vue:
    raise SystemExit('EventManager createEvent block not found')
vue = vue.replace(old, new)
vue = vue.replace('<input v-model="form.title" placeholder=', '<input v-model="form.title" maxlength="160" placeholder=')
vue = vue.replace('<input v-model="form.location" placeholder=', '<input v-model="form.location" maxlength="160" placeholder=')
vue = vue.replace(':disabled="saving || !form.title || !form.location" @click="createEvent"', ':disabled="saving" @click="createEvent"')
vue_path.write_text(vue, encoding='utf-8')

api_path = Path('frontend/src/api.js')
api = api_path.read_text(encoding='utf-8').replace('\r\n', '\n')
old_api = '''async function request(path, options = {}) {
  const response = await fetch(path, options)
  if (!response.ok) {
    let detail = `请求失败 (${response.status})`
    try {
      const payload = await response.json()
      detail = payload.detail || payload.message || detail
      if (typeof detail === 'object') detail = detail.message || JSON.stringify(detail)
    } catch (_) {}
    throw new Error(detail)
  }
  return response.json()
}
'''
new_api = '''const FIELD_LABELS = {
  title: '事件标题',
  location: '影响区域',
  affected_population: '影响人口',
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
    throw new Error(detail)
  }
  return response.json()
}
'''
if old_api not in api:
    raise SystemExit('api request block not found')
api_path.write_text(api.replace(old_api, new_api), encoding='utf-8')
print('Frontend validation updated')