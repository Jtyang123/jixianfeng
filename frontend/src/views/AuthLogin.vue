<script setup>
import { ref } from 'vue'
import { ArrowRight, LockKeyhole, RadioTower, ShieldCheck, UserRound } from '@lucide/vue'
import { api } from '../api'

const emit = defineEmits(['success'])
// 演示凭据只在构建期注入，源码与仓库中不保留明文密码。
// 本地/提交包构建：在 .env 里设置 VITE_DEMO_USERNAME / VITE_DEMO_PASSWORD。
const DEMO_USERNAME = import.meta.env.VITE_DEMO_USERNAME || ''
const DEMO_PASSWORD = import.meta.env.VITE_DEMO_PASSWORD || ''
const hasDemoHint = Boolean(DEMO_USERNAME && DEMO_PASSWORD)
const username = ref(DEMO_USERNAME)
const password = ref(DEMO_PASSWORD)
const loading = ref(false)
const error = ref('')

async function submit() {
  if (!username.value.trim() || !password.value) {
    error.value = '请输入用户名和密码'
    return
  }
  loading.value = true
  error.value = ''
  try {
    emit('success', await api.login({ username: username.value.trim(), password: password.value }))
  } catch (exc) {
    error.value = exc.message
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <main class="auth-page">
    <section class="auth-brand-panel">
      <div class="auth-brand-mark"><ShieldCheck :size="28" /></div>
      <div class="auth-kicker">RAPID RESPONSE OS</div>
      <h1>急先锋</h1>
      <p>应急资源智能调度与风险核验平台</p>
      <div class="auth-capabilities">
        <span><RadioTower :size="15" />四智能体实时编排</span>
        <span><ShieldCheck :size="15" />独立核验与人工审批</span>
        <span><LockKeyhole :size="15" />角色权限与全量审计</span>
      </div>
    </section>
    <section class="auth-form-panel">
      <form class="auth-card" @submit.prevent="submit">
        <div class="eyebrow">SECURE ACCESS</div>
        <h2>登录调度平台</h2>

        <div v-if="hasDemoHint" class="auth-demo-credentials" data-testid="demo-credentials"><strong>演示账号</strong><code>{{ DEMO_USERNAME }}</code><strong>密码</strong><code>{{ DEMO_PASSWORD }}</code></div>
        <div v-else class="auth-first-run" data-testid="first-run-hint">
          <strong>首次使用？账号固定为 <code>admin</code></strong>
          <span>密码在首次启动时随机生成：启动窗口会直接打印，也可以打开项目根目录的 <code>data/bootstrap-admin-password.txt</code> 复制。</span>
        </div>
        <label><span>用户名</span><div class="auth-input"><UserRound :size="16" /><input v-model="username" autocomplete="username" maxlength="64" placeholder="请输入用户名" /></div></label>
        <label><span>密码</span><div class="auth-input"><LockKeyhole :size="16" /><input v-model="password" type="password" autocomplete="current-password" maxlength="200" placeholder="请输入密码" /></div></label>
        <div v-if="error" class="inline-error">{{ error }}</div>
        <button class="primary-action auth-submit" type="submit" :disabled="loading">{{ loading ? '登录中...' : '进入平台' }}<ArrowRight :size="16" /></button>
        <small v-if="hasDemoHint">演示口令仅用于评审环境；正式部署请修改密码并移除构建期注入。</small>
        <small v-else>初始管理员密码保存在服务器本地，不在网页中暴露。</small>
      </form>
    </section>
  </main>
</template>
