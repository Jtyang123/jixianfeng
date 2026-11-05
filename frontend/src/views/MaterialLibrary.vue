<script setup>
import { computed, onMounted, ref } from 'vue'
import {
  Boxes, CheckCircle2, Download, FileSpreadsheet, Plus, Search,
  Snowflake, Upload, X,
} from 'lucide-vue-next'
import { api, formatNumber } from '../api'

const emit = defineEmits(['refresh-dashboard'])
const materials = ref([])
const stockMap = ref({})
const loading = ref(true)
const search = ref('')
const category = ref('全部')
const showModal = ref(false)
const uploading = ref(false)
const importResult = ref(null)
const error = ref('')
const fileInput = ref(null)
const form = ref({
  code: '', name: '', aliases: '', category: '生活保障', specification: '',
  base_unit: '件', package_unit: '', conversion_rate: 1, storage_type: '常温',
  shelf_life_days: 365, is_cold_chain: false, is_hazardous: false,
  unit_weight_kg: 0.5, default_priority: 'P1',
})

const categories = computed(() => ['全部', ...new Set(materials.value.map((item) => item.category))])
const filtered = computed(() => materials.value.filter((item) => {
  const term = search.value.trim().toLowerCase()
  const matchesTerm = !term || [item.name, item.code, item.aliases, item.category]
    .some((value) => String(value || '').toLowerCase().includes(term))
  return matchesTerm && (category.value === '全部' || item.category === category.value)
}))
const stats = computed(() => ({
  total: materials.value.length,
  categories: new Set(materials.value.map((item) => item.category)).size,
  cold: materials.value.filter((item) => item.is_cold_chain).length,
  builtin: materials.value.filter((item) => item.source === 'builtin').length,
}))

async function load() {
  loading.value = true
  try {
    const [materialRows, inventory] = await Promise.all([api.materials(), api.inventory()])
    materials.value = materialRows
    stockMap.value = Object.fromEntries(inventory.summary.map((item) => [item.material_id, item.total_quantity]))
  } catch (exc) { error.value = exc.message } finally { loading.value = false }
}

async function uploadFile(event) {
  const file = event.target.files?.[0]
  if (!file) return
  uploading.value = true
  error.value = ''
  importResult.value = null
  try {
    importResult.value = await api.uploadMaterials(file)
    await load()
    emit('refresh-dashboard')
  } catch (exc) { error.value = exc.message } finally {
    uploading.value = false
    event.target.value = ''
  }
}

async function createMaterial() {
  error.value = ''
  if (!form.value.code || !form.value.name) {
    error.value = '请填写物资编码和名称'
    return
  }
  try {
    await api.createMaterial(form.value)
    showModal.value = false
    form.value = {
      code: '', name: '', aliases: '', category: '生活保障', specification: '',
      base_unit: '件', package_unit: '', conversion_rate: 1, storage_type: '常温',
      shelf_life_days: 365, is_cold_chain: false, is_hazardous: false,
      unit_weight_kg: 0.5, default_priority: 'P1',
    }
    await load()
    emit('refresh-dashboard')
  } catch (exc) { error.value = exc.message }
}

onMounted(load)
</script>

