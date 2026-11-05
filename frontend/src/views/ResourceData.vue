<script setup>
import { computed, onMounted, ref } from 'vue'
import {
  CheckCircle2, Download, LocateFixed, MapPinned, Plus, Radio, Trash2, Truck,
  Upload, Warehouse as WarehouseIcon,
} from 'lucide-vue-next'
import { api, formatTime } from '../api'

const emit = defineEmits(['refresh-dashboard'])
const readiness = ref(null)
const network = ref({ warehouses: [], destinations: [], routes: [], vehicles: [] })
const loading = ref(true)
const uploading = ref('')
const message = ref('')
const error = ref('')
const requestedTab = new URLSearchParams(window.location.search).get('resourceTab')
const validTabs = ['warehouses', 'destinations', 'vehicles', 'routes', 'imports']
const resourceTab = ref(validTabs.includes(requestedTab) ? requestedTab : 'warehouses')
const warehouseSaving = ref(false)
const warehouseGeocoding = ref(false)
const warehouseGeocodeMessage = ref('')
const warehouseMessage = ref('')
const deletingWarehouseId = ref('')
const warehouseForm = ref({ name: '', region: '', capacity: 0, status: 'open', lat: null, lng: null })
const destinationSaving = ref(false)
const destinationGeocoding = ref(false)
const destinationGeocodeMessage = ref('')
const destinationMessage = ref('')
const deletingDestinationId = ref('')
const destinationForm = ref({ name: '', address: '', population: 0, contact: '', lat: null, lng: null })
const vehicleSaving = ref(false)
const vehicleMessage = ref('')
const deletingVehicleId = ref('')
const vehicleForm = ref({ plate_no: '', vehicle_type: '4.2米厢式货车', capacity_kg: 4200, temp_zone: '常温', warehouse_id: '', status: 'available', lat: null, lng: null })
const routeGenerating = ref(false)
const routeMessage = ref('')
const routeWarnings = ref([])
const deletingRouteId = ref('')
const builtinWarehouseIds = new Set(['WH-A', 'WH-B', 'WH-C', 'WH-D'])
const builtinDestinationIds = new Set(['DST-01', 'DST-02', 'DST-03'])
const routableWarehouses = computed(() => network.value.warehouses.filter((item) => !builtinWarehouseIds.has(item.id) && item.lat != null && item.lng != null && item.status === 'open'))
const routableDestinations = computed(() => network.value.destinations.filter((item) => !builtinDestinationIds.has(item.id) && item.lat != null && item.lng != null))
const visibleRoutes = computed(() => network.value.routes.filter((item) => !builtinWarehouseIds.has(item.warehouse_id) && !builtinDestinationIds.has(item.destination_id)))
const uploadCards = [
  { kind: 'warehouses', title: '仓库数据', desc: '仓库编码、名称、经纬度、容量', icon: WarehouseIcon },
  { kind: 'destinations', title: '安置点数据', desc: '安置点编码、地址、经纬度、人口、联系人', icon: MapPinned },
  { kind: 'vehicles', title: '车辆数据', desc: '车辆载重、温区、所属仓库、初始位置', icon: Truck },
  { kind: 'routes', title: '路线数据', desc: '仓库到安置点的路线、ETA、成本', icon: Radio },
]
const readinessCards = computed(() => {
  const counts = readiness.value?.counts || {}
  return [
    { label: '物资主数据', value: counts.materials || 0, unit: '条', state: counts.materials ? 'ready' : 'empty' },
    { label: '仓库', value: counts.warehouses || 0, unit: '个', state: counts.warehouses ? 'ready' : 'empty' },
    { label: '安置点', value: counts.destinations || 0, unit: '个', state: counts.destinations ? 'ready' : 'empty' },
    { label: '车辆', value: counts.vehicles || 0, unit: '辆', state: counts.vehicles ? 'ready' : 'empty' },
    { label: 'GPS 在线车辆', value: readiness.value?.gps_vehicles || 0, unit: '辆', state: readiness.value?.gps_vehicles ? 'ready' : 'pending' },
    { label: '运营事件', value: counts.operational_events || 0, unit: '个', state: counts.operational_events ? 'ready' : 'empty' },
  ]
})
function switchResourceTab(tab) {
  resourceTab.value = tab
  const url = new URL(window.location.href)
  url.searchParams.set('resourceTab', tab)
  window.history.replaceState({}, '', url)
}
function gpsState(vehicle) {
  if (!vehicle.last_position_at) return '未接入 GPS'
  if (vehicle.gps_source === 'warehouse' || vehicle.gps_source === 'manual') return '仓库/手工位置'
  return 'GPS 已接入'
}
function vehicleStatusLabel(status) {
  return ({ available: '可调度', maintenance: '维护中', unavailable: '不可用' })[status] || status
}
async function load() {
  loading.value = true
  try {
    const [readinessData, networkData] = await Promise.all([api.readiness(), api.network()])
    readiness.value = readinessData
    network.value = networkData
    if (!vehicleForm.value.warehouse_id && networkData.warehouses.length) {
      vehicleForm.value.warehouse_id = networkData.warehouses[0].id
      syncVehicleLocation()
    }
  } catch (exc) { error.value = exc.message } finally { loading.value = false }
}
function resetWarehouseForm() {
  warehouseForm.value = { name: '', region: '', capacity: 0, status: 'open', lat: null, lng: null }
  warehouseGeocodeMessage.value = ''
}
async function locateWarehouse() {
  if (!warehouseForm.value.region.trim()) { error.value = '请先填写仓库地址或所在区域'; return }
  warehouseGeocoding.value = true
  error.value = ''
  warehouseGeocodeMessage.value = ''
  try {
    const result = await api.geocode(warehouseForm.value.region)
    warehouseForm.value.lat = result.lat
    warehouseForm.value.lng = result.lng
    warehouseForm.value.region = result.formatted_address || warehouseForm.value.region
    warehouseGeocodeMessage.value = `已定位：${result.lat.toFixed(6)}, ${result.lng.toFixed(6)}`
  } catch (exc) { error.value = exc.message } finally { warehouseGeocoding.value = false }
}
async function createWarehouse() {
  const name = warehouseForm.value.name.trim()
  const region = warehouseForm.value.region.trim()
  const capacity = Number(warehouseForm.value.capacity || 0)
  const lat = Number(warehouseForm.value.lat)
  const lng = Number(warehouseForm.value.lng)
  if (name.length < 2) { error.value = '仓库名称至少填写 2 个字符'; return }
  if (region.length < 2) { error.value = '仓库地址至少填写 2 个字符'; return }
  if (!Number.isFinite(lat) || lat < -90 || lat > 90 || !Number.isFinite(lng) || lng < -180 || lng > 180) { error.value = '请点击“地图定位”，或手工填写有效经纬度'; return }
  if (!Number.isFinite(capacity) || capacity < 0) { error.value = '仓库容量不能小于 0'; return }
  warehouseSaving.value = true
  error.value = ''
  warehouseMessage.value = ''
  try {
    const created = await api.createWarehouse({ name, region, capacity, status: warehouseForm.value.status, lat, lng })
    await load()
    warehouseMessage.value = `仓库“${created.name}”已录入，可在调度工作台地图查看`
    resetWarehouseForm()
    emit('refresh-dashboard')
  } catch (exc) { error.value = exc.message } finally { warehouseSaving.value = false }
}
async function removeWarehouse(warehouse) {
  const confirmed = window.confirm(`确定删除仓库“${warehouse.name}”吗？\\n\\n相关路线、库存、车辆和 GPS 记录会一并删除。此操作不可撤销。`)
  if (!confirmed) return
  deletingWarehouseId.value = warehouse.id
  error.value = ''
  warehouseMessage.value = ''
  try {
    await api.deleteWarehouse(warehouse.id, true)
    await load()
    warehouseMessage.value = `仓库“${warehouse.name}”已删除`
    emit('refresh-dashboard')
  } catch (exc) { error.value = exc.message } finally { deletingWarehouseId.value = '' }
}
function resetDestinationForm() {
  destinationForm.value = { name: '', address: '', population: 0, contact: '', lat: null, lng: null }
  destinationGeocodeMessage.value = ''
}
async function locateDestination() {
  if (!destinationForm.value.address.trim()) { error.value = '请先填写安置点地址'; return }
  destinationGeocoding.value = true
  error.value = ''
  destinationGeocodeMessage.value = ''
  try {
    const result = await api.geocode(destinationForm.value.address)
    destinationForm.value.lat = result.lat
    destinationForm.value.lng = result.lng
    destinationForm.value.address = result.formatted_address || destinationForm.value.address
    destinationGeocodeMessage.value = `已定位：${result.lat.toFixed(6)}, ${result.lng.toFixed(6)}`
  } catch (exc) { error.value = exc.message } finally { destinationGeocoding.value = false }
}
async function createDestination() {
  const name = destinationForm.value.name.trim()
  const address = destinationForm.value.address.trim()
  const lat = Number(destinationForm.value.lat)
  const lng = Number(destinationForm.value.lng)
  if (name.length < 2) { error.value = '安置点名称至少填写 2 个字符'; return }
  if (address.length < 2) { error.value = '安置点地址至少填写 2 个字符'; return }
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) { error.value = '请点击“地图定位”，或手工填写有效经纬度'; return }
  destinationSaving.value = true
  error.value = ''
  destinationMessage.value = ''
  try {
    const created = await api.createDestination({ name, address, lat, lng, population: Number(destinationForm.value.population || 0), contact: destinationForm.value.contact.trim() })
    await load()
    destinationMessage.value = `安置点“${created.name}”已录入，可在调度工作台地图查看`
    resetDestinationForm()
    emit('refresh-dashboard')
  } catch (exc) { error.value = exc.message } finally { destinationSaving.value = false }
}
async function removeDestination(destination) {
  const confirmed = window.confirm(`确定删除安置点“${destination.name}”吗？\\n\\n关联路线会一并删除，此操作不可撤销。`)
  if (!confirmed) return
  deletingDestinationId.value = destination.id
  error.value = ''
  destinationMessage.value = ''
  try {
    await api.deleteDestination(destination.id, true)
    await load()
    destinationMessage.value = `安置点“${destination.name}”已删除`
    emit('refresh-dashboard')
  } catch (exc) { error.value = exc.message } finally { deletingDestinationId.value = '' }
}
function syncVehicleLocation() {
  const warehouse = network.value.warehouses.find((item) => item.id === vehicleForm.value.warehouse_id)
  if (warehouse) { vehicleForm.value.lat = warehouse.lat; vehicleForm.value.lng = warehouse.lng }
}
async function createVehicle() {
  if (!vehicleForm.value.warehouse_id) { error.value = '请选择所属仓库'; return }
  if (vehicleForm.value.plate_no.trim().length < 2) { error.value = '请填写车牌号'; return }
  if (Number(vehicleForm.value.capacity_kg) <= 0) { error.value = '车辆载重必须大于 0'; return }
  vehicleSaving.value = true
  error.value = ''
  vehicleMessage.value = ''
  try {
    const created = await api.createVehicle({ plate_no: vehicleForm.value.plate_no.trim(), vehicle_type: vehicleForm.value.vehicle_type.trim(), capacity_kg: Number(vehicleForm.value.capacity_kg), temp_zone: vehicleForm.value.temp_zone, warehouse_id: vehicleForm.value.warehouse_id, status: vehicleForm.value.status, lat: vehicleForm.value.lat, lng: vehicleForm.value.lng })
    await load()
    vehicleMessage.value = `车辆“${created.plate_no}”已录入，可在调度工作台地图查看`
    vehicleForm.value = { plate_no: '', vehicle_type: '4.2米厢式货车', capacity_kg: 4200, temp_zone: '常温', warehouse_id: network.value.warehouses[0]?.id || '', status: 'available', lat: null, lng: null }
    syncVehicleLocation()
    emit('refresh-dashboard')
  } catch (exc) { error.value = exc.message } finally { vehicleSaving.value = false }
}
async function removeVehicle(vehicle) {
  const confirmed = window.confirm(`确定删除车辆“${vehicle.plate_no}”吗？\\n\\n车辆的 GPS 位置历史也会一并删除。`)
  if (!confirmed) return
  deletingVehicleId.value = vehicle.id
  error.value = ''
  vehicleMessage.value = ''
  try {
    await api.deleteVehicle(vehicle.id)
    await load()
    vehicleMessage.value = `车辆“${vehicle.plate_no}”已删除`
    emit('refresh-dashboard')
  } catch (exc) { error.value = exc.message } finally { deletingVehicleId.value = '' }
}
function routeWarehouseName(id) {
  return network.value.warehouses.find((item) => item.id === id)?.name || id
}

