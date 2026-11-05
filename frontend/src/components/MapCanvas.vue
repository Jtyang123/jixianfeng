<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Crosshair, Layers3, MapPin, Warehouse } from 'lucide-vue-next'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'

const props = defineProps({
  network: { type: Object, default: () => ({}) },
  plan: { type: Object, default: null },
  closedRoutes: { type: Array, default: () => [] },
  running: { type: Boolean, default: false },
  eventLocation: { type: Object, default: null },
})

const mapEl = ref(null)
const tileFailed = ref(false)
const basemapMode = ref('satellite')
const provider = import.meta.env.VITE_MAP_PROVIDER || 'amap'
let map = null
let layerGroup = null
let activeTiles = null
let resizeObserver = null

const builtinWarehouseIds = new Set(['WH-A', 'WH-B', 'WH-C', 'WH-D'])
const builtinDestinationIds = new Set(['DST-01', 'DST-02', 'DST-03'])
const warehouses = computed(() => (props.network.warehouses || []).filter((item) => !builtinWarehouseIds.has(item.id)))
const destinations = computed(() => (props.network.destinations || []).filter((item) => !builtinDestinationIds.has(item.id)))
const routes = computed(() => (props.network.routes || []).filter((item) => (
  !builtinWarehouseIds.has(item.warehouse_id) && !builtinDestinationIds.has(item.destination_id)
)))
const vehicles = computed(() => (props.network.vehicles || []).filter((item) => !builtinWarehouseIds.has(item.warehouse_id)))
const eventPoint = computed(() => props.eventLocation ? pointOf(props.eventLocation) : null)
const closedSet = computed(() => new Set(props.closedRoutes))
const activeRoutes = computed(() => {
  if (!props.plan?.groups?.length) return new Set()
  return new Set(props.plan.groups.map((group) => group.route_id))
})
const warehouseMap = computed(() => Object.fromEntries(warehouses.value.map((item) => [item.id, item])))
const destinationMap = computed(() => Object.fromEntries(destinations.value.map((item) => [item.id, item])))

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
  }[char]))
}

function wgs84ToGcj02(lat, lng) {
  const a = 6378245.0
  const ee = 0.00669342162296594323
  const pi = Math.PI
  const transformLat = (x, y) => -100 + 2 * x + 3 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * Math.sqrt(Math.abs(x)) + (20 * Math.sin(6 * x * pi) + 20 * Math.sin(2 * x * pi)) * 2 / 3 + (20 * Math.sin(y * pi) + 40 * Math.sin(y / 3 * pi)) * 2 / 3 + (160 * Math.sin(y / 12 * pi) + 320 * Math.sin(y * pi / 30)) * 2 / 3
  const transformLng = (x, y) => 300 + x + 2 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * Math.sqrt(Math.abs(x)) + (20 * Math.sin(6 * x * pi) + 20 * Math.sin(2 * x * pi)) * 2 / 3 + (20 * Math.sin(x * pi) + 40 * Math.sin(x / 3 * pi)) * 2 / 3 + (150 * Math.sin(x / 12 * pi) + 300 * Math.sin(x / 30 * pi)) * 2 / 3
  const dLat0 = transformLat(lng - 105, lat - 35)
  const dLng0 = transformLng(lng - 105, lat - 35)
  const radLat = lat / 180 * pi
  let magic = Math.sin(radLat)
  magic = 1 - ee * magic * magic
  const sqrtMagic = Math.sqrt(magic)
  const dLat = (dLat0 * 180) / ((a * (1 - ee)) / (magic * sqrtMagic) * pi)
  const dLng = (dLng0 * 180) / (a / sqrtMagic * Math.cos(radLat) * pi)
  return [lat + dLat, lng + dLng]
}

function pointOf(item) {
  const lat = Number(item?.lat)
  const lng = Number(item?.lng)
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null
  return provider === 'amap' ? wgs84ToGcj02(lat, lng) : [lat, lng]
}

function makeIcon(kind, label, detail = '') {
  return L.divIcon({
    className: 'geo-marker-wrap',
    html: `<div class="geo-marker geo-marker--${kind}"><span>${escapeHtml(label.slice(0, 1))}</span><i></i></div><div class="geo-marker-label">${escapeHtml(detail || label)}</div>`,
    iconSize: [56, 34],
    iconAnchor: [16, 16],
  })
}

