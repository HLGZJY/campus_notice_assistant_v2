<script setup lang="ts">
import { ref, onMounted, h } from 'vue'
import { useRoute } from 'vue-router'
import { endpoints } from './api/endpoints'
import { get } from './api/http'
import router from './router'

const route = useRoute()
const pendingCount = ref(0)

async function fetchPendingCount() {
  try {
    pendingCount.value = await get<number>(endpoints.reminders.pendingCount)
  } catch {
    pendingCount.value = 0
  }
}

const menuItems = [
  { key: '/', label: '首页' },
  { key: '/notices', label: '通知浏览' },
  { key: '/todos', label: '待办清单' },
  { key: '/qa', label: '智能问答' },
  { key: '/subscriptions', label: '订阅管理' },
  { key: '/config', label: '系统配置' },
  { key: '/market', label: '服务市场' },
]

function renderMenuItem(item: typeof menuItems[number]) {
  return h('span', { style: { display: 'flex', alignItems: 'center', gap: '8px' } }, [
    item.label,
    item.key === '/' && pendingCount.value > 0 ? h('span', { style: { color: '#f00' } }, `(${pendingCount.value})`) : null,
  ])
}

function onMenuUpdate(key: string) {
  router.push(key)
}

onMounted(() => {
  fetchPendingCount()
  setInterval(fetchPendingCount, 30000)
})
</script>

<template>
  <n-layout has-sider style="min-height: 100vh">
    <n-layout-sider bordered width="220" :native-scrollbar="false">
      <n-menu
        :value="route.path"
        :options="menuItems.map(item => ({ key: item.key, label: () => renderMenuItem(item) }))"
        @update:value="onMenuUpdate"
      />
    </n-layout-sider>
    <n-layout style="padding: 24px">
      <n-layout-content>
        <router-view />
      </n-layout-content>
    </n-layout>
  </n-layout>
</template>