function routeDestinationName(id) {
  return network.value.destinations.find((item) => item.id === id)?.name || id
}

function routeSourceLabel(source) {
  return ({ osrm: 'OSRM 实时路网', fallback: '离线估算', manual: '手工导入', import: '批量导入' })[source] || source || '手工'
}

async function generateRoutes() {
  if (!routableWarehouses.value.length || !routableDestinations.value.length) {
    error.value = '至少需要一个已定位的业务仓库和一个已定位的业务安置点'
    return
  }
  routeGenerating.value = true
  error.value = ''
  routeMessage.value = ''
  routeWarnings.value = []
  try {
    const result = await api.generateRoutes({
      warehouse_ids: routableWarehouses.value.map((item) => item.id),
      destination_ids: routableDestinations.value.map((item) => item.id),
      replace_generated: true,
    })
    await load()
    routeMessage.value = `已生成 ${result.generated} 条路线：OSRM 实时规划 ${result.osrm} 条，离线回退 ${result.fallback} 条`
    routeWarnings.value = result.warnings || []
    emit('refresh-dashboard')
  } catch (exc) {
    error.value = exc.message
  } finally {
    routeGenerating.value = false
  }
}

async function removeRoute(route) {
  const confirmed = window.confirm(`确定删除路线“${route.road_name}”吗？`)
  if (!confirmed) return
  deletingRouteId.value = route.id
  error.value = ''
  routeMessage.value = ''
  try {
    await api.deleteRoute(route.id)
    await load()
    routeMessage.value = '路线已删除'
    emit('refresh-dashboard')
  } catch (exc) {
    error.value = exc.message
  } finally {
    deletingRouteId.value = ''
  }
}

