<script setup>
import { computed, onMounted, ref } from 'vue'
import { Activity, Plus, RefreshCw, ShieldCheck, Trash2, UserCheck, UserX, Users } from '@lucide/vue'
import { api, formatTime } from '../api'

const props = defineProps({ user: { type: Object, default: null } })
const users = ref([])
const logs = ref([])
const loading = ref(true)
const saving = ref(false)
const refreshing = ref(false)
const deletingId = ref(null)
const error = ref('')
const message = ref('')
const form = ref({ username: '', display_name: '', password: '', role: 'viewer' })
const isAdmin = computed(() => props.user?.role === 'admin')
const roleLabels = { viewer: '只读观察员', dispatcher: '调度员', verifier: '核验员', admin: '系统管理员' }

async function load() {
  if (!isAdmin.value) { loading.value = false; return }
  loading.value = true
  error.value = ''
  try {
    const [userRows, auditRows] = await Promise.all([api.users(), api.auditLogs(200)])
    users.value = userRows
    logs.value = auditRows
  } catch (exc) { error.value = exc.message } finally { loading.value = false }
}

async function refreshUsers() {
  if (refreshing.value) return
  refreshing.value = true
  error.value = ''
  message.value = ''
  try {
    const [userRows, auditRows] = await Promise.all([api.users(), api.auditLogs(200)])
    users.value = userRows
    logs.value = auditRows
    message.value = `账号与审计数据已刷新 · ${new Date().toLocaleTimeString('zh-CN', { hour12: false })}`
  } catch (exc) {
    error.value = exc.message
  } finally {
    refreshing.value = false
  }
}

async function deleteUser(user) {
  if (user.id === props.user?.id) {
    error.value = '不能删除当前登录账号'
    return
  }
  if (!window.confirm(`确认永久删除账号“${user.username}”？该账号的登录会话将同时失效。`)) return
  deletingId.value = user.id
  error.value = ''
  message.value = ''
  try {
    await api.deleteUser(user.id)
    users.value = users.value.filter((item) => item.id !== user.id)
    logs.value = await api.auditLogs(200)
    message.value = `账号“${user.username}”已删除`
  } catch (exc) {
    error.value = exc.message
  } finally {
    deletingId.value = null
  }
}
async function createUser() {
  if (form.value.username.trim().length < 2 || form.value.password.length < 10) {
    error.value = '用户名至少 2 个字符，密码至少 10 个字符'
    return
  }
  saving.value = true
  error.value = ''
  message.value = ''
  try {
    const created = await api.createUser({ ...form.value, username: form.value.username.trim(), display_name: form.value.display_name.trim() })
    users.value.push(created)
    message.value = `账号“${created.username}”已创建`
    form.value = { username: '', display_name: '', password: '', role: 'viewer' }
    await load()
  } catch (exc) { error.value = exc.message } finally { saving.value = false }
}

async function toggleUser(user) {
  if (user.id === props.user?.id && user.is_active) {
    error.value = '不能停用当前登录账号'
    return
  }
  error.value = ''
  try {
    const updated = await api.updateUser(user.id, { is_active: !user.is_active })
    const index = users.value.findIndex((item) => item.id === user.id)
    if (index >= 0) users.value[index] = updated
    message.value = `账号“${user.username}”已${updated.is_active ? '启用' : '停用'}`
  } catch (exc) { error.value = exc.message }
}

onMounted(load)
</script>