<template>
  <div class="library-page">
    <section class="metric-strip">
      <div class="metric-card metric-card--accent"><Boxes :size="22" /><div><span>物资主数据</span><strong>{{ stats.total }}</strong><small>条有效物资</small></div></div>
      <div class="metric-card"><div><span>物资分类</span><strong>{{ stats.categories }}</strong><small>个分类体系</small></div></div>
      <div class="metric-card"><div><span>冷链物资</span><strong>{{ stats.cold }}</strong><small>需温控运输</small></div></div>
      <div class="metric-card"><div><span>内置目录</span><strong>{{ stats.builtin }}</strong><small>可复制修改</small></div></div>
    </section>

    <section class="content-card">
      <div class="library-toolbar">
        <div>
          <div class="eyebrow">MATERIAL MASTER DATA</div>
          <h2>物资库</h2>
          <p>统一物资编码、单位、重量、储存条件和优先级，供库存上传与需求选择复用。</p>
        </div>
        <div class="toolbar-actions">
          <a class="secondary-action" href="/api/materials/template" download><Download :size="16" />下载模板</a>
          <button class="secondary-action" :disabled="uploading" @click="fileInput.click()"><Upload :size="16" />{{ uploading ? '导入中...' : '批量导入' }}</button>
          <button class="primary-action" @click="showModal = true"><Plus :size="16" />新增物资</button>
          <input ref="fileInput" type="file" accept=".csv,.xlsx" hidden @change="uploadFile" />
        </div>
      </div>

      <div v-if="importResult" class="import-result">
        <CheckCircle2 :size="18" />
        <span>成功新增 {{ importResult.imported }} 条，更新 {{ importResult.updated }} 条</span>
        <span v-if="importResult.errors.length" class="import-errors">错误 {{ importResult.errors.length }} 条</span>
      </div>
      <div v-if="error" class="inline-error">{{ error }}</div>

      <div class="filter-bar">
        <label class="search-box"><Search :size="17" /><input v-model="search" placeholder="搜索名称、编码、别名..." /></label>
        <div class="category-tabs">
          <button v-for="item in categories" :key="item" :class="{ active: category === item }" @click="category = item">{{ item }}</button>
        </div>
      </div>

      <div class="table-wrap">
        <table class="data-table material-table">
          <thead><tr><th>物资</th><th>分类</th><th>规格 / 单位</th><th>储存条件</th><th>优先级</th><th>当前库存</th><th>来源</th></tr></thead>
          <tbody>
            <tr v-for="item in filtered" :key="item.id">
              <td><div class="material-cell"><span class="material-symbol">{{ item.name.slice(0, 1) }}</span><div><strong>{{ item.name }}</strong><small>{{ item.code }}</small></div></div></td>
              <td><span class="soft-tag">{{ item.category }}</span></td>
              <td><strong>{{ item.specification || '通用规格' }}</strong><small class="cell-sub">{{ item.base_unit }}<template v-if="item.package_unit"> · {{ item.conversion_rate }}{{ item.base_unit }}/{{ item.package_unit }}</template></small></td>
              <td><span class="storage-chip" :class="{ 'storage-chip--cold': item.is_cold_chain }"><Snowflake v-if="item.is_cold_chain" :size="12" />{{ item.storage_type }}</span></td>
              <td><span class="priority-dot" :class="`priority-dot--${item.default_priority}`" />{{ item.default_priority }}</td>
              <td><strong>{{ formatNumber(stockMap[item.id]) }}</strong> <small>{{ item.base_unit }}</small></td>
              <td><span class="source-chip">{{ { builtin: '内置', import: '导入', manual: '自建' }[item.source] || item.source }}</span></td>
            </tr>
          </tbody>
        </table>
        <div v-if="!filtered.length" class="empty-table"><FileSpreadsheet :size="30" /><strong>没有匹配的物资</strong><span>调整搜索条件或新增物资。</span></div>
      </div>
    </section>

    <div v-if="showModal" class="modal-backdrop" @click.self="showModal = false">
      <form class="modal-card material-modal" @submit.prevent="createMaterial">
        <div class="modal-head"><div><div class="eyebrow">NEW MATERIAL</div><h2>新增物资</h2></div><button type="button" class="icon-button" @click="showModal = false"><X :size="18" /></button></div>
        <div class="form-grid">
          <label><span>物资编码 *</span><input v-model="form.code" placeholder="MAT-CUSTOM-001" /></label>
          <label><span>物资名称 *</span><input v-model="form.name" placeholder="例如：折叠行军床" /></label>
          <label><span>分类</span><select v-model="form.category"><option v-for="item in ['生活保障','安置保障','医疗救护','电力设备','抢险设备','卫生防疫','抢险装备','其他']" :key="item">{{ item }}</option></select></label>
          <label><span>规格</span><input v-model="form.specification" placeholder="例如：1850×700mm/张" /></label>
          <label><span>基本单位</span><input v-model="form.base_unit" /></label>
          <label><span>包装单位</span><input v-model="form.package_unit" /></label>
          <label><span>包装换算</span><input v-model.number="form.conversion_rate" type="number" min="0.01" /></label>
          <label><span>单件重量 kg</span><input v-model.number="form.unit_weight_kg" type="number" min="0" step="0.01" /></label>
          <label><span>储存条件</span><select v-model="form.storage_type"><option>常温</option><option>阴凉</option><option>冷藏</option><option>冷冻</option></select></label>
          <label><span>默认优先级</span><select v-model="form.default_priority"><option>P0</option><option>P1</option><option>P2</option></select></label>
          <label class="check-label"><input v-model="form.is_cold_chain" type="checkbox" />需要冷链运输</label>
          <label class="check-label"><input v-model="form.is_hazardous" type="checkbox" />属于危险品</label>
        </div>
        <div class="modal-foot"><button type="button" class="secondary-action" @click="showModal = false">取消</button><button class="primary-action" type="submit">保存物资</button></div>
      </form>
    </div>
  </div>
</template>