async function upload(kind, event) {
  const file = event.target.files?.[0]
  if (!file) return
  uploading.value = kind
  message.value = ''
  error.value = ''
  try {
    const result = await api.uploadResource(kind, file)
    message.value = `${kind}：成功导入 ${result.imported} 条${result.errors?.length ? `，错误 ${result.errors.length} 条` : ''}`
    await load()
    emit('refresh-dashboard')
  } catch (exc) { error.value = exc.message } finally { uploading.value = ''; event.target.value = '' }
}
onMounted(load)
</script>
<template>
  <div class="resource-page">
    <section class="data-readiness">
      <div class="data-readiness-head">
        <div><div class="eyebrow">OPERATIONAL DATA READINESS</div><h2>真实数据接入状态</h2><p>仓库、安置点、车辆和 GPS 数据均可逐条维护，也保留 CSV / Excel 批量导入。</p></div>
        <span class="mode-badge">{{ readiness?.mode === 'operational' ? '运营模式' : '演示模式' }}</span>
      </div>
      <div class="readiness-grid">
        <div v-for="item in readinessCards" :key="item.label" class="readiness-card" :class="`readiness-card--${item.state}`">
          <span>{{ item.label }}</span><strong>{{ item.value }}</strong><small>{{ item.unit }}</small>
        </div>
      </div>
      <div class="readiness-lines">
        <span><CheckCircle2 :size="13" />事件来源：{{ readiness?.event_source }}</span>
        <span><CheckCircle2 :size="13" />车辆来源：{{ readiness?.vehicle_source }}</span>
        <span><CheckCircle2 :size="13" />地图：{{ readiness?.map_provider }}</span>
      </div>
    </section>

    <div v-if="message" class="import-result"><CheckCircle2 :size="17" />{{ message }}</div>
    <div v-if="error" class="inline-error">{{ error }}</div>

    <div class="resource-subtabs">
      <button :class="{ active: resourceTab === 'warehouses' }" @click="switchResourceTab('warehouses')"><WarehouseIcon :size="16" /><span>应急仓库<small>录入、定位、删除</small></span></button>
      <button :class="{ active: resourceTab === 'destinations' }" @click="switchResourceTab('destinations')"><MapPinned :size="16" /><span>安置点<small>录入、定位、删除</small></span></button>
      <button :class="{ active: resourceTab === 'vehicles' }" @click="switchResourceTab('vehicles')"><Truck :size="16" /><span>车辆<small>仓库绑定、GPS 状态</small></span></button>
      <button :class="{ active: resourceTab === 'routes' }" @click="switchResourceTab('routes')"><Radio :size="16" /><span>路线规划<small>OSRM 自动生成</small></span></button>
      <button :class="{ active: resourceTab === 'imports' }" @click="switchResourceTab('imports')"><Upload :size="16" /><span>批量导入<small>CSV / Excel</small></span></button>
    </div>

    <section v-if="resourceTab === 'warehouses'" class="content-card warehouse-manager">
      <div class="workflow-head">
        <div><div class="eyebrow">EMERGENCY WAREHOUSE DIRECTORY</div><h2>应急仓库录入与定位</h2><p>逐个录入真实仓库，地址解析后自动写入经纬度；调度工作台地图会显示新增仓库。</p></div>
        <a class="secondary-action" href="/?page=workbench"><MapPinned :size="14" />到地图查看</a>
      </div>
      <form class="warehouse-form-grid" @submit.prevent="createWarehouse">
        <label><span>仓库名称 *</span><input v-model="warehouseForm.name" maxlength="120" placeholder="例如：江汉区应急物资中心库" /></label>
        <label><span>容量</span><input v-model.number="warehouseForm.capacity" type="number" min="0" step="1" placeholder="例如 20000" /></label>
        <label><span>调度状态</span><select v-model="warehouseForm.status"><option value="open">可调度</option><option value="closed">停用</option></select></label>
        <label class="full-span"><span>仓库地址 / 所在区域 *</span><div class="geocode-field"><input v-model="warehouseForm.region" maxlength="200" placeholder="例如：武汉市江汉区江汉路" /><button type="button" class="secondary-action" :disabled="warehouseGeocoding" @click="locateWarehouse"><LocateFixed :size="14" />{{ warehouseGeocoding ? '定位中...' : '地图定位' }}</button></div><small v-if="warehouseGeocodeMessage" class="geocode-success">{{ warehouseGeocodeMessage }}</small></label>
        <label><span>纬度 *</span><input v-model.number="warehouseForm.lat" type="number" min="-90" max="90" step="0.000001" placeholder="例如 30.5728" /></label>
        <label><span>经度 *</span><input v-model.number="warehouseForm.lng" type="number" min="-180" max="180" step="0.000001" placeholder="例如 114.3073" /></label>
        <div class="warehouse-form-actions full-span">
          <span v-if="warehouseMessage" class="warehouse-success"><CheckCircle2 :size="14" />{{ warehouseMessage }}</span>
          <span v-else class="warehouse-hint">地图默认隐藏系统内置演示点位，只显示您录入或导入的业务仓库。</span>
          <button type="submit" class="primary-action" :disabled="warehouseSaving"><Plus :size="15" />{{ warehouseSaving ? '保存中...' : '创建仓库' }}</button>
        </div>
      </form>
      <div class="table-wrap warehouse-table-wrap">
        <table class="data-table">
          <thead><tr><th>仓库</th><th>地址 / 区域</th><th>容量</th><th>经纬度</th><th>状态</th><th>操作</th></tr></thead>
          <tbody><tr v-for="warehouse in network.warehouses" :key="warehouse.id">
            <td><strong>{{ warehouse.name }}</strong><small class="cell-sub">{{ warehouse.id }}<span v-if="builtinWarehouseIds.has(warehouse.id)" class="demo-tag">内置演示</span></small></td>
            <td>{{ warehouse.region }}</td>
            <td>{{ Number(warehouse.capacity || 0).toLocaleString('zh-CN') }}</td>
            <td class="mono-cell">{{ warehouse.lat != null && warehouse.lng != null ? `${Number(warehouse.lat).toFixed(6)}, ${Number(warehouse.lng).toFixed(6)}` : '未定位' }}</td>
            <td><span class="warehouse-state" :class="{ 'warehouse-state--closed': warehouse.status !== 'open' }">{{ warehouse.status === 'open' ? '可调度' : '停用' }}</span></td>
            <td><button type="button" class="record-delete" :disabled="deletingWarehouseId === warehouse.id" @click="removeWarehouse(warehouse)"><Trash2 :size="12" />{{ deletingWarehouseId === warehouse.id ? '删除中' : '删除' }}</button></td>
          </tr></tbody>
        </table>
        <div v-if="!network.warehouses.length" class="empty-table"><WarehouseIcon :size="30" /><strong>尚未录入仓库</strong><span>在上方填写仓库信息并点击地图定位。</span></div>
      </div>
    </section>
    <section v-else-if="resourceTab === 'destinations'" class="content-card warehouse-manager">
      <div class="workflow-head">
        <div><div class="eyebrow">SHELTER DIRECTORY</div><h2>安置点录入与定位</h2><p>录入真实安置点地址、人口和联系人，保存后会显示在调度工作台地图。</p></div>
        <a class="secondary-action" href="/?page=workbench"><MapPinned :size="14" />到地图查看</a>
      </div>
      <form class="warehouse-form-grid" @submit.prevent="createDestination">
        <label><span>安置点名称 *</span><input v-model="destinationForm.name" maxlength="120" placeholder="例如：江汉区第一中学安置点" /></label>
        <label><span>安置人口</span><input v-model.number="destinationForm.population" type="number" min="0" /></label>
        <label><span>现场联系人</span><input v-model="destinationForm.contact" maxlength="120" placeholder="姓名 / 电话" /></label>
        <label class="full-span"><span>安置点地址 *</span><div class="geocode-field"><input v-model="destinationForm.address" maxlength="200" placeholder="例如：武汉市江汉区某学校" /><button type="button" class="secondary-action" :disabled="destinationGeocoding" @click="locateDestination"><LocateFixed :size="14" />{{ destinationGeocoding ? '定位中...' : '地图定位' }}</button></div><small v-if="destinationGeocodeMessage" class="geocode-success">{{ destinationGeocodeMessage }}</small></label>
        <label><span>纬度 *</span><input v-model.number="destinationForm.lat" type="number" min="-90" max="90" step="0.000001" /></label>
        <label><span>经度 *</span><input v-model.number="destinationForm.lng" type="number" min="-180" max="180" step="0.000001" /></label>
        <div class="warehouse-form-actions full-span">
          <span v-if="destinationMessage" class="warehouse-success"><CheckCircle2 :size="14" />{{ destinationMessage }}</span>
          <span v-else class="warehouse-hint">地图默认隐藏内置演示安置点，只显示您录入或导入的业务点位。</span>
          <button type="submit" class="primary-action" :disabled="destinationSaving"><Plus :size="15" />{{ destinationSaving ? '保存中...' : '创建安置点' }}</button>
        </div>
      </form>
      <div class="table-wrap warehouse-table-wrap">
        <table class="data-table">
          <thead><tr><th>安置点</th><th>地址</th><th>人口</th><th>经纬度</th><th>联系人</th><th>操作</th></tr></thead>
          <tbody><tr v-for="destination in network.destinations" :key="destination.id">
            <td><strong>{{ destination.name }}</strong><small class="cell-sub">{{ destination.id }}<span v-if="builtinDestinationIds.has(destination.id)" class="demo-tag">内置演示</span></small></td>
            <td>{{ destination.address || '未填写' }}</td>
            <td>{{ Number(destination.population || 0).toLocaleString('zh-CN') }}</td>
            <td class="mono-cell">{{ destination.lat != null && destination.lng != null ? `${Number(destination.lat).toFixed(6)}, ${Number(destination.lng).toFixed(6)}` : '未定位' }}</td>
            <td>{{ destination.contact || '--' }}</td>
            <td><button type="button" class="record-delete" :disabled="deletingDestinationId === destination.id" @click="removeDestination(destination)"><Trash2 :size="12" />{{ deletingDestinationId === destination.id ? '删除中' : '删除' }}</button></td>
          </tr></tbody>
        </table>
        <div v-if="!network.destinations.length" class="empty-table"><MapPinned :size="30" /><strong>尚未录入安置点</strong><span>在上方填写地址并点击地图定位。</span></div>
      </div>
    </section>
    <section v-else-if="resourceTab === 'vehicles'" class="content-card warehouse-manager">
      <div class="workflow-head">
        <div><div class="eyebrow">FLEET DIRECTORY</div><h2>车辆录入与仓库绑定</h2><p>车辆初始位置默认使用所属仓库坐标，之后可通过 GPS 接口持续更新。</p></div>
        <a class="secondary-action" href="/?page=workbench"><MapPinned :size="14" />到地图查看</a>
      </div>
      <form class="warehouse-form-grid" @submit.prevent="createVehicle">
        <label><span>车牌号 *</span><input v-model="vehicleForm.plate_no" maxlength="32" placeholder="例如：鄂A12345" /></label>
        <label><span>所属仓库 *</span><select v-model="vehicleForm.warehouse_id" @change="syncVehicleLocation"><option value="" disabled>选择仓库</option><option v-for="warehouse in network.warehouses" :key="warehouse.id" :value="warehouse.id">{{ warehouse.name }}（{{ warehouse.id }}）</option></select></label>
        <label><span>车型</span><input v-model="vehicleForm.vehicle_type" maxlength="80" /></label>
        <label><span>载重（kg）*</span><input v-model.number="vehicleForm.capacity_kg" type="number" min="1" step="1" /></label>
        <label><span>温区</span><select v-model="vehicleForm.temp_zone"><option>常温</option><option>冷藏</option><option>冷冻</option></select></label>
        <label><span>状态</span><select v-model="vehicleForm.status"><option value="available">可调度</option><option value="maintenance">维护中</option><option value="unavailable">不可用</option></select></label>
        <label><span>初始纬度</span><input v-model.number="vehicleForm.lat" type="number" min="-90" max="90" step="0.000001" /></label>
        <label><span>初始经度</span><input v-model.number="vehicleForm.lng" type="number" min="-180" max="180" step="0.000001" /></label>
        <div class="warehouse-form-actions full-span">
          <span v-if="vehicleMessage" class="warehouse-success"><CheckCircle2 :size="14" />{{ vehicleMessage }}</span>
          <span v-else class="warehouse-hint">修改所属仓库时，初始经纬度会自动同步为该仓库位置。</span>
          <button type="submit" class="primary-action" :disabled="vehicleSaving"><Plus :size="15" />{{ vehicleSaving ? '保存中...' : '创建车辆' }}</button>
        </div>
      </form>
      <div class="table-wrap warehouse-table-wrap">
        <table class="data-table">
          <thead><tr><th>车辆</th><th>类型</th><th>载重</th><th>所属仓库</th><th>状态</th><th>GPS 状态</th><th>操作</th></tr></thead>
          <tbody><tr v-for="vehicle in network.vehicles" :key="vehicle.id">
            <td><strong>{{ vehicle.plate_no }}</strong><small class="cell-sub">{{ vehicle.id }}</small></td>
            <td>{{ vehicle.vehicle_type }}</td>
            <td>{{ Number(vehicle.capacity_kg || 0).toLocaleString('zh-CN') }} kg</td>
            <td>{{ vehicle.warehouse_id || '未绑定' }}</td>
            <td><span class="warehouse-state" :class="{ 'warehouse-state--closed': vehicle.status !== 'available' }">{{ vehicleStatusLabel(vehicle.status) }}</span></td>
            <td><span class="run-status" :class="{ 'run-status--awaiting_approval': vehicle.last_position_at && !['warehouse','manual'].includes(vehicle.gps_source) }">{{ gpsState(vehicle) }}</span><small class="cell-sub">{{ vehicle.last_position_at ? formatTime(vehicle.last_position_at) : '未接入' }}</small></td>
            <td><button type="button" class="record-delete" :disabled="deletingVehicleId === vehicle.id" @click="removeVehicle(vehicle)"><Trash2 :size="12" />{{ deletingVehicleId === vehicle.id ? '删除中' : '删除' }}</button></td>
          </tr></tbody>
        </table>
        <div v-if="!network.vehicles.length" class="empty-table"><Truck :size="30" /><strong>尚未录入车辆</strong><span>在上方绑定仓库并创建车辆。</span></div>
      </div>
    </section>

    <section v-else-if="resourceTab === 'routes'" class="content-card warehouse-manager">
      <div class="workflow-head">
        <div><div class="eyebrow">ROUTE PLANNING</div><h2>真实道路自动规划</h2><p>根据已定位的业务仓库和安置点调用 OSRM，自动计算道路距离、预计时效和运输成本。</p></div>
        <button type="button" class="primary-action" :disabled="routeGenerating || !routableWarehouses.length || !routableDestinations.length" @click="generateRoutes"><Radio :size="14" />{{ routeGenerating ? '正在规划...' : '自动生成路线' }}</button>
      </div>
      <div class="route-planning-summary">
        <span>可规划仓库 <strong>{{ routableWarehouses.length }}</strong></span>
        <span>可规划安置点 <strong>{{ routableDestinations.length }}</strong></span>
        <span>将生成 <strong>{{ routableWarehouses.length * routableDestinations.length }}</strong> 条路线</span>
        <a href="/?page=workbench"><MapPinned :size="13" />到地图查看</a>
      </div>
      <div v-if="!routableWarehouses.length || !routableDestinations.length" class="inventory-entry-warning"><AlertTriangle :size="16" /><span>请先录入至少一个已定位的业务仓库和一个已定位的业务安置点；内置演示点位不会参与自动规划。</span></div>
      <div v-if="routeMessage" class="import-result route-result"><CheckCircle2 :size="16" />{{ routeMessage }}</div>
      <div v-if="routeWarnings.length" class="route-warning-list"><AlertTriangle :size="15" /><div><strong>{{ routeWarnings.length }} 条路线使用了离线估算</strong><span v-for="warning in routeWarnings.slice(0, 5)" :key="warning">{{ warning }}</span></div></div>
      <div class="table-wrap warehouse-table-wrap">
        <table class="data-table">
          <thead><tr><th>路线</th><th>仓库</th><th>安置点</th><th>距离</th><th>预计时效</th><th>成本</th><th>来源</th><th>操作</th></tr></thead>
          <tbody><tr v-for="route in visibleRoutes" :key="route.id">
            <td><strong>{{ route.road_name }}</strong><small class="cell-sub">{{ route.id }}</small></td>
            <td>{{ routeWarehouseName(route.warehouse_id) }}</td>
            <td>{{ routeDestinationName(route.destination_id) }}</td>
            <td>{{ Number(route.distance_km).toFixed(2) }} km</td>
            <td>{{ Number(route.eta_hours).toFixed(2) }} h</td>
            <td>¥{{ Number(route.cost || 0).toLocaleString('zh-CN', { maximumFractionDigits: 2 }) }}</td>
            <td><span class="soft-tag" :class="{ 'route-source--fallback': route.source === 'fallback' }">{{ routeSourceLabel(route.source) }}</span></td>
            <td><button type="button" class="record-delete" :disabled="deletingRouteId === route.id" @click="removeRoute(route)"><Trash2 :size="12" />{{ deletingRouteId === route.id ? '删除中' : '删除' }}</button></td>
          </tr></tbody>
        </table>
        <div v-if="!visibleRoutes.length" class="empty-table"><Radio :size="30" /><strong>尚无业务路线</strong><span>录入业务仓库和安置点后点击“自动生成路线”。</span></div>
      </div>
    </section>

    <section v-else class="upload-grid">
      <article v-for="card in uploadCards" :key="card.kind" class="resource-upload-card">
        <div class="resource-upload-icon"><component :is="card.icon" :size="19" /></div>
        <div><h3>{{ card.title }}</h3><p>{{ card.desc }}</p></div>
        <div class="resource-upload-actions">
          <a :href="`/api/${card.kind}/template`" download><Download :size="13" />模板</a>
          <label class="upload-label"><Upload :size="13" />{{ uploading === card.kind ? '导入中' : '上传' }}<input type="file" accept=".csv,.xlsx" @change="upload(card.kind, $event)" /></label>
        </div>
      </article>
    </section>
  </div>
</template>