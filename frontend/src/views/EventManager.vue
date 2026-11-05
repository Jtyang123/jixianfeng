<script setup>
import { onMounted, ref } from 'vue'
import { AlertTriangle, CheckCircle2, Clock3, Download, FilePlus2, LocateFixed, MapPinned, Plus, Trash2, Upload } from 'lucide-vue-next'
import { api, formatTime } from '../api'

const events = ref([])
const loading = ref(true)
const saving = ref(false)
const importing = ref(false)
const geocoding = ref(false)
const geocodeMessage = ref('')
const error = ref('')
const message = ref('')
const deletingId = ref('')
const emit = defineEmits(['refresh-dashboard'])
const form = ref({
  title: '', event_type: '暴雨洪涝', severity: 'II级', location: '',
  affected_population: 0, description: '', source: 'manual', source_ref: '',
  lat: null, lng: null, closed_routes_text: '',
})

async function load() {
  loading.value = true
  try { events.value = await api.events() } catch (exc) { error.value = exc.message } finally { loading.value = false }
}

async function locateAddress() {
  if (!form.value.location) {
    error.value = '请先填写影响区域'
    return
  }
  geocoding.value = true
  error.value = ''
  geocodeMessage.value = ''
  try {
    const result = await api.geocode(form.value.location)
    form.value.lat = result.lat
    form.value.lng = result.lng
    form.value.location = result.formatted_address || form.value.location
    geocodeMessage.value = `已定位：${result.lat.toFixed(6)}, ${result.lng.toFixed(6)}`
  } catch (exc) { error.value = exc.message } finally { geocoding.value = false }
}

async function importEvent(fileEvent) {
  const file = fileEvent.target.files?.[0]
  if (!file) return
  importing.value = true
  error.value = ''
  message.value = ''
  try {
    const formData = new FormData()
    formData.append('file', file)
    const response = await fetch('/api/events/import', { method: 'POST', body: formData })
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}))
      throw new Error(payload.detail || `导入失败 (${response.status})`)
    }
    const event = await response.json()
    events.value.unshift(event)
    message.value = `事件 ${event.id} 已导入，包含 ${event.demand_items?.length || 0} 条需求`
  } catch (exc) { error.value = exc.message } finally {
    importing.value = false
    fileEvent.target.value = ''
  }
}

function validateEvent() {
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
    events.value.unshift(event)
    message.value = `事件 ${event.id} 已创建并确认`
    form.value = { title: '', event_type: '暴雨洪涝', severity: 'II级', location: '', affected_population: 0, description: '', source: 'manual', source_ref: '', lat: null, lng: null, closed_routes_text: '' }
  } catch (exc) { error.value = exc.message } finally { saving.value = false }
}

async function removeEvent(event) {
  const confirmed = window.confirm(`确定删除事件“${event.title}”吗？\n\n该事件关联的调度运行、智能体记录和运输任务也会一并删除，此操作不可撤销。`)
  if (!confirmed) return
  deletingId.value = event.id
  error.value = ''
  message.value = ''
  try {
    await api.deleteEvent(event.id)
    events.value = events.value.filter((item) => item.id !== event.id)
    message.value = `事件“${event.title}”已删除`
    emit('refresh-dashboard')
  } catch (exc) {
    error.value = exc.message
  } finally {
    deletingId.value = ''
  }
}

onMounted(load)
</script>

<template>
  <div class="event-page">
    <section class="event-layout">
      <div class="content-card event-form-card">
        <div class="workflow-head"><div><div class="eyebrow">OPERATIONAL EVENT INTAKE</div><h2>导入或录入真实事件</h2><p>支持手工创建，也支持导入包含事件和需求明细的 Excel。</p></div><FilePlus2 :size="20" /></div>
        <div class="event-import-bar">
          <a class="secondary-action" href="/api/events/template" download><Download :size="14" />下载场景模板</a>
          <label class="upload-label"><Upload :size="14" />{{ importing ? '导入中...' : '导入真实场景' }}<input type="file" accept=".xlsx,.csv,.json" @change="importEvent" /></label>
        </div>
        <div class="form-grid event-form">
          <label><span>事件标题 *</span><input v-model="form.title" maxlength="160" placeholder="例如：某区暴雨洪涝应急事件" /></label>
          <label><span>事件类型</span><select v-model="form.event_type"><option>暴雨洪涝</option><option>台风</option><option>地震</option><option>火灾</option><option>道路中断</option><option>其他</option></select></label>
          <label><span>等级</span><select v-model="form.severity"><option>I级</option><option>II级</option><option>III级</option><option>IV级</option></select></label>
          <label class="full-span"><span>影响区域 *</span><div class="geocode-field"><input v-model="form.location" maxlength="160" placeholder="区县、街道或具体区域" /><button type="button" class="secondary-action" :disabled="geocoding" @click="locateAddress"><LocateFixed :size="14" />{{ geocoding ? '定位中...' : '地图定位' }}</button></div><small v-if="geocodeMessage" class="geocode-success">{{ geocodeMessage }}</small></label>
          <label><span>纬度</span><input v-model.number="form.lat" type="number" step="0.000001" placeholder="例如 30.5728" /></label>
          <label><span>经度</span><input v-model.number="form.lng" type="number" step="0.000001" placeholder="例如 114.3073" /></label>
          <label><span>影响人口</span><input v-model.number="form.affected_population" type="number" min="0" /></label>
          <label><span>来源</span><input v-model="form.source" placeholder="manual / 应急值班 / 气象通知" /></label>
          <label class="full-span"><span>事件描述</span><textarea v-model="form.description" rows="4" placeholder="记录官方通知、现场情况、已确认的道路阻断等" /></label>
          <label class="full-span"><span>封闭路线编号（逗号分隔，可空）</span><input v-model="form.closed_routes_text" placeholder="R-ROUTE-01,R-ROUTE-02" /></label>
        </div>
        <div v-if="error" class="inline-error">{{ error }}</div>
        <div v-if="message" class="import-result"><CheckCircle2 :size="17" />{{ message }}</div>
        <button class="primary-action event-submit" :disabled="saving" @click="createEvent"><Plus :size="16" />{{ saving ? '创建中...' : '确认并创建事件' }}</button>
      </div>

      <div class="content-card event-list-card">
        <div class="workflow-head"><div><div class="eyebrow">CONFIRMED EVENTS</div><h2>已确认事件</h2></div><Clock3 :size="20" /></div>
        <div v-if="events.length" class="event-record-list">
          <article v-for="event in events" :key="event.id" class="event-record">
            <div class="event-record-head"><span class="severity-badge">{{ event.severity }}</span><strong>{{ event.title }}</strong><button type="button" class="record-delete" :disabled="deletingId === event.id" @click="removeEvent(event)"><Trash2 :size="12" />{{ deletingId === event.id ? '删除中' : '删除' }}</button><span class="run-status run-status--awaiting_approval">{{ event.status }}</span></div>
            <p>{{ event.description || '无补充描述' }}</p>
            <div><span><MapPinned :size="13" />{{ event.location }}</span><span>{{ event.affected_population }} 人</span><span>{{ event.demand_items?.length || 0 }} 条需求</span><span>{{ formatTime(event.created_at) }}</span></div>
            <small>{{ event.id }} · 来源：{{ event.source }}</small>
          </article>
        </div>
        <div v-else-if="!loading" class="empty-table"><AlertTriangle :size="30" /><strong>尚未录入真实事件</strong><span>有官方通知或人工确认事件时，在左侧创建。</span></div>
      </div>
    </section>
  </div>
</template>
