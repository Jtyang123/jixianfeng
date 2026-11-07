<script setup>
import { computed } from 'vue'
import {
  BrainCircuit, Check, CircleDot, FileCheck2, LoaderCircle,
  RotateCcw, ScanSearch, ShieldCheck, TriangleAlert,
} from '@lucide/vue'

const props = defineProps({
  events: { type: Array, default: () => [] },
  running: { type: Boolean, default: false },
})

const agents = [
  { id: 'demand_agent', name: '需求研判', code: 'A01', icon: BrainCircuit, caption: '标准化与需求拆分' },
  { id: 'matching_agent', name: '资源匹配', code: 'A02', icon: ScanSearch, caption: '多目标方案生成' },
  { id: 'verifier_agent', name: '独立核验', code: 'A03', icon: ShieldCheck, caption: '道路 / 库存 / 时效' },
  { id: 'delivery_agent', name: '方案交付', code: 'A04', icon: FileCheck2, caption: '对比与交付包' },
]

const statusMap = computed(() => {
  const result = {}
  for (const agent of agents) result[agent.id] = 'waiting'
  for (const event of props.events) {
    if (!result[event.agent_id]) continue
    if (event.type === 'agent.started') result[event.agent_id] = 'running'
    if (event.type === 'agent.completed') result[event.agent_id] = 'done'
    if (event.type === 'verification.failed') result[event.agent_id] = 'rework'
    if (event.type === 'verification.passed') result[event.agent_id] = 'done'
    if (event.type === 'agent.failed') result[event.agent_id] = 'failed'
  }
  return result
})

const latestMap = computed(() => {
  const result = {}
  for (const event of props.events) {
    if (event.agent_id) result[event.agent_id] = event
  }
  return result
})

const recentEvents = computed(() => props.events.slice(-7))

function timeOf(value) {
  return new Date(value).toLocaleTimeString('zh-CN', { hour12: false })
}
</script>

<template>
  <aside class="agent-rail">
    <div class="agent-rail-head">
      <div>
        <div class="eyebrow">AGENT ORCHESTRATION</div>
        <h2>智能体运行轨</h2>
      </div>
      <span class="runtime-live" :class="{ 'runtime-live--on': running }">
        <span />{{ running ? 'LIVE' : 'READY' }}
      </span>
    </div>

    <div class="agent-chain">
      <article v-for="agent in agents" :key="agent.id" class="agent-step"
        :class="`agent-step--${statusMap[agent.id]}`">
        <div class="agent-node">
          <component :is="agent.icon" :size="18" />
          <span class="agent-code">{{ agent.code }}</span>
        </div>
        <div class="agent-copy">
          <div class="agent-name-line">
            <strong>{{ agent.name }}</strong>
            <span v-if="statusMap[agent.id] === 'running'" class="agent-state agent-state--running"><LoaderCircle :size="12" />运行中</span>
            <span v-else-if="statusMap[agent.id] === 'done'" class="agent-state agent-state--done"><Check :size="12" />完成</span>
            <span v-else-if="statusMap[agent.id] === 'rework'" class="agent-state agent-state--rework"><RotateCcw :size="12" />退回</span>
            <span v-else-if="statusMap[agent.id] === 'failed'" class="agent-state agent-state--failed"><TriangleAlert :size="12" />异常</span>
            <span v-else class="agent-state"><CircleDot :size="12" />等待</span>
          </div>
          <p>{{ latestMap[agent.id]?.title || agent.caption }}</p>
        </div>
      </article>
    </div>

    <div class="event-log">
      <div class="event-log-head"><span>实时事件流</span><span class="mono">{{ events.length }} EVENTS</span></div>
      <div v-if="!recentEvents.length" class="event-empty">
        <div class="event-empty-orbit" />
        <span>启动推演后，这里显示智能体交接、工具调用与核验结果。</span>
      </div>
      <div v-else class="event-list">
        <div v-for="event in recentEvents" :key="event.seq" class="event-item">
          <span class="event-time">{{ timeOf(event.created_at) }}</span>
          <span class="event-dot" :class="`event-dot--${event.type.split('.')[0]}`" />
          <div>
            <strong>{{ event.title }}</strong>
            <p>{{ event.detail }}</p>
          </div>
        </div>
      </div>
    </div>
  </aside>
</template>
