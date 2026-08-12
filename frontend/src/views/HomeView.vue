<script setup lang="ts">
import { onMounted } from 'vue'
import { useNoticesStore } from '../stores/useNoticesStore'
import { useRemindersStore } from '../stores/useRemindersStore'

const notices = useNoticesStore()
const reminders = useRemindersStore()

onMounted(async () => {
  await notices.fetchFilters().catch(() => {})
  await notices.fetchNotices({ limit: 10 }).catch(() => {})
  await reminders.fetchPendingCount().catch(() => {})
})
</script>

<template>
  <n-space vertical size="large">
    <n-card title="欢迎">
      <div>欢迎使用 Campus Notice Assistant — 首页概览待完成功能的占位视图。</div>
    </n-card>

    <n-card title="近期通知">
      <div v-if="notices.list.length === 0">暂无通知</div>
      <n-list v-else>
        <n-list-item v-for="item in notices.list" :key="item.id">
          <template #title>{{ item.title }}</template>
          <template #desc>{{ item.source }} · {{ item.published_at ?? item.crawled_at }}</template>
        </n-list-item>
      </n-list>
    </n-card>

    <n-card title="提醒（待处理）">
      <div>待处理提醒：{{ reminders.pendingCount }}</div>
    </n-card>
  </n-space>
</template>
