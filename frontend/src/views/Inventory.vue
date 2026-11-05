<script setup>
import { computed, onMounted, ref } from 'vue'
import {
  AlertTriangle, CheckCircle2, Clock3, Database, Download, FileSpreadsheet,
  Layers3, Pencil, Plus, Trash2, Upload, Warehouse, X,
} from 'lucide-vue-next'
import { api, formatNumber, formatTime } from '../api'

const emit = defineEmits(['refresh-dashboard'])
const data = ref({ snapshot: null, summary: [], items: [], warehouses: [] })
const materials = ref([])
const loading = ref(true)
const uploading = ref(false)
const saving = ref(false)
const deletingId = ref(null)
const error = ref('')
const message = ref('')
const importResult = ref(null)
const tab = ref('summary')
const fileInput = ref(null)
const editingEntryId = ref(null)
const stockForm = ref({
  warehouse_id: '', material_id: '', quantity: 1, batch_no: '', expiry_date: '', status: 'available',
})

const warehouseStats = computed(() => data.value.warehouses.map((warehouse) => {
  const items = data.value.items.filter((item) => item.warehouse_id === warehouse.id)
  return {
    ...warehouse,
    skuCount: new Set(items.map((item) => item.material_id)).size,
    totalUnits: items.reduce((sum, item) => sum + Number(item.quantity || 0), 0),
  }
}))
const totalRecords = computed(() => data.value.items.length)
const coldItems = computed(() => data.value.summary.filter((item) => item.category?.includes('医疗')).length)
const selectedMaterial = computed(() => materials.value.find((item) => item.id === Number(stockForm.value.material_id)))
const canSaveStock = computed(() => stockForm.value.warehouse_id && stockForm.value.material_id && Number(stockForm.value.quantity) > 0)

function statusLabel(status) {
  return ({ available: '可用', locked: '锁定', expired: '过期' })[status] || status
}

function resetStockForm() {
  editingEntryId.value = null
  stockForm.value = {
    warehouse_id: data.value.warehouses[0]?.id || '',
    material_id: materials.value[0]?.id || '',
    quantity: 1,
    batch_no: '',
    expiry_date: '',
    status: 'available',
  }
}

async function load() {
  loading.value = true
  try {
    const [inventoryData, materialRows] = await Promise.all([api.inventory(), api.materials()])
    data.value = inventoryData
    materials.value = materialRows
    if (!stockForm.value.warehouse_id) stockForm.value.warehouse_id = inventoryData.warehouses[0]?.id || ''
    if (!stockForm.value.material_id) stockForm.value.material_id = materialRows[0]?.id || ''
  } catch (exc) { error.value = exc.message } finally { loading.value = false }
}

function startEdit(item) {
  editingEntryId.value = item.id
  stockForm.value = {
    warehouse_id: item.warehouse_id,
    material_id: item.material_id,
    quantity: Number(item.quantity),
    batch_no: item.batch_no || '',
    expiry_date: item.expiry_date || '',
    status: item.status || 'available',
  }
  tab.value = 'details'
}

async function saveStock() {
  if (!canSaveStock.value) {
    error.value = '请选择仓库、物资并填写大于 0 的数量'
    return
  }
  saving.value = true
  error.value = ''
  message.value = ''
  try {
    const payload = {
      warehouse_id: stockForm.value.warehouse_id,
      material_id: Number(stockForm.value.material_id),
      quantity: Number(stockForm.value.quantity),
      batch_no: stockForm.value.batch_no.trim(),
      expiry_date: stockForm.value.expiry_date || null,
      status: stockForm.value.status,
    }
    if (editingEntryId.value) {
      delete payload.warehouse_id
      delete payload.material_id
      const result = await api.updateInventoryEntry(editingEntryId.value, payload)
      message.value = `库存已调整，生成快照“${result.snapshot.label}”`
    } else {
      const result = await api.createInventoryEntry(payload)
      message.value = `库存已入库，生成快照“${result.snapshot.label}”`
    }
    await load()
    resetStockForm()
    emit('refresh-dashboard')
  } catch (exc) {
    error.value = exc.message
  } finally {
    saving.value = false
  }
}

async function removeStock(item) {
  const confirmed = window.confirm(`确定删除“${item.warehouse_name} / ${item.material_name}”的这条库存吗？\n\n系统会生成一个新快照，历史快照不会丢失。`)
  if (!confirmed) return
  deletingId.value = item.id
  error.value = ''
  message.value = ''
  try {
    const result = await api.deleteInventoryEntry(item.id)
    message.value = `库存记录已删除，生成快照“${result.snapshot.label}”`
    await load()
    if (editingEntryId.value === item.id) resetStockForm()
    emit('refresh-dashboard')
  } catch (exc) {
    error.value = exc.message
  } finally {
    deletingId.value = null
  }
}

async function uploadFile(event) {
  const file = event.target.files?.[0]
  if (!file) return
  uploading.value = true
  error.value = ''
  importResult.value = null
  try {
    importResult.value = await api.uploadInventory(file)
    await load()
    emit('refresh-dashboard')
  } catch (exc) { error.value = exc.message } finally {
    uploading.value = false
    event.target.value = ''
  }
}

onMounted(load)
</script>

<template>
  <div class="inventory-page">
    <section class="inventory-hero">
      <div class="inventory-hero-copy">
        <div class="eyebrow">RESOURCE SNAPSHOT</div>
        <h2>库存数据与快照</h2>
        <p>手工入库、库存调整和 Excel 导入都会生成独立快照，推演始终引用固定版本，保证方案可复现和可审计。</p>
        <div class="snapshot-badges" v-if="data.snapshot">
          <span><Database :size="14" />{{ data.snapshot.label }}</span>
          <span><Clock3 :size="14" />{{ formatTime(data.snapshot.uploaded_at) }}</span>
          <span><Layers3 :size="14" />{{ data.snapshot.row_count }} 条记录</span>
        </div>
      </div>
      <div class="upload-zone">
        <div class="upload-icon"><Upload :size="23" /></div>
        <div><strong>{{ uploading ? '正在校验并写入快照...' : '批量上传库存 Excel / CSV' }}</strong><span>适合首次导入或批量盘点，上传后生成新快照</span></div>
        <button class="primary-action" :disabled="uploading" @click="fileInput.click()">选择文件</button>
        <input ref="fileInput" type="file" accept=".csv,.xlsx" hidden @change="uploadFile" />
        <a href="/api/inventory/template" download><Download :size="14" />下载库存模板</a>
      </div>
    </section>

    <div v-if="importResult" class="import-result">
      <CheckCircle2 :size="18" /><span>已生成库存快照 {{ importResult.snapshot.label }}，导入 {{ importResult.imported }} 条记录</span>
      <span v-if="importResult.warnings.length" class="import-warning"><AlertTriangle :size="14" />{{ importResult.warnings.length }} 条提示</span>
    </div>
    <div v-if="message" class="import-result"><CheckCircle2 :size="17" />{{ message }}</div>
    <div v-if="error" class="inline-error"><AlertTriangle :size="16" />{{ error }}</div>

    <section class="content-card inventory-entry-card">
      <div class="workflow-head">
        <div><div class="eyebrow">WAREHOUSE INVENTORY ENTRY</div><h2>{{ editingEntryId ? '调整库存记录' : '手工入库' }}</h2><p>选择仓库和物资，录入数量、批次与有效期。保存后会自动生成新的库存快照。</p></div>
        <button v-if="editingEntryId" type="button" class="secondary-action" @click="resetStockForm"><X :size="14" />取消修改</button>
      </div>
      <div v-if="!data.warehouses.length || !materials.length" class="inventory-entry-warning">
        <AlertTriangle :size="16" />
        <span>{{ !data.warehouses.length ? '请先在“仓库与接入”中录入仓库。' : '物资库为空，请先导入物资主数据。' }}</span>
      </div>
      <form class="inventory-entry-grid" @submit.prevent="saveStock">
        <label><span>仓库 *</span><select v-model="stockForm.warehouse_id" :disabled="Boolean(editingEntryId)"><option value="" disabled>选择仓库</option><option v-for="warehouse in data.warehouses" :key="warehouse.id" :value="warehouse.id">{{ warehouse.name }}（{{ warehouse.id }}）</option></select></label>
        <label><span>物资 *</span><select v-model.number="stockForm.material_id" :disabled="Boolean(editingEntryId)"><option value="" disabled>选择物资</option><option v-for="material in materials" :key="material.id" :value="material.id">{{ material.name }}（{{ material.code }}）</option></select></label>
        <label><span>数量 *</span><div class="quantity-input"><input v-model.number="stockForm.quantity" type="number" min="0.001" step="0.001" /><small>{{ selectedMaterial?.base_unit || '单位' }}</small></div></label>
        <label><span>批次号</span><input v-model="stockForm.batch_no" maxlength="80" placeholder="例如 BATCH-20261104" /></label>
        <label><span>有效期</span><input v-model="stockForm.expiry_date" type="date" /></label>
        <label><span>库存状态</span><select v-model="stockForm.status"><option value="available">可用</option><option value="locked">锁定</option><option value="expired">过期</option></select></label>
        <div class="inventory-entry-actions full-span">
          <span>当前物资单位：<strong>{{ selectedMaterial?.base_unit || '--' }}</strong></span>
          <button class="primary-action" type="submit" :disabled="saving || !canSaveStock"><Plus :size="15" />{{ saving ? '保存中...' : editingEntryId ? '保存调整' : '确认入库' }}</button>
        </div>
      </form>
    </section>

    <section class="metric-strip inventory-metrics">
      <div class="metric-card metric-card--accent"><Database :size="22" /><div><span>最新快照</span><strong>{{ data.snapshot?.row_count || 0 }}</strong><small>条库存记录</small></div></div>
      <div class="metric-card"><div><span>仓库节点</span><strong>{{ warehouseStats.length }}</strong><small>已纳入路网</small></div></div>
      <div class="metric-card"><div><span>物资品类</span><strong>{{ data.summary.length }}</strong><small>当前汇总</small></div></div>
      <div class="metric-card"><div><span>医疗相关</span><strong>{{ coldItems }}</strong><small>重点保障</small></div></div>
    </section>

    <section class="content-card">
      <div class="inventory-toolbar">
        <div><div class="eyebrow">WAREHOUSE INVENTORY</div><h2>库存分布</h2></div>
        <div class="segmented-control"><button :class="{ active: tab === 'summary' }" @click="tab = 'summary'">物资汇总</button><button :class="{ active: tab === 'warehouse' }" @click="tab = 'warehouse'">仓库视图</button><button :class="{ active: tab === 'details' }" @click="tab = 'details'">库存明细</button></div>
      </div>

      <div v-if="tab === 'summary'" class="table-wrap">
        <table class="data-table">
          <thead><tr><th>物资</th><th>分类</th><th>总可用量</th><th>覆盖仓库</th><th>最近更新</th></tr></thead>
          <tbody><tr v-for="item in data.summary" :key="item.material_id">
            <td><div class="material-cell"><span class="material-symbol">{{ item.name.slice(0, 1) }}</span><div><strong>{{ item.name }}</strong><small>{{ item.code }}</small></div></div></td>
            <td><span class="soft-tag">{{ item.category }}</span></td>
            <td><strong class="stock-number">{{ formatNumber(item.total_quantity) }}</strong> <small>{{ item.base_unit }}</small></td>
            <td>{{ item.warehouse_count }} 个仓库</td>
            <td>{{ formatTime(item.updated_at) }}</td>
          </tr></tbody>
        </table>
        <div v-if="!data.summary.length" class="empty-table"><FileSpreadsheet :size="30" /><strong>暂无库存</strong><span>使用上方手工入库或库存模板建立第一份快照。</span></div>
      </div>

      <div v-else-if="tab === 'warehouse'" class="warehouse-grid">
        <article v-for="warehouse in warehouseStats" :key="warehouse.id" class="warehouse-card">
          <div class="warehouse-card-head"><div class="warehouse-icon"><Warehouse :size="20" /></div><span class="soft-tag">{{ warehouse.region }}</span></div>
          <h3>{{ warehouse.name }}</h3>
          <div class="warehouse-meta"><span>{{ warehouse.skuCount }} 类物资</span><span>{{ formatNumber(warehouse.totalUnits) }} 基本单位</span></div>
          <div class="warehouse-bar"><i :style="{ width: `${Math.min(100, warehouse.totalUnits / 300)}%` }" /></div>
          <small>状态：{{ warehouse.status === 'open' ? '可调度' : '不可用' }}</small>
        </article>
      </div>

      <div v-else class="table-wrap">
        <table class="data-table">
          <thead><tr><th>仓库</th><th>物资</th><th>数量</th><th>批次</th><th>有效期</th><th>状态</th><th>操作</th></tr></thead>
          <tbody><tr v-for="item in data.items" :key="item.id" :class="{ 'inventory-row--editing': editingEntryId === item.id }">
            <td><strong>{{ item.warehouse_name }}</strong><small class="cell-sub">{{ item.warehouse_id }}</small></td>
            <td><strong>{{ item.material_name }}</strong><small class="cell-sub">{{ item.material_code }}</small></td>
            <td><strong class="stock-number">{{ formatNumber(item.quantity, 2) }}</strong> <small>{{ item.unit }}</small></td>
            <td>{{ item.batch_no || '--' }}</td>
            <td>{{ item.expiry_date || '未填写' }}</td>
            <td><span class="inventory-status" :class="`inventory-status--${item.status}`">{{ statusLabel(item.status) }}</span></td>
            <td><div class="inventory-row-actions"><button type="button" class="record-edit" @click="startEdit(item)"><Pencil :size="12" />调整</button><button type="button" class="record-delete" :disabled="deletingId === item.id" @click="removeStock(item)"><Trash2 :size="12" />{{ deletingId === item.id ? '删除中' : '删除' }}</button></div></td>
          </tr></tbody>
        </table>
        <div v-if="!data.items.length" class="empty-table"><FileSpreadsheet :size="30" /><strong>暂无库存明细</strong><span>在上方选择仓库和物资后确认入库。</span></div>
      </div>
    </section>
  </div>
</template>
