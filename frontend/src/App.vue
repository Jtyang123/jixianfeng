<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  AlertTriangle, ChevronDown, ChevronRight, Database, History, KeyRound, LayoutDashboard, LogOut,
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
const accountMenu = ref(null)

const navGroups = [
  {
    label: '业务操作',
    items: [
      { id: 'workbench', label: '开始调度', caption: '录入需求并生成方案', icon: LayoutDashboard },
      { id: 'events', label: '事件管理', caption: '导入与确认灾情', icon: AlertTriangle },
      { id: 'data', label: '资源数据', caption: '物资、库存与运力', icon: Database },
    ],
  },
  {
    label: '系统与证据',
    items: [
      { id: 'orchestration', label: '智能体', caption: '编排、注册与评估', icon: Network },
      { id: 'history', label: '运行记录', caption: '报告、审批与回放', icon: History },
    ],
  },
]
const navItems = navGroups.flatMap((group) => group.items)

const current = computed(() => navItems.find((item) => item.id === active.value))
const pageComponent = computed(() => ({
  workbench: Workbench,
  data: DataCenter,
  events: EventManager,
  orchestration: SystemCenter,
  history: RunHistory,
}[active.value]))


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

function closeAccountMenu() {
  if (accountMenu.value) accountMenu.value.open = false
}

function handleDocumentClick(event) {
  if (accountMenu.value?.open && !accountMenu.value.contains(event.target)) closeAccountMenu()
}

function openAccountManagement() {
  closeAccountMenu()
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
  document.addEventListener('click', handleDocumentClick)
  await loadSession()
  if (authUser.value) await refreshDashboard()
})
onBeforeUnmount(() => {
  window.removeEventListener('farmmatch:unauthorized', handleUnauthorized)
  document.removeEventListener('click', handleDocumentClick)
})
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

      <nav class="nav-list">
        <template v-for="group in navGroups" :key="group.label">
          <div class="nav-group-label">{{ group.label }}</div>
          <button v-for="item in group.items" :key="item.id" class="nav-item"
            :class="{ 'nav-item--active': active === item.id }" @click="navigate(item.id)">
            <component :is="item.icon" :size="18" />
            <span class="nav-copy"><strong>{{ item.label }}</strong><small>{{ item.caption }}</small></span>
            <ChevronRight v-if="active === item.id" :size="15" class="nav-arrow" />
          </button>
        </template>
      </nav>

      <div class="sidebar-spacer" />
      <div class="sidebar-footer">
        <span class="system-ready"><i />系统运行正常</span>
        <small>{{ systemStatus.database?.engine || 'sqlite' }} · {{ systemStatus.agentarts?.enabled ? 'AgentArts 已连接' : '本地智能体' }}</small>
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
          <span class="simulation-chip"><span />{{ systemStatus.readiness?.mode === 'operational' ? '运营数据' : '演练数据' }}</span>
          <details ref="accountMenu" class="account-menu">
            <summary class="account-summary">
              <span class="account-avatar"><UserRound :size="16" /></span>
              <span><strong>{{ authUser?.display_name }}</strong><small>{{ authUser?.role_label }}</small></span>
              <ChevronDown :size="14" />
            </summary>
            <div class="account-popover">
              <button v-if="authUser?.role === 'admin'" @click="openAccountManagement"><UserPlus :size="15" />账号管理</button>
              <button @click="closeAccountMenu(); passwordDialogOpen = true"><KeyRound :size="15" />修改密码</button>
              <button class="account-logout" @click="logout"><LogOut :size="15" />退出登录</button>
            </div>
          </details>
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
