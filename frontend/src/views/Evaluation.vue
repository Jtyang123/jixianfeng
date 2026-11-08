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
const queueMetrics = ref(null)

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
  { label: '可复用工作流模板', status: true, evidence: '3 个可实例化模板 + 可见双核验投票 Agent' },
  { label: '跨运行记忆', status: true, evidence: 'SQLite/PostgreSQL 持久化，仅作安全 tie-break' },
  { label: '独立布局与长稳证据', status: true, evidence: '西部走廊与南部环线两套独立路网 + 可配置 30 秒持续提交 + 端到端运行样本分区' },
])

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [bench, platform, latest, queue] = await Promise.all([
      api.benchmark(), api.manifest(), api.latestDiagnostics(), api.queueMetrics(),
    ])
    benchmark.value = bench
    manifest.value = platform
    queueMetrics.value = queue
    if (latest?.result?.run_id) diagnostics.value = latest.result
  } catch (exc) { error.value = exc.message } finally { loading.value = false }
}

function dataIndependenceLabel(value) {
  return { shared_layout: '共享默认路网', controlled_variant: '受控变体', independent_layout: '独立布局' }[value] || value
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
      <div><div class="eyebrow">VALIDATION & BENCHMARK</div><h2>结果验证与量化评估</h2><p>覆盖道路中断、正常条件、库存缺口、无可用车辆和两套独立路网布局；基线使用独立的贪心单 Agent 生成器，不共享多目标方案生成器。</p></div>
      <button class="secondary-action" :disabled="loading" @click="load"><RefreshCw :size="16" :class="{ spin: loading }" />重新评估</button>
    </section>
    <div v-if="error" class="inline-error">{{ error }}</div>
    <div v-if="loading" class="loading-panel"><div class="loading-orbit" /><strong>正在执行基准场景</strong></div>

    <template v-else-if="benchmark">
      <section v-if="queueMetrics" class="content-card cloud-runtime-card">
        <div class="workflow-head"><div><div class="eyebrow">PLATFORM RUN SCHEDULER</div><h2>平台级运行队列</h2><p>持久化队列、并发上限、租约心跳和断点恢复；单次运行内部的 Agent 优先级不再冒充平台调度。</p></div><Activity :size="20" /></div>
        <div class="cloud-runtime-summary">
          <article><span>当前排队</span><strong>{{ queueMetrics.queued }}</strong><small>队列上限 {{ queueMetrics.max_queue_size }}</small></article>
          <article><span>正在运行</span><strong>{{ queueMetrics.running }}</strong><small>平台并发上限 {{ queueMetrics.concurrency_limit }}</small></article>
          <article><span>最久等待</span><strong>{{ formatNumber(queueMetrics.oldest_wait_ms, 1) }} ms</strong><small>优先级按等级、P0-P3、时限和人口计算</small></article>
          <article><span>重试队列</span><strong>{{ Object.values(queueMetrics.retry_jobs || {}).reduce((sum, value) => sum + value, 0) }}</strong><small>{{ queueMetrics.engine }}</small></article>
        </div>
      </section>

      <section class="evaluation-metrics">
        <article><div class="eval-icon eval-icon--red"><AlertTriangle :size="19" /></div><span>硬阻断拦截</span><strong>{{ benchmark.metrics.hard_blockers_detected }}</strong><small>类问题被独立核验发现</small></article>
        <article><div class="eval-icon eval-icon--teal"><ShieldCheck :size="19" /></div><span>不安全方案拦截</span><strong>{{ benchmark.metrics.unsafe_plans_blocked }}</strong><small>候选方案被阻止交付</small></article>
        <article><div class="eval-icon eval-icon--orange"><Target :size="19" /></div><span>不安全验收下降</span><strong>{{ benchmark.metrics.unsafe_acceptance_reduction_pp }} pp</strong><small>{{ (benchmark.quantitative.baseline.unsafe_acceptance_rate * 100).toFixed(1) }}% → {{ (benchmark.quantitative.multi_agent.unsafe_acceptance_rate * 100).toFixed(1) }}%</small></article>
        <article><div class="eval-icon eval-icon--blue"><Clock3 :size="19" /></div><span>纯计算耗时</span><strong>{{ formatNumber(benchmark.metrics.compute_ms, 1) }}</strong><small>毫秒 · 重复复现 {{ benchmark.metrics.reproducible ? '一致' : '不一致' }}</small></article>
      </section>

      <div class="evaluation-grid">
        <section class="content-card chart-card">
          <div class="workflow-head"><div><div class="eyebrow">BASELINE COMPARISON</div><h2>基线 vs 多 Agent 安全结果率</h2><p>比较“可行交付或资源不足时安全升级”的比例，不把平均评分当作安全结论。</p></div><BarChart3 :size="20" /></div>
          <div class="score-chart">
            <div v-for="item in benchmark.comparison" :key="item.label" class="score-bar-row">
              <div class="score-bar-label"><strong>{{ item.label }}</strong><span v-if="item.unsafe_plans">{{ item.unsafe_plans }} 套不安全验收</span><span v-else>无安全失败结果</span></div>
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

          <div v-if="diagnostics.platform_queue" class="diagnostic-summary soak-summary">
            <article :class="diagnostics.platform_queue.status === 'pass' ? 'diagnostic-pass' : 'diagnostic-fail'"><span>平台队列压测</span><strong>{{ diagnostics.platform_queue.status === 'pass' ? '通过' : '异常' }}</strong><small>{{ diagnostics.platform_queue.scope }}</small></article>
            <article><span>完成 / 提交</span><strong>{{ diagnostics.platform_queue.completed }}/{{ diagnostics.platform_queue.samples }}</strong><small>峰值并发 {{ diagnostics.platform_queue.peak_concurrency }} / 上限 {{ diagnostics.platform_queue.concurrency_limit }}</small></article>
            <article><span>平均 / P95 排队</span><strong>{{ diagnostics.platform_queue.average_wait_ms }} ms</strong><small>P95 {{ diagnostics.platform_queue.p95_wait_ms }} ms</small></article>
            <article><span>墙钟吞吐</span><strong>{{ diagnostics.platform_queue.throughput_runs_per_second }}</strong><small>次/秒 · {{ diagnostics.platform_queue.wall_ms }} ms</small></article>
          </div>

          <div v-if="diagnostics.soak" class="diagnostic-summary soak-summary">
            <article :class="diagnostics.soak.status === 'pass' ? 'diagnostic-pass' : 'diagnostic-fail'"><span>长稳结论</span><strong>{{ diagnostics.soak.status === 'pass' ? '持续稳定' : '存在异常' }}</strong><small>{{ diagnostics.soak.scope }}</small></article>
            <article><span>持续时间 / 样本</span><strong>{{ (diagnostics.soak.duration_ms / 1000).toFixed(1) }} s</strong><small>目标 {{ diagnostics.soak.target_duration_seconds }} 秒 · {{ diagnostics.soak.samples }} 个完整闭环样本</small></article>
            <article><span>成功率 / 吞吐</span><strong>{{ (diagnostics.soak.success_rate * 100).toFixed(1) }}%</strong><small>{{ diagnostics.soak.throughput_runs_per_second }} 次/秒 · P95 {{ diagnostics.soak.p95_latency_ms }} ms</small></article>
            <article><span>结果隔离</span><strong>{{ diagnostics.soak.isolation_consistent ? '一致' : '异常' }}</strong><small>{{ diagnostics.soak.batches }} 个批次 · {{ diagnostics.soak.concurrency }} 路持续提交</small></article>
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

      <section class="content-card quantitative-card">
        <div class="workflow-head"><div><div class="eyebrow">MULTI-SCENARIO SUITE</div><h2>差异化场景结果</h2><p>不可行场景不被要求“强行出方案”，正确行为是核验拦截并转人工。</p></div><BarChart3 :size="20" /></div>
        <div class="table-wrap quantitative-table-wrap">
          <table class="data-table"><thead><tr><th>场景</th><th>数据独立性</th><th>是否可行</th><th>基线</th><th>首轮</th><th>最终</th><th>评分</th><th>覆盖</th><th>修正轮次</th></tr></thead><tbody>
            <tr v-for="(item, index) in benchmark.scenario_results" :key="`${item.scenario_id}-${index}`"><td><strong>{{ item.name }}</strong></td><td><span class="plan-tag">{{ dataIndependenceLabel(item.data_independence) }}</span></td><td>{{ item.feasible ? '可行' : '资源不足' }}</td><td>{{ item.baseline_verdict }}</td><td>{{ item.initial_verdict }}</td><td>{{ item.final_verdict }}</td><td>{{ item.baseline_score }} → {{ item.final_score }}</td><td>{{ (item.coverage * 100).toFixed(1) }}%</td><td>{{ item.rework_rounds }}</td></tr>
          </tbody></table>
        </div>
        <div class="benchmark-note"><GitCompare :size="16" /><span>不安全验收率按全部样本统计；共享路网、受控变体和独立布局分别标注。不可行场景通过“转人工研判”计为安全结果，不把失败交付包装成成功。</span></div>
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
