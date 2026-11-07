<script setup>
import { ref } from 'vue'
import { Boxes, Database, Truck } from '@lucide/vue'
import MaterialLibrary from './MaterialLibrary.vue'
import Inventory from './Inventory.vue'
import ResourceData from './ResourceData.vue'

defineProps({ dashboard: { type: Object, default: () => ({}) } })
const emit = defineEmits(['refresh-dashboard'])
const requestedTab = new URLSearchParams(window.location.search).get('tab')
const validTabs = ['inventory', 'materials', 'resources']
const tab = ref(validTabs.includes(requestedTab) ? requestedTab : 'inventory')
</script>

<template>
  <div class="center-page">
    <div class="center-switch">
      <button :class="{ active: tab === 'inventory' }" @click="tab = 'inventory'; const u = new URL(window.location.href); u.searchParams.set('tab', 'inventory'); history.replaceState({}, '', u)"><Database :size="17" /><span><strong>库存数据</strong><small>上传与快照</small></span></button>
      <button :class="{ active: tab === 'materials' }" @click="tab = 'materials'; const u = new URL(window.location.href); u.searchParams.set('tab', 'materials'); history.replaceState({}, '', u)"><Boxes :size="17" /><span><strong>物资库</strong><small>主数据与物资选择</small></span></button>
      <button :class="{ active: tab === 'resources' }" @click="tab = 'resources'; const u = new URL(window.location.href); u.searchParams.set('tab', 'resources'); history.replaceState({}, '', u)"><Truck :size="17" /><span><strong>运营资源</strong><small>仓库、安置点、车辆、GPS</small></span></button>
    </div>
    <Inventory v-if="tab === 'inventory'" @refresh-dashboard="emit('refresh-dashboard')" />
    <MaterialLibrary v-else-if="tab === 'materials'" @refresh-dashboard="emit('refresh-dashboard')" />
    <ResourceData v-else @refresh-dashboard="emit('refresh-dashboard')" />
  </div>
</template>
