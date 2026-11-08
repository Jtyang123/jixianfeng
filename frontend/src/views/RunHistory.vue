<script setup>
import { computed, onMounted, ref } from 'vue'
import { CheckCircle2, Clock3, FileText, History, RefreshCw, ShieldCheck } from '@lucide/vue'
import { api, formatTime } from '../api'

const runs = ref([])
const loading = ref(true)
const error = ref('')
const stats = computed(() => ({
  total: runs.value.length,
  approved: runs.value.filter((run) => run.approved).length,
  awaiting: runs.value.filter((run) => run.status === 'awaiting_approval').length,
  completed: runs.value.filter((run) => ['awaiting_approval', 'needs_human'].includes(run.status)).length,
}))
const statusLabel = {
  queued: '排队中', running: '推演中', awaiting_approval: '待审批', approved: '已审批', needs_human: '人工研判', failed: '执行失败',
}

async function load() {
  loading.value = true
  try { runs.value = await api.listRuns() } catch (exc) { error.value = exc.message } finally { loading.value = false }
}
function openReport(id) { window.open(`/api/runs/${id}/report`, '_blank') }
onMounted(load)
</script>

<template>
  <div class="history-page">
    <section class="history-hero">
      <div><div class="eyebrow">AUDIT TRAIL</div><h2>运行记录与审计轨迹</h2><p>每一次推演都记录场景、数据快照、智能体事件、核验版本和人工审批结果。</p></div>
      <button class="secondary-action" :disabled="loading" @click="load"><RefreshCw :size="16" :class="{ spin: loading }" />刷新记录</button>
    </section>
    <div v-if="error" class="inline-error">{{ error }}</div>

    <section class="metric-strip">
      <div class="metric-card metric-card--accent"><History :size="22" /><div><span>累计运行</span><strong>{{ stats.total }}</strong><small>次推演</small></div></div>
      <div class="metric-card"><div><span>已完成闭环</span><strong>{{ stats.completed }}</strong><small>含人工研判</small></div></div>
      <div class="metric-card"><div><span>待人工审批</span><strong>{{ stats.awaiting }}</strong><small>需负责人确认</small></div></div>
      <div class="metric-card"><div><span>已审批</span><strong>{{ stats.approved }}</strong><small>审计留痕</small></div></div>
    </section>

    <section class="content-card">
      <div class="table-wrap">
        <table class="data-table history-table">
          <thead><tr><th>运行编号</th><th>场景</th><th>状态</th><th>调度</th><th>创建时间</th><th>审批人</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-for="run in runs" :key="run.id">
              <td><span class="mono run-id">{{ run.id }}</span></td>
              <td><strong>{{ run.scenario_id === 'rainstorm-ii' ? '武汉暴雨洪涝应急演练' : '持续高温生活保障推演' }}</strong></td>
              <td><span class="run-status" :class="`run-status--${run.status}`">{{ statusLabel[run.status] || run.status }}</span></td>
              <td><span class="mono">P{{ Number(run.priority_score || 0).toFixed(1) }}</span><small class="cell-sub">第 {{ run.attempts || 0 }}/{{ run.max_attempts || 3 }} 次尝试</small></td>
              <td>{{ formatTime(run.created_at) }}</td>
              <td><span v-if="run.approved" class="approved-chip"><CheckCircle2 :size="13" />{{ run.approved_by_name || '已通过' }}</span><span v-else class="muted-text">未审批</span></td>
              <td><button class="text-action" @click="openReport(run.id)"><FileText :size="15" />查看报告</button></td>
            </tr>
          </tbody>
        </table>
        <div v-if="!runs.length && !loading" class="empty-table"><ShieldCheck :size="32" /><strong>尚无运行记录</strong><span>前往调度工作台启动第一次智能体推演。</span></div>
      </div>
    </section>
  </div>
</template>