function draw() {
  if (!map || !layerGroup) return
  layerGroup.clearLayers()
  const points = []

  const visibleRoutes = props.plan?.groups?.length
    ? routes.value.filter((route) => activeRoutes.value.has(route.id) || closedSet.value.has(route.id))
    : routes.value.filter((route) => route.is_primary)

  for (const route of visibleRoutes) {
    const from = pointOf(warehouseMap.value[route.warehouse_id])
    const to = pointOf(destinationMap.value[route.destination_id])
    if (!from || !to) continue
    const active = activeRoutes.value.has(route.id)
    const closed = closedSet.value.has(route.id) && !active
    const line = L.polyline([from, to], {
      color: active ? '#e65b35' : closed ? '#c64332' : '#91a29b',
      weight: active ? 4 : closed ? 3 : 2,
      opacity: active ? 0.95 : closed ? 0.78 : 0.25,
      dashArray: active ? '9 7' : closed ? '5 6' : null,
    }).addTo(layerGroup)
    line.bindPopup(`<strong>${escapeHtml(route.road_name)}</strong><br>ETA ${route.eta_hours}h · ${closed ? '道路封闭' : active ? '方案采用' : '候选道路'}`)
    points.push(from, to)
  }

  for (const warehouse of warehouses.value) {
    const point = pointOf(warehouse)
    if (!point) continue
    L.marker(point, {
      icon: makeIcon('warehouse', warehouse.name, warehouse.name),
      zIndexOffset: 400,
    }).addTo(layerGroup).bindPopup(
      `<strong>${escapeHtml(warehouse.name)}</strong><br>${escapeHtml(warehouse.region)}<br>状态：${warehouse.status === 'open' ? '可调度' : '不可用'}`
    )
    points.push(point)
  }

  for (const destination of destinations.value) {
    const point = pointOf(destination)
    if (!point) continue
    if (props.running) {
      L.circle(point, { radius: 700, color: '#e65b35', weight: 1, fillColor: '#e65b35', fillOpacity: 0.08 }).addTo(layerGroup)
    }
    L.marker(point, {
      icon: makeIcon('destination', destination.name, destination.name),
      zIndexOffset: 500,
    }).addTo(layerGroup).bindPopup(
      `<strong>${escapeHtml(destination.name)}</strong><br>安置人口：${Number(destination.population || 0).toLocaleString('zh-CN')} 人<br>${escapeHtml(destination.contact)}`
    )
    points.push(point)
  }

  if (eventPoint.value) {
    L.marker(eventPoint.value, {
      icon: makeIcon('event', props.eventLocation.title || '事件位置', props.eventLocation.title || '事件位置'),
      zIndexOffset: 700,
    }).addTo(layerGroup).bindPopup(
      `<strong>${escapeHtml(props.eventLocation.title || '事件位置')}</strong><br>${escapeHtml(props.eventLocation.location || '')}<br>事件等级：${escapeHtml(props.eventLocation.severity || '')}`
    )
    points.push(eventPoint.value)
  }

  for (const vehicle of vehicles.value) {
    const point = pointOf(vehicle)
    if (!point) continue
    const gpsLive = vehicle.last_position_at && vehicle.gps_source && !['warehouse', 'manual'].includes(vehicle.gps_source)
    L.marker(point, {
      icon: makeIcon('vehicle', vehicle.plate_no || vehicle.id, vehicle.plate_no || vehicle.id),
      zIndexOffset: 300,
    }).addTo(layerGroup).bindPopup(
      `<strong>${escapeHtml(vehicle.plate_no || vehicle.id)}</strong><br>${escapeHtml(vehicle.vehicle_type)}<br>载重：${Number(vehicle.capacity_kg || 0).toLocaleString('zh-CN')} kg<br>定位：${gpsLive ? 'GPS 实时位置' : '仓库/手工位置'}`
    )
    points.push(point)
  }

  if (points.length) {
    map.fitBounds(L.latLngBounds(points).pad(0.18), { animate: false, maxZoom: 13 })
  }
}

