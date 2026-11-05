<script setup>
import { ref } from 'vue'
import { BarChart3, GitBranch, Network, ShieldCheck } from 'lucide-vue-next'
import AgentStudio from './AgentStudio.vue'
import Evaluation from './Evaluation.vue'
import AccessControl from './AccessControl.vue'
import AgentRegistryPanel from './AgentRegistryPanel.vue'

defineProps({ user: { type: Object, default: null } })

const requestedTab = new URLSearchParams(window.location.search).get('tab')
const tab = ref(['agents', 'evaluation', 'registry', 'access'].includes(requestedTab) ? requestedTab : 'agents')
</script>

<template>
  <div class="center-page">
    <div class="center-switch">
      <button :class="{ active: tab === 'agents' }" @click="tab = 'agents'; const u = new URL(window.location.href); u.searchParams.set('tab', 'agents'); history.replaceState({}, '', u)"><Network :size="17" /><span><strong>Agent 编排</strong><small>角色、工具与工作流</small></span></button>
      <button :class="{ active: tab === 'evaluation' }" @click="tab = 'evaluation'; const u = new URL(window.location.href); u.searchParams.set('tab', 'evaluation'); history.replaceState({}, '', u)"><BarChart3 :size="17" /><span><strong>验证评估</strong><small>指标、测试与复现</small></span></button>
      <button :class="{ active: tab === 'registry' }" @click="tab = 'registry'; const u = new URL(window.location.href); u.searchParams.set('tab', 'registry'); history.replaceState({}, '', u)"><GitBranch :size="17" /><span><strong>Agent 注册与版本</strong><small>发现、负载与版本切换</small></span></button>
      <button :class="{ active: tab === 'access' }" @click="tab = 'access'; const u = new URL(window.location.href); u.searchParams.set('tab', 'access'); history.replaceState({}, '', u)"><ShieldCheck :size="17" /><span><strong>权限与审计</strong><small>账号、角色与操作日志</small></span></button>
    </div>
    <AgentStudio v-if="tab === 'agents'" :user="user" />
    <Evaluation v-else-if="tab === 'evaluation'" />
    <AgentRegistryPanel v-else-if="tab === 'registry'" :user="user" />
    <AccessControl v-else :user="user" />
  </div>
</template>
