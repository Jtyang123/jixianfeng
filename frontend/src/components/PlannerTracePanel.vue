<script setup>
import { computed } from 'vue'

const props = defineProps({
  plannerTrace: { type: Object, default: null },
  plannerDecision: { type: Object, default: () => ({}) },
  llmCallCount: { type: Number, default: 0 },
})

const initialSteps = computed(() => props.plannerTrace?.initial_plan?.steps || [])
const decisions = computed(() => props.plannerTrace?.decisions || [])
const activeStep = computed(() => decisions.value[decisions.value.length - 1]?.step || null)
const executedSteps = computed(() => Number(props.plannerTrace?.executed_steps || 0))
const replans = computed(() => Number(props.plannerDecision?.replans || 0))
const totalLlmCalls = computed(() => Number(props.llmCallCount || (decisions.value.length + 1)))

function isActiveStep(step) {
  const active = activeStep.value
  if (!active) return false
  return step.id === active.id && step.tool === active.tool
}

function decisionLabel(value) {
  return { continue: '继续', stop: '停止', replan: '重规划', human: '转人工' }[value] || value || '未知'
}
</script>

<template>
  <details v-if="plannerTrace?.initial_plan" class="planner-trace-panel">
    <summary>
      <strong>Planner 推理轨迹</strong>
      <span>{{ initialSteps.length }} 步初始计划 · {{ decisions.length }} 条决策</span>
    </summary>

    <div class="planner-trace-summary">
      <article><span>实际执行</span><strong>{{ executedSteps }}</strong><small>executed_steps</small></article>
      <article><span>重规划</span><strong>{{ replans }}</strong><small>replans</small></article>
      <article><span>LLM 调用</span><strong>{{ totalLlmCalls }}</strong><small>含 1 次初始规划</small></article>
    </div>

    <div class="planner-initial-plan">
      <h4>初始计划</h4>
      <div class="planner-initial-steps">
        <article v-for="step in initialSteps" :key="step.id" :class="{ 'planner-step--active': isActiveStep(step) }">
          <div><strong>{{ step.id }}</strong><span>{{ step.tool }}</span></div>
          <p>{{ step.reason }}</p>
        </article>
      </div>
    </div>

    <div class="planner-decision-list">
      <h4>实际执行轨迹</h4>
      <article
        v-for="(item, index) in decisions"
        :key="`${item.step?.id || index}-${index}`"
        data-testid="planner-decision"
        class="planner-decision-row"
        :class="{
          'planner-decision-row--replan': item.decision?.decision === 'replan',
          'planner-decision-row--deterministic': item.deterministic,
        }"
      >
        <div class="planner-decision-head">
          <strong>{{ index + 1 }}. {{ item.step?.tool }}</strong>
          <span class="planner-decision-badge">{{ decisionLabel(item.decision?.decision) }}</span>
          <code>{{ item.decision?.decision }}</code>
          <em v-if="item.decision?.decision === 'replan'">重规划</em>
          <em v-else-if="item.deterministic" class="planner-decision-deterministic">计划内推进</em>
        </div>
        <p>{{ item.decision?.reason }}</p>
        <small v-if="item.decision?.repair_policy">repair_policy：{{ item.decision.repair_policy }}</small>
      </article>
    </div>
  </details>
</template>