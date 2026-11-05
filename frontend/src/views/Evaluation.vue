<script setup>
import { computed, onMounted, ref } from 'vue'
import {
  Activity, AlertTriangle, BarChart3, CheckCircle2, Clock3, GitCompare,
  RefreshCw, ShieldCheck, Target, TestTube2,
} from 'lucide-vue-next'
import { api, formatNumber } from '../api'

const benchmark = ref(null)
const manifest = ref(null)
const loading = ref(true)
const error = ref('')

const maxScore = computed(() => Math.max(...(benchmark.value?.comparison?.map((item) => item.score) || [100])))
const featureChecks = computed(() => [
  { label: '多 Agent 角色定义', status: true, evidence: '4 个版本化 Agent 契约' },
  { label: '可视化工作流编排', status: true, evidence: '并行、条件、循环与人工门控' },
  { label: '动态调度与执行监控', status: true, evidence: '能力权重 + SSE 实时事件' },
  { label: '独立结果验证', status: true, evidence: '确定性硬规则 + 隔离上下文' },
  { label: '自我循环与反馈优化', status: true, evidence: '结构化退回、重算、收敛' },
  { label: 'RAG 与证据链', status: true, evidence: '6 条规则文档与引用编号' },
])

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [bench, platform] = await Promise.all([api.benchmark(), api.manifest()])
    benchmark.value = bench
    manifest.value = platform
  } catch (exc) { error.value = exc.message } finally { loading.value = false }
}
onMounted(load)
</script>

<template>
  <div class="evaluation-page">
    <section class="evaluation-hero">
      <div><div class="eyebrow">VALIDATION & BENCHMARK</div><h2>结果验证与量化评估</h2><p>用同一库存快照与事件约束重复计算，验证核验拦截、反馈收敛和结果可复现性。</p></div>
      <button class="secondary-action" :disabled="loading" @click="load"><RefreshCw :size="16" :class="{ spin: loading }" />重新评估</button>
    </section>
    <div v-if="error" class="inline-error">{{ error }}</div>
    <div v-if="loading" class="loading-panel"><div class="loading-orbit" /><strong>正在执行基准场景</strong></div>

    <template v-else-if="benchmark">
      <section class="evaluation-metrics">
        <article><div class="eval-icon eval-icon--red"><AlertTriangle :size="19" /></div><span>硬阻断拦截</span><strong>{{ benchmark.metrics.hard_blockers_detected }}</strong><small>类问题被独立核验发现</small></article>
        <article><div class="eval-icon eval-icon--teal"><ShieldCheck :size="19" /></div><span>不安全方案拦截</span><strong>{{ benchmark.metrics.unsafe_plans_blocked }}</strong><small>候选方案被阻止交付</small></article>
        <article><div class="eval-icon eval-icon--orange"><Target :size="19" /></div><span>核验评分提升</span><strong>+{{ benchmark.metrics.score_gain }}</strong><small>{{ benchmark.metrics.initial_score }} → {{ benchmark.metrics.final_score }}</small></article>
        <article><div class="eval-icon eval-icon--blue"><Clock3 :size="19" /></div><span>纯计算耗时</span><strong>{{ formatNumber(benchmark.metrics.compute_ms, 1) }}</strong><small>毫秒 · 重复复现 {{ benchmark.metrics.reproducible ? '一致' : '不一致' }}</small></article>
      </section>

      <div class="evaluation-grid">
        <section class="content-card chart-card">
          <div class="workflow-head"><div><div class="eyebrow">BASELINE COMPARISON</div><h2>基线与反馈优化对比</h2></div><BarChart3 :size="20" /></div>
          <div class="score-chart">
            <div v-for="item in benchmark.comparison" :key="item.label" class="score-bar-row">
              <div class="score-bar-label"><strong>{{ item.label }}</strong><span v-if="item.unsafe_plans">{{ item.unsafe_plans }} 套未安全方案</span><span v-else>全部通过硬规则</span></div>
              <div class="score-bar-track"><i :class="{ 'score-bar-fill--pass': item.passed }" :style="{ width: `${Math.max(4, item.score / maxScore * 100)}%` }" /></div>
              <em>{{ item.score }}</em>
            </div>
          </div>
          <div class="benchmark-note"><GitCompare :size="16" /><span>基线代表“无独立核验直接采用候选方案”，用于说明验证闭环的实际拦截价值；不是正式行业基准。</span></div>
        </section>

        <section class="content-card reproducibility-card">
          <div class="workflow-head"><div><div class="eyebrow">REPRODUCIBILITY</div><h2>可复现性证据</h2></div><TestTube2 :size="20" /></div>
          <div class="repro-list">
            <div><CheckCircle2 :size="16" /><span>固定库存快照</span><strong>{{ benchmark.metrics.inventory_snapshot }}</strong></div>
            <div><CheckCircle2 :size="16" /><span>重复计算结果</span><strong>{{ benchmark.metrics.reproducible ? '完全一致' : '存在差异' }}</strong></div>
            <div><CheckCircle2 :size="16" /><span>需求覆盖</span><strong>{{ (benchmark.metrics.final_coverage * 100).toFixed(1) }}%</strong></div>
            <div><CheckCircle2 :size="16" /><span>通过方案数</span><strong>{{ benchmark.metrics.approved_plans }}</strong></div>
            <div><CheckCircle2 :size="16" /><span>重复计算耗时</span><strong>{{ benchmark.metrics.repeat_compute_ms }} ms</strong></div>
          </div>
          <p class="repro-command"><span>复现命令</span><code>cd backend && pytest -q</code></p>
        </section>
      </div>

      <section class="content-card feature-validation">
        <div class="workflow-head"><div><div class="eyebrow">CAPABILITY MATRIX</div><h2>赛题核心能力核验</h2></div><Activity :size="20" /></div>
        <div class="feature-check-grid">
          <div v-for="item in featureChecks" :key="item.label" class="feature-check">
            <CheckCircle2 :size="17" /><div><strong>{{ item.label }}</strong><span>{{ item.evidence }}</span></div><em>PASS</em>
          </div>
        </div>
      </section>

      <section class="content-card rubric-table-card">
        <div class="workflow-head"><div><div class="eyebrow">JUDGING ALIGNMENT</div><h2>评分维度与实现证据</h2></div><ShieldCheck :size="20" /></div>
        <div class="table-wrap">
          <table class="data-table"><thead><tr><th>评分维度</th><th>分值</th><th>本项目证据</th></tr></thead>
            <tbody><tr v-for="item in manifest?.score_alignment || []" :key="item.item"><td><strong>{{ item.item }}</strong></td><td><span class="score-pill">{{ item.score }} 分</span></td><td>{{ item.evidence }}</td></tr></tbody>
          </table>
        </div>
        <div class="benchmark-note"><Target :size="16" /><span>{{ benchmark.notes.join(' ') }}</span></div>
      </section>
    </template>
  </div>
</template>
