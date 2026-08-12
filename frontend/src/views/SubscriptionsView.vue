<script setup lang="ts">
import { onMounted } from 'vue'
import { useSubscriptionsStore } from '../stores/useSubscriptionsStore'

const subs = useSubscriptionsStore()

onMounted(async () => {
  await subs.fetchList().catch(() => {})
  await subs.fetchStats().catch(() => {})
})

async function toggle(id: number) {
  await subs.toggle(id).catch(() => {})
  await subs.fetchList().catch(() => {})
}
</script>

<template>
  <n-card title="订阅管理">
    <div v-if="subs.list.length === 0">暂无订阅</div>
    <n-list v-else>
      <n-list-item v-for="s in subs.list" :key="s.id">
        <template #title>{{ s.name }}</template>
        <template #desc>Active: {{ s.active }}</template>
        <template #extra>
          <n-button size="small" @click="toggle(s.id)">切换</n-button>
        </template>
      </n-list-item>
    </n-list>
  </n-card>
</template>