<template>
  <div class="access-page">
    <section v-if="!isAdmin" class="content-card access-restricted">
      <ShieldCheck :size="28" />
      <h2>需要系统管理员权限</h2>
      <p>当前角色为 {{ user?.role_label || '未登录' }}，只能执行只读或调度业务操作。</p>
    </section>
    <template v-else>
      <section class="access-summary">
        <div><Users :size="20" /><span>平台账号</span><strong>{{ users.length }}</strong></div>
        <div><Activity :size="20" /><span>审计记录</span><strong>{{ logs.length }}</strong></div>
        <div><ShieldCheck :size="20" /><span>当前角色</span><strong>{{ user?.role_label }}</strong></div>
      </section>

      <div v-if="message" class="import-result">{{ message }}</div>
      <div v-if="error" class="inline-error">{{ error }}</div>

      <section class="content-card access-create-card">
        <div class="workflow-head"><div><div class="eyebrow">USER PROVISIONING</div><h2>创建平台账号</h2><p>管理员创建账号并分配最小必要权限；密码不会在列表和审计中显示。</p></div><Users :size="20" /></div>
        <form class="access-create-grid" @submit.prevent="createUser">
          <label><span>用户名 *</span><input v-model="form.username" maxlength="64" placeholder="例如：dispatcher01" /></label>
          <label><span>显示名称</span><input v-model="form.display_name" maxlength="80" placeholder="例如：值班调度员" /></label>
          <label><span>初始密码 *</span><input v-model="form.password" type="password" maxlength="200" placeholder="至少 10 个字符" /></label>
          <label><span>角色</span><select v-model="form.role"><option value="viewer">只读观察员</option><option value="dispatcher">调度员</option><option value="verifier">核验员</option><option value="admin">系统管理员</option></select></label>
          <div class="access-create-actions full-span"><span>普通调度员可维护资源和发起调度；观察员只能查看。</span><button class="primary-action" :disabled="saving"><Plus :size="14" />{{ saving ? '创建中...' : '创建账号' }}</button></div>
        </form>
      </section>

      <section class="content-card">
        <div class="workflow-head"><div><div class="eyebrow">ACCOUNT DIRECTORY</div><h2>账号与权限</h2></div><button class="secondary-action" :disabled="refreshing" @click="refreshUsers"><RefreshCw :size="14" :class="{ spin: refreshing }" />{{ refreshing ? '刷新中...' : '刷新' }}</button></div>
        <div class="table-wrap access-table-wrap"><table class="data-table"><thead><tr><th>账号</th><th>角色</th><th>状态</th><th>最后登录</th><th>操作</th></tr></thead><tbody>
          <tr v-for="item in users" :key="item.id"><td><strong>{{ item.display_name }}</strong><small class="cell-sub">{{ item.username }}</small></td><td><span class="soft-tag">{{ roleLabels[item.role] }}</span></td><td><span class="warehouse-state" :class="{ 'warehouse-state--closed': !item.is_active }">{{ item.is_active ? '启用' : '停用' }}</span></td><td>{{ item.last_login_at ? formatTime(item.last_login_at) : '尚未登录' }}</td><td><div class="inventory-row-actions"><button type="button" class="record-edit" @click="toggleUser(item)"><UserX v-if="item.is_active" :size="12" /><UserCheck v-else :size="12" />{{ item.is_active ? '停用' : '启用' }}</button><button type="button" class="record-delete" :disabled="deletingId === item.id || item.id === user?.id" :title="item.id === user?.id ? '不能删除当前登录账号' : '永久删除账号'" @click="deleteUser(item)"><Trash2 :size="12" />{{ deletingId === item.id ? '删除中' : '删除' }}</button></div></td></tr>
        </tbody></table></div>
      </section>

      <section class="content-card">
        <div class="workflow-head"><div><div class="eyebrow">AUDIT TRAIL</div><h2>操作审计日志</h2><p>记录登录、资源变更、运行和审批等关键操作。</p></div><Activity :size="20" /></div>
        <div class="table-wrap access-table-wrap"><table class="data-table audit-table"><thead><tr><th>时间</th><th>账号</th><th>角色</th><th>动作</th><th>请求</th><th>状态</th><th>来源 IP</th></tr></thead><tbody>
          <tr v-for="item in logs" :key="item.id"><td>{{ formatTime(item.created_at) }}</td><td>{{ item.username || '匿名' }}</td><td>{{ roleLabels[item.role] || item.role || '--' }}</td><td>{{ item.action }}</td><td class="mono-cell">{{ item.method }} {{ item.path }}</td><td>{{ item.status_code || '--' }}</td><td>{{ item.ip || '--' }}</td></tr>
        </tbody></table><div v-if="!logs.length" class="empty-table"><Activity :size="28" /><strong>暂无审计记录</strong><span>登录或执行写操作后会在此记录。</span></div></div>
      </section>
    </template>
  </div>
</template>