function basemapUrl(mode) {
  const custom = import.meta.env.VITE_MAP_TILE_URL?.trim()
  if (custom) return custom
  if (provider === 'amap') {
    return mode === 'satellite'
      ? 'https://webst0{s}.is.autonavi.com/appmaptile?style=6&x={x}&y={y}&z={z}'
      : 'https://webrd0{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}'
  }
  return 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png'
}

function switchBasemap(mode) {
  basemapMode.value = mode
  if (!map) return
  tileFailed.value = false
  if (activeTiles) map.removeLayer(activeTiles)
  const isAmap = provider === 'amap'
  const tiles = L.tileLayer(basemapUrl(mode), {
    maxZoom: 19,
    subdomains: import.meta.env.VITE_MAP_TILE_SUBDOMAINS?.split(',') || (isAmap ? ['1', '2', '3', '4'] : ['a', 'b', 'c']),
    attribution: import.meta.env.VITE_MAP_ATTRIBUTION || (isAmap ? (mode === 'satellite' ? '&copy; 高德卫星影像' : '&copy; 高德地图') : '&copy; OpenStreetMap contributors'),
  })
  tiles.on('tileerror', () => { tileFailed.value = true })
  tiles.addTo(map)
  activeTiles = tiles
}

onMounted(async () => {
  await nextTick()
  const center = provider === 'amap' ? wgs84ToGcj02(30.56, 114.31) : [30.56, 114.31]
  map = L.map(mapEl.value, {
    center,
    zoom: 11,
    zoomControl: false,
    attributionControl: true,
    preferCanvas: true,
  })
  L.control.zoom({ position: 'bottomright' }).addTo(map)
  switchBasemap('satellite')
  layerGroup = L.layerGroup().addTo(map)
  draw()
  resizeObserver = new ResizeObserver(() => map?.invalidateSize())
  resizeObserver.observe(mapEl.value)
})

watch(() => [props.network, props.plan, props.closedRoutes, props.running], draw, { deep: true })
onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  map?.remove()
  map = null
})
</script>

<template>
  <section class="map-panel">
    <div class="panel-heading map-heading">
      <div>
        <div class="eyebrow">GEO OPERATIONS MAP / 真实业务点位</div>
        <h2>应急资源地理态势</h2>
      </div>
      <div class="map-legend">
        <span><i class="legend-dot legend-dot--wh" />仓库</span>
        <span><i class="legend-dot legend-dot--dst" />安置点</span>
        <span><i class="legend-line" />方案路线</span>
        <span><i class="legend-line legend-line--closed" />封闭道路</span>
      </div>
    </div>

    <div class="leaflet-shell">
      <div ref="mapEl" class="leaflet-map" role="img" aria-label="应急物资调拨真实地图" />
      <div class="map-layer-switch"><button type="button" :class="{ active: basemapMode === 'satellite' }" @click="switchBasemap('satellite')">卫星纯净</button><button type="button" :class="{ active: basemapMode === 'road' }" @click="switchBasemap('road')">道路底图</button></div>
      <div class="map-corner map-corner--tl"><Crosshair :size="14" /> GEO LIVE · OPERATIONS</div>
      <div class="map-corner map-corner--br"><MapPin :size="14" /> 纯净底图 · 业务点位</div>
      <div v-if="tileFailed" class="map-tile-warning"><Layers3 :size="14" />地图瓦片加载失败，路线与点位仍有效</div>
    </div>

    <div class="map-footer">
      <div class="map-stat"><Warehouse :size="15" /><span>可用仓库</span><strong>{{ warehouses.length }}</strong></div>
      <div class="map-stat"><MapPin :size="15" /><span>安置点</span><strong>{{ destinations.length }}</strong></div>
      <div class="map-stat"><span class="route-swatch" /><span>封闭主通道</span><strong>{{ closedSet.size }}</strong></div>
      <div class="map-stat"><span class="vehicle-swatch" /><span>可用车辆</span><strong>{{ vehicles.length }}</strong></div>
      <div class="map-footer-note">{{ provider === 'amap' ? (basemapMode === 'satellite' ? '高德卫星纯净底图' : '高德道路底图') : 'OpenStreetMap 底图' }} · 默认隐藏内置演示点位</div>
    </div>
  </section>
</template>
