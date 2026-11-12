<script setup>
import { onMounted, ref } from 'vue'
import { BookOpen, CheckCircle2, RefreshCw, Search, Trash2, Upload } from '@lucide/vue'
import { api, formatTime } from '../api'

const props = defineProps({ user: { type: Object, default: null } })
const documents = ref([])
const chunks = ref([])
const selected = ref(null)
const query = ref('')
const results = ref([])
const loading = ref(true)
const uploading = ref(false)
const searching = ref(false)
const error = ref('')
const message = ref('')
const embeddingStatus = ref(null)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [documentRows, platform] = await Promise.all([api.knowledgeDocuments(), api.manifest()])
    documents.value = documentRows
    embeddingStatus.value = platform?.knowledge?.embedding || null
  } catch (exc) { error.value = exc.message } finally { loading.value = false }
}

async function upload(fileEvent) {
  const file = fileEvent.target.files?.[0]
  if (!file) return
  uploading.value = true
  error.value = ''
  message.value = ''
  try {
    const document = await api.uploadKnowledge(file, '业务规则')
    message.value = `已上传“${document.name}”，生成 ${document.chunk_count} 个片段`
    await load()
  } catch (exc) { error.value = exc.message } finally {
    uploading.value = false
    fileEvent.target.value = ''
  }
}

async function openDocument(document) {
  selected.value = document
  try { chunks.value = await api.knowledgeChunks(document.id) } catch (exc) { error.value = exc.message }
}

async function search() {
  if (!query.value.trim()) return
  searching.value = true
  error.value = ''
  try { results.value = await api.searchKnowledge(query.value.trim(), 6) } catch (exc) { error.value = exc.message } finally { searching.value = false }
}

async function removeDocument(document) {
  if (!window.confirm(`确认删除知识文档“${document.name}”？`)) return
  try {
    await api.deleteKnowledge(document.id)
    if (selected.value?.id === document.id) { selected.value = null; chunks.value = [] }
    message.value = '知识文档已删除'
    await load()
  } catch (exc) { error.value = exc.message }
}

function embeddingModeLabel(mode) {
  return { local: 'LOCAL', remote: 'REMOTE', 'hash-fallback': 'HASH FALLBACK' }[mode] || mode
}

onMounted(load)
</script>

<template>
  <div class="knowledge-page">
    <section class="content-card knowledge-manager-head">
      <div><div class="eyebrow">RAG KNOWLEDGE BASE</div><h2>知识库管理</h2><p>上传运输规则、冷链要求、物资说明等文档，系统自动分块并接入调度 RAG 检索。</p></div>
      <div class="knowledge-upload-actions">
        <label class="upload-label"><Upload :size="15" />{{ uploading ? '上传中...' : '上传知识文档' }}<input type="file" accept=".txt,.md,.csv,.json,.docx,.xlsx,.pdf" :disabled="!user || user.role !== 'admin'" @change="upload" /></label>
        <button class="secondary-action" @click="load"><RefreshCw :size="14" />刷新</button>
      </div>
    </section>
    <div v-if="error" class="inline-error">{{ error }}</div>
    <div v-if="embeddingStatus" class="embedding-status-note">
      <BookOpen :size="15" />
      <span class="embedding-mode-badge" :class="`embedding-mode-badge--${embeddingStatus.mode}`">{{ embeddingModeLabel(embeddingStatus.mode) }}</span>
      <strong>{{ embeddingStatus.model }} · {{ embeddingStatus.dimensions }} 维</strong>
      <span>stale {{ embeddingStatus.stale_chunks }}/{{ embeddingStatus.total_chunks }}</span>
      <em v-if="embeddingStatus.mode === 'hash-fallback'">hash-fallback = 领域同义词 + BM25 + 哈希词袋向量，非语义模型；配置 EMBEDDING_PROVIDER=remote 后自动启用语义检索</em>
      <em v-else>当前检索后端已启用{{ embeddingStatus.mode === 'remote' ? '远程语义 embedding' : '本地语义 embedding' }}</em>
    </div>
    <div v-if="message" class="import-result"><CheckCircle2 :size="16" />{{ message }}</div>
    <div class="knowledge-manager-grid">
      <section class="content-card knowledge-documents-card">
        <div class="workflow-head"><div><div class="eyebrow">DOCUMENTS</div><h2>已入库文档</h2></div><BookOpen :size="18" /></div>
        <div v-if="loading" class="loading-panel"><div class="loading-orbit" /><strong>正在加载文档</strong></div>
        <div v-else class="knowledge-document-list">
          <button v-for="document in documents" :key="document.id" :class="{ active: selected?.id === document.id }" @click="openDocument(document)">
            <span><strong>{{ document.name }}</strong><small>{{ document.category }} · {{ document.chunk_count }} 个片段 · {{ formatTime(document.created_at) }}</small></span>
            <Trash2 v-if="user?.role === 'admin'" :size="14" @click.stop="removeDocument(document)" />
          </button>
          <div v-if="!documents.length" class="empty-table"><BookOpen :size="28" /><strong>尚无知识文档</strong><span>上传文本或规则文件后即可参与 RAG 检索。</span></div>
        </div>
      </section>
      <section class="content-card knowledge-search-card">
        <div class="workflow-head"><div><div class="eyebrow">RETRIEVAL TEST</div><h2>检索测试</h2></div><Search :size="18" /></div>
        <form class="knowledge-search-form" @submit.prevent="search"><input v-model="query" placeholder="例如：暴雨备用通道 冷链运输" /><button class="primary-action" :disabled="searching">{{ searching ? '检索中...' : '检索' }}</button></form>
        <div class="knowledge-search-results"><article v-for="item in results" :key="item.id"><strong>{{ item.title }}</strong><span>{{ item.source }} · {{ item.category }} · 相关度 {{ item.score }}</span><p>{{ item.excerpt }}</p></article><div v-if="query && !results.length && !searching" class="empty-table"><Search :size="26" /><strong>没有匹配片段</strong></div></div>
      </section>
    </div>
    <section v-if="selected" class="content-card knowledge-chunks-card">
      <div class="workflow-head"><div><div class="eyebrow">CHUNKS</div><h2>{{ selected.name }} · 分块预览</h2></div><span class="workflow-chip">{{ chunks.length }} CHUNKS</span></div>
      <div class="knowledge-chunk-list"><article v-for="chunk in chunks" :key="chunk.id"><span>#{{ chunk.seq }}</span><div><strong>{{ chunk.title }}</strong><p>{{ chunk.content }}</p></div></article></div>
    </section>
  </div>
</template>
