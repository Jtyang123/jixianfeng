<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  AlertTriangle, ChevronRight, Database, History, KeyRound, LayoutDashboard, LogOut,
  Menu, Network, ShieldCheck, UserPlus, UserRound, X,
} from '@lucide/vue'
import Workbench from './views/Workbench.vue'
import DataCenter from './views/DataCenter.vue'
import EventManager from './views/EventManager.vue'
import SystemCenter from './views/SystemCenter.vue'
import RunHistory from './views/RunHistory.vue'
import AuthLogin from './views/AuthLogin.vue'
import PasswordDialog from './views/PasswordDialog.vue'
import { api } from './api'

const active = ref('workbench')
const sidebarOpen = ref(false)
const dashboard = ref({})
const systemStatus = ref({})
const authUser = ref(null)
const authRequired = ref(true)
const authLoading = ref(true)
const passwordDialogOpen = ref(false)

const navItems = [
  { id: 'workbench', label: '调度工作台', caption: '生成、核验与重算', icon: LayoutDashboard },
  { id: 'data', label: '数据中心', caption: '物资库与库存快照', icon: Database },
  { id: 'events', label: '真实事件', caption: '导入与人工确认', icon: AlertTriangle },
  { id: 'orchestration', label: '智能体中心', caption: '编排、工具与评估', icon: Network },
  { id: 'history', label: '运行记录', caption: '审计与回放', icon: History },
]

const current = computed(() => navItems.find((item) => item.id === active.value))
const pageComponent = computed(() => ({
  workbench: Workbench,
  data: DataCenter,
  events: EventManager,
  orchestration: SystemCenter,
  history: RunHistory,
}[active.value]))
const dateLabel = new Intl.DateTimeFormat('zh-CN', {
  month: 'long', day: 'numeric', weekday: 'short',
}).format(new Date())

async function refreshDashboard() {
  try {
    const [dashboardData, statusData] = await Promise.all([api.dashboard(), api.systemStatus()])
    dashboard.value = dashboardData
    systemStatus.value = statusData
  } catch (_) {}
}
async function loadSession() {
  authLoading.value = true
  try {
    const result = await api.me()
    authUser.value = result.user
    authRequired.value = Boolean(result.auth_required)
  } catch (exc) {
    authUser.value = null
    authRequired.value = exc.status === 401
  } finally {
    authLoading.value = false
  }
}

async function handleLogin(result) {
  authUser.value = result.user
  authRequired.value = Boolean(result.auth_required)
  await refreshDashboard()
}

async function logout() {
  try { await api.logout() } catch (_) {}
  authUser.value = null
  authRequired.value = true
}

function handlePasswordChanged() {
  passwordDialogOpen.value = false
  authUser.value = null
  authRequired.value = true
}

function handleUnauthorized() {
  authUser.value = null
  authRequired.value = true
}

function openAccountManagement() {
  navigate('orchestration')
  const url = new URL(window.location.href)
  url.searchParams.set('tab', 'access')
  window.history.replaceState({}, '', url)
}

function navigate(id) {
  active.value = id
  sidebarOpen.value = false
  const url = new URL(window.location.href)
  url.searchParams.set('page', id)
  window.history.replaceState({}, '', url)
}
onMounted(async () => {
  const requested = new URLSearchParams(window.location.search).get('page')
  const aliases = { materials: 'data', inventory: 'data', evaluation: 'orchestration' }
  const page = aliases[requested] || requested
  if (navItems.some((item) => item.id === page)) active.value = page
  window.addEventListener('farmmatch:unauthorized', handleUnauthorized)
  await loadSession()
  if (authUser.value) await refreshDashboard()
})
onBeforeUnmount(() => window.removeEventListener('farmmatch:unauthorized', handleUnauthorized))
</script>

<template>
  <div v-if="authLoading" class="auth-loading"><div class="loading-orbit" /><strong>正在验证登录状态</strong></div>
  <AuthLogin v-else-if="authRequired && !authUser" @success="handleLogin" />
  <div v-else class="app-shell">
    <aside class="sidebar" :class="{ 'sidebar--open': sidebarOpen }">
      <div class="brand">
        <div class="brand-mark">
          <ShieldCheck :size="22" :stroke-width="2.4" />
          <span class="brand-pulse" />
        </div>
        <div>
          <div class="brand-name">急先锋</div>
          <div class="brand-en">RAPID RESPONSE OS</div>
        </div>
        <button class="icon-button sidebar-close" @click="sidebarOpen = false"><X :size="18" /></button>
      </div>

      <div class="mission-chip">
        <span class="live-dot" /><span>应急演练沙盘</span><span class="mono">SIM-01</span>
      </div>

      <nav class="nav-list">
        <button v-for="item in navItems" :key="item.id" class="nav-item"
          :class="{ 'nav-item--active': active === item.id }" @click="navigate(item.id)">
          <component :is="item.icon" :size="19" />
          <span class="nav-copy"><strong>{{ item.label }}</strong><small>{{ item.caption }}</small></span>
          <ChevronRight v-if="active === item.id" :size="16" class="nav-arrow" />
        </button>
      </nav>

      <div class="sidebar-spacer" />
      <section class="resource-mini">
        <div class="eyebrow">资源在线概览</div>
        <div class="mini-grid">
          <div><strong>{{ dashboard.materials ?? '--' }}</strong><span>物资</span></div>
          <div><strong>{{ dashboard.warehouses ?? '--' }}</strong><span>仓库</span></div>
          <div><strong>{{ dashboard.vehicles ?? '--' }}</strong><span>车辆</span></div>
        </div>
        <div class="snapshot-line"><Database :size="14" /><span>{{ dashboard.snapshot?.label || '正在读取快照' }}</span></div>
      </section>

      <div class="runtime-card">
        <div class="runtime-head"><span class="runtime-dot" /><span>LOCAL AGENT RUNTIME</span></div>
        <p>四智能体确定性编排已就绪</p>
        <div class="runtime-foot"><span>DB {{ systemStatus.database?.engine || 'sqlite' }}</span><span>{{ systemStatus.agentarts?.enabled ? 'AGENTARTS' : 'LOCAL' }}</span></div>
      </div>
    </aside>

    <button v-if="sidebarOpen" class="sidebar-scrim" @click="sidebarOpen = false" />

    <main class="main-shell">
      <header class="topbar">
        <button class="icon-button mobile-menu" @click="sidebarOpen = true"><Menu :size="20" /></button>
        <div class="page-title">
          <div class="eyebrow">{{ current?.caption }}</div>
          <h1>{{ current?.label }}</h1>
        </div>
        <div class="topbar-actions">
          <span class="date-chip">{{ dateLabel }}</span>
          <div class="auth-user-chip"><UserRound :size="15" /><span><strong>{{ authUser?.display_name }}</strong><small>{{ authUser?.role_label }}</small></span></div>
          <button v-if="authUser?.role === 'admin'" class="auth-manage-button" @click="openAccountManagement"><UserPlus :size="15" />账号管理</button>
          <button class="icon-button" title="修改密码" @click="passwordDialogOpen = true"><KeyRound :size="16" /></button>
          <button class="icon-button" title="退出登录" @click="logout"><LogOut :size="17" /></button>
        </div>
      </header>
      <div class="page-body">
        <component :is="pageComponent" :dashboard="dashboard" :user="authUser"
          @refresh-dashboard="refreshDashboard" @navigate="navigate" />
      </div>
    </main>
    <PasswordDialog v-if="passwordDialogOpen" @close="passwordDialogOpen = false" @success="handlePasswordChanged" />
  </div>
</template>
