<script setup>
import { ref } from 'vue'
import { KeyRound, X } from '@lucide/vue'
import { api } from '../api'

const emit = defineEmits(['close', 'success'])
const currentPassword = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const loading = ref(false)
const error = ref('')

async function submit() {
  if (newPassword.value.length < 10) {
    error.value = '新密码至少需要 10 个字符'
    return
  }
  if (newPassword.value !== confirmPassword.value) {
    error.value = '两次输入的新密码不一致'
    return
  }
  loading.value = true
  error.value = ''
  try {
    const result = await api.changePassword({ current_password: currentPassword.value, new_password: newPassword.value })
    emit('success', result)
  } catch (exc) {
    error.value = exc.message
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="password-modal" @click.self="emit('close')">
    <form class="password-dialog" @submit.prevent="submit">
      <button type="button" class="icon-button password-close" @click="emit('close')"><X :size="17" /></button>
      <div class="password-dialog-icon"><KeyRound :size="22" /></div>
      <div class="eyebrow">ACCOUNT SECURITY</div>
      <h2>修改登录密码</h2>
      <p>修改后所有已登录会话会立即失效，需要使用新密码重新登录。</p>
      <label><span>当前密码</span><input v-model="currentPassword" type="password" autocomplete="current-password" maxlength="200" /></label>
      <label><span>新密码</span><input v-model="newPassword" type="password" autocomplete="new-password" maxlength="200" placeholder="至少 10 个字符" /></label>
      <label><span>确认新密码</span><input v-model="confirmPassword" type="password" autocomplete="new-password" maxlength="200" /></label>
      <div v-if="error" class="inline-error">{{ error }}</div>
      <button class="primary-action password-submit" type="submit" :disabled="loading">{{ loading ? '保存中...' : '确认修改' }}</button>
    </form>
  </div>
</template>
