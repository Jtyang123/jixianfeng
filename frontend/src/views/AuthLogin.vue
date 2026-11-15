<script setup>
import { ref } from 'vue'
import { ArrowRight, LockKeyhole, RadioTower, ShieldCheck, UserRound } from '@lucide/vue'
import { api } from '../api'

const emit = defineEmits(['success'])
const DEMO_USERNAME = 'admin'
const DEMO_PASSWORD = '4JHcg071wjryH1e_uupnZX1Q'
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
        <p>评委演示环境已自动填入管理员账号，直接点击“进入平台”。</p>
        <div class="auth-demo-credentials" data-testid="demo-credentials"><strong>演示账号</strong><code>{{ DEMO_USERNAME }}</code><strong>密码</strong><code>{{ DEMO_PASSWORD }}</code></div>
        <label><span>用户名</span><div class="auth-input"><UserRound :size="16" /><input v-model="username" autocomplete="username" maxlength="64" placeholder="请输入用户名" /></div></label>
        <label><span>密码</span><div class="auth-input"><LockKeyhole :size="16" /><input v-model="password" type="password" autocomplete="current-password" maxlength="200" placeholder="请输入密码" /></div></label>
        <div v-if="error" class="inline-error">{{ error }}</div>
        <button class="primary-action auth-submit" type="submit" :disabled="loading">{{ loading ? '登录中...' : '进入平台' }}<ArrowRight :size="16" /></button>
        <small>演示密码仅用于本地评审；正式部署请修改密码并关闭自动填入。</small>
      </form>
    </section>
  </main>
</template>
