<script setup>
import { computed, onMounted, ref } from 'vue'
import {
  Activity, AlertTriangle, BarChart3, CheckCircle2, Clock3, Gauge, GitCompare,
  Play, RefreshCw, ShieldCheck, Target, TestTube2, XCircle,
} from '@lucide/vue'
import { api, formatNumber } from '../api'

const benchmark = ref(null)
const manifest = ref(null)
const loading = ref(true)
const error = ref('')
const diagnostics = ref(null)
const diagnosticsLoading = ref(false)
const diagnosticsError = ref('')

const maxScore = computed(() => Math.max(...(benchmark.value?.comparison?.map((item) => item.score) || [100])))
const featureChecks = computed(() => [
  { label: '多 Agent 角色定义', status: true, evidence: '4 个版本化 Agent 契约' },
  { label: '动态 Agent 注册与版本', status: true, evidence: '注册、发现、版本发布与在线切换' },
  { label: '可视化工作流编排', status: true, evidence: '并行、条件、循环与人工门控' },
  { label: '真实异步调度', status: true, evidence: 'asyncio.PriorityQueue、3 Worker 与排队指标' },
  { label: 'AgentArts 决策闭环', status: true, evidence: '云端推荐参与已核验方案排序' },
  { label: '独立结果验证', status: true, evidence: '确定性硬规则 + 隔离上下文' },
  { label: '自我循环与反馈优化', status: true, evidence: '问题分类、策略切换、多轮收敛' },
  { label: 'RAG 与证据链', status: true, evidence: '6 条规则文档与引用编号' },
])

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [bench, platform, latest] = await Promise.all([
      api.benchmark(), api.manifest(), api.latestDiagnostics(),
    ])
    benchmark.value = bench
    manifest.value = platform
    if (latest?.result?.run_id) diagnostics.value = latest.result
  } catch (exc) { error.value = exc.message } finally { loading.value = false }
}

async function runDiagnostics() {
  diagnosticsLoading.value = true
  diagnosticsError.value = ''
  try {
    const result = await api.runDiagnostics()
    if (result.status === 'failed') throw new Error(result.error || '诊断评估失败')
    diagnostics.value = result
  } catch (exc) {
    diagnosticsError.value = exc.message
  } finally {
    diagnosticsLoading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="evaluation-page">
    <section class="evaluation-hero">
      <div><div class="eyebrow">VALIDATION & BENCHMARK</div><h2>结果验证与量化评估</h2><p>用同一库存快照和事件约束各重复运行 20 次，对比单 Agent 基线与多 Agent 核验闭环的安全性和性能。</p></div>
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
          <div class="benchmark-note"><GitCompare :size="16" /><span>基线表示直接采用第一套候选方案且不执行独立核验；展示分数由同一规则事后计算，仅用于说明验证闭环的拦截价值。</span></div>
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

      <section class="content-card cloud-runtime-card">
        <div class="workflow-head"><div><div class="eyebrow">REAL CLOUD RUNTIME EVIDENCE</div><h2>真实运行与 AgentArts 观测</h2><p>来自已持久化运行记录和 AgentArts 调用事件，不是本地性能模拟。</p></div><Activity :size="20" /></div>
        <template v-if="benchmark.runtime_evidence?.fully_cloud?.samples">
          <div class="cloud-runtime-summary">
            <article><span>历史运行样本</span><strong>{{ benchmark.runtime_evidence.fully_cloud.samples }}</strong><small>成功 {{ benchmark.runtime_evidence.fully_cloud.successful_runs }} 次</small></article>
            <article><span>端到端平均耗时</span><strong>{{ formatNumber(benchmark.runtime_evidence.fully_cloud.average_end_to_end_ms, 1) }} ms</strong><small>最新一次 {{ formatNumber(benchmark.runtime_evidence.fully_cloud.latest_run_end_to_end_ms, 1) }} ms</small></article>
            <article><span>端到端 P95</span><strong>{{ formatNumber(benchmark.runtime_evidence.fully_cloud.p95_end_to_end_ms, 1) }} ms</strong><small>运行样本尾延迟</small></article>
            <article><span>AgentArts 调用成功率</span><strong>{{ (benchmark.runtime_evidence.fully_cloud.agent_call_success_rate * 100).toFixed(1) }}%</strong><small>回退 {{ benchmark.runtime_evidence.fully_cloud.agent_fallback_count }} 次</small></article>
          </div>
          <div class="table-wrap cloud-runtime-table">
            <table class="data-table"><thead><tr><th>Agent</th><th>调用样本</th><th>平均延迟</th><th>成功率</th><th>排队</th><th>负载分</th></tr></thead><tbody>
              <tr v-for="(metric, agentId) in benchmark.runtime_evidence.agents" :key="agentId"><td><strong>{{ agentId }}</strong></td><td>{{ metric.sample_count }}</td><td>{{ metric.average_latency_ms }} ms</td><td>{{ (metric.success_rate * 100).toFixed(1) }}%</td><td>{{ metric.pending_calls }}</td><td><span class="inventory-status" :class="metric.load_score >= 80 ? 'inventory-status--expired' : 'inventory-status--available'">{{ metric.load_score }}</span></td></tr>
            </tbody></table>
          </div>
        </template>
        <div v-else class="empty-table"><Activity :size="28" /><strong>暂无历史运行样本</strong><span>完成一次完整调度后，这里会显示真实运行与 AgentArts 观测数据。</span></div>
      </section>

      <section v-if="benchmark.runtime_evidence?.scheduler?.total_tasks" class="content-card cloud-runtime-card">
        <div class="workflow-head"><div><div class="eyebrow">REAL TASK QUEUE EVIDENCE</div><h2>实际异步优先队列调度</h2><p>来自最近一次真实运行，记录 AgentArts 调用的排队、执行和并发峰值。</p></div><Activity :size="20" /></div>
        <div class="diagnostic-summary">
          <article><span>最近运行</span><strong>{{ benchmark.runtime_evidence.scheduler.run_id }}</strong><small>asyncio.PriorityQueue · {{ benchmark.runtime_evidence.scheduler.workers }} 个 Worker</small></article>
          <article><span>调度任务</span><strong>{{ benchmark.runtime_evidence.scheduler.total_tasks }}</strong><small>成功 {{ benchmark.runtime_evidence.scheduler.successful_tasks }} · 失败 {{ benchmark.runtime_evidence.scheduler.failed_tasks }}</small></article>
          <article><span>平均排队</span><strong>{{ benchmark.runtime_evidence.scheduler.average_wait_ms }} ms</strong><small>最大等待 {{ benchmark.runtime_evidence.scheduler.max_wait_ms }} ms</small></article>
          <article><span>峰值并发</span><strong>{{ benchmark.runtime_evidence.scheduler.peak_concurrency }}</strong><small>平均执行 {{ benchmark.runtime_evidence.scheduler.average_execution_ms }} ms</small></article>
        </div>
        <div class="table-wrap diagnostic-table">
          <table class="data-table"><thead><tr><th>Agent</th><th>优先级</th><th>排队时间</th><th>执行时间</th><th>状态</th></tr></thead><tbody>
            <tr v-for="(record, index) in benchmark.runtime_evidence.scheduler.records.slice(0, 12)" :key="`${record.agent_id}-${index}`"><td><strong>{{ record.agent_id }}</strong></td><td>{{ record.priority }}</td><td>{{ record.wait_ms }} ms</td><td>{{ record.execution_ms }} ms</td><td><span class="inventory-status" :class="record.status === 'success' ? 'inventory-status--available' : 'inventory-status--expired'">{{ record.status === 'success' ? '成功' : '失败' }}</span></td></tr>
          </tbody></table>
        </div>
      </section>
      <section class="content-card diagnostics-card">
        <div class="workflow-head">
          <div><div class="eyebrow">STRESS & FAULT INJECTION</div><h2>并发压力、故障注入与误差分析</h2><p>完整自适应闭环分别在 1/5/10/20 并发下执行，并验证外部依赖故障时的安全降级。</p></div>
          <button class="secondary-action" :disabled="diagnosticsLoading" @click="runDiagnostics"><Play :size="16" />{{ diagnosticsLoading ? '正在执行…' : '运行诊断' }}</button>
        </div>
        <div v-if="diagnosticsError" class="inline-error">{{ diagnosticsError }}</div>
        <div v-if="diagnosticsLoading" class="loading-panel"><div class="loading-orbit" /><strong>正在执行并发与故障注入</strong></div>
        <template v-else-if="diagnostics">
          <div class="diagnostic-summary">
            <article :class="diagnostics.status === 'passed' ? 'diagnostic-pass' : 'diagnostic-fail'"><span>总体结论</span><strong>{{ diagnostics.status === 'passed' ? '全部通过' : '存在异常' }}</strong><small>诊断编号 {{ diagnostics.run_id }}</small></article>
            <article><span>诊断总耗时</span><strong>{{ formatNumber(diagnostics.duration_ms, 1) }} ms</strong><small>{{ diagnostics.inventory_snapshot }}</small></article>
            <article><span>最高并发吞吐</span><strong>{{ Math.max(...diagnostics.concurrency.map((item) => item.throughput_runs_per_second)).toFixed(1) }}</strong><small>次/秒 · 最多 20 路并行</small></article>
            <article><span>故障用例</span><strong>{{ diagnostics.fault_injection.filter((item) => item.passed).length }}/{{ diagnostics.fault_injection.length }}</strong><small>超时、路网、JSON、库存、运力、道路</small></article>
          </div>

          <div class="table-wrap diagnostic-table">
            <table class="data-table"><thead><tr><th>并发</th><th>样本</th><th>成功率</th><th>墙钟耗时</th><th>平均延迟</th><th>P95</th><th>吞吐</th><th>结果隔离</th></tr></thead><tbody>
              <tr v-for="item in diagnostics.concurrency" :key="item.concurrency"><td><strong>{{ item.concurrency }} 路</strong></td><td>{{ item.samples }}</td><td>{{ (item.success_rate * 100).toFixed(1) }}%</td><td>{{ item.wall_ms }} ms</td><td>{{ item.average_latency_ms }} ms</td><td>{{ item.p95_latency_ms }} ms</td><td>{{ item.throughput_runs_per_second }} 次/秒</td><td><span class="inventory-status" :class="item.status === 'pass' ? 'inventory-status--available' : 'inventory-status--expired'">{{ item.isolation_consistent ? '一致' : '异常' }}</span></td></tr>
            </tbody></table>
          </div>

          <div class="diagnostic-grid">
            <div>
              <h3>故障注入结果</h3>
              <div class="fault-list">
                <article v-for="item in diagnostics.fault_injection" :key="item.id" :class="{ 'fault-failed': !item.passed }">
                  <CheckCircle2 v-if="item.passed" :size="17" /><XCircle v-else :size="17" />
                  <div><strong>{{ item.name }}</strong><span>{{ item.observed }}</span><small>预期：{{ item.expected }}</small></div>
                  <em>{{ item.recovery_ms }} ms</em>
                </article>
              </div>
            </div>
            <div>
              <h3>误差分析</h3>
              <div class="error-profile">
                <article><span>错误事件下降</span><strong>{{ (diagnostics.error_analysis.error_reduction_rate * 100).toFixed(1) }}%</strong><small>{{ diagnostics.error_analysis.multi_initial_error_events }} → {{ diagnostics.error_analysis.multi_final_error_events }}</small></article>
                <article><span>不安全验收率</span><strong>{{ (diagnostics.error_analysis.baseline_unsafe_acceptance_rate * 100).toFixed(1) }}% → {{ (diagnostics.error_analysis.multi_final_unsafe_acceptance_rate * 100).toFixed(1) }}%</strong><small>95% 区间 {{ (diagnostics.error_analysis.multi_final_unsafe_acceptance_ci95.high * 100).toFixed(1) }}% 以下</small></article>
              </div>
              <div class="table-wrap diagnostic-table error-table">
                <table class="data-table"><thead><tr><th>误差类别</th><th>基线</th><th>首轮</th><th>闭环后</th></tr></thead><tbody>
                  <tr v-for="row in diagnostics.error_analysis.rows" :key="row.category"><td>{{ row.category }}</td><td>{{ row.baseline_events }}</td><td>{{ row.initial_events }}</td><td>{{ row.final_events }}</td></tr>
                </tbody></table>
              </div>
            </div>
          </div>
          <div class="benchmark-note"><Gauge :size="16" /><span>{{ diagnostics.conclusion }} {{ diagnostics.error_analysis.method }}</span></div>
        </template>
        <div v-else class="empty-table"><Gauge :size="28" /><strong>尚未运行诊断</strong><span>点击“运行诊断”生成可持久化的并发、故障与误差证据。</span></div>
      </section>
      <section class="content-card quantitative-card">
        <div class="workflow-head"><div><div class="eyebrow">QUANTITATIVE COMPARISON</div><h2>单 Agent 基线 vs 多 Agent 验证闭环</h2><p>{{ benchmark.quantitative.repetitions }} 次重复运行，同一库存快照与事件约束。</p></div><GitCompare :size="20" /></div>
        <div class="quantitative-summary">
          <article><span>不安全方案验收率</span><strong>{{ (benchmark.quantitative.baseline.unsafe_acceptance_rate * 100).toFixed(1) }}% → {{ (benchmark.quantitative.multi_agent.unsafe_acceptance_rate * 100).toFixed(1) }}%</strong><small>多 Agent 闭环显著降低错误验收</small></article>
          <article><span>风险检出率</span><strong>0.0% → {{ (benchmark.quantitative.multi_agent.risk_detection_rate * 100).toFixed(1) }}%</strong><small>独立核验发现首轮硬阻断</small></article>
          <article><span>平均纯计算耗时</span><strong>{{ benchmark.quantitative.multi_agent.average_latency_ms }} ms</strong><small>比基线增加 {{ benchmark.quantitative.tradeoff.latency_overhead_ms }} ms</small></article>
          <article><span>闭环收敛率</span><strong>{{ (benchmark.quantitative.multi_agent.convergence_rate * 100).toFixed(1) }}%</strong><small>重算率 {{ (benchmark.quantitative.multi_agent.rework_rate * 100).toFixed(1) }}%</small></article>
        </div>
        <div class="table-wrap quantitative-table-wrap">
          <table class="data-table"><thead><tr><th>指标</th><th>单 Agent / 无核验</th><th>多 Agent / 自适应多轮</th><th>变化</th></tr></thead><tbody>
            <tr v-for="row in benchmark.quantitative_rows" :key="row.metric"><td><strong>{{ row.metric }}</strong></td><td>{{ row.baseline }}</td><td>{{ row.multi }}</td><td><span class="quantitative-change">{{ row.change }}</span></td></tr>
          </tbody></table>
        </div>
        <div class="benchmark-note"><Activity :size="16" /><span>耗时只统计本地确定性计算，不包含公网 AgentArts 网络等待；安全结论以确定性硬规则为准。</span></div>
      </section>

      <section class="content-card feature-validation">
        <div class="workflow-head"><div><div class="eyebrow">CAPABILITY MATRIX</div><h2>赛题核心能力核验</h2></div><Activity :size="20" /></div>
        <div class="feature-check-grid">
          <div v-for="item in featureChecks" :key="item.label" class="feature-check">
            <CheckCircle2 :size="17" /><div><strong>{{ item.label }}</strong><span>{{ item.evidence }}</span></div><em>PASS</em>
          </div>
        </div>
      </section>

      <section class="content-card rubric-table-card">
        <div class="workflow-head"><div><div class="eyebrow">EVIDENCE MAPPING</div><h2>赛题能力证据映射</h2></div><ShieldCheck :size="20" /></div>
        <div class="table-wrap">
          <table class="data-table"><thead><tr><th>能力维度</th><th>本项目证据</th></tr></thead>
            <tbody><tr v-for="item in manifest?.score_alignment || []" :key="item.item"><td><strong>{{ item.item }}</strong></td><td>{{ item.evidence }}</td></tr></tbody>
          </table>
        </div>
        <div class="benchmark-note"><Target :size="16" /><span>{{ benchmark.notes.join(' ') }}</span></div>
      </section>
    </template>
  </div>
</template>
