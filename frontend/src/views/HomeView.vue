<script setup lang="ts">
import { onMounted } from 'vue'
import { useNoticesStore } from '../stores/useNoticesStore'
import { useRemindersStore } from '../stores/useRemindersStore'

const notices = useNoticesStore()
const reminders = useRemindersStore()

onMounted(async () => {
  await Promise.all([
    notices.fetchMeta().catch(() => {}),
    notices.fetchFilters().catch(() => {}),
    notices.fetchNotices({ page: 1, page_size: 10 }).catch(() => {}),
    notices.fetchStatusCounts().catch(() => {}),
    reminders.fetchPendingCount().catch(() => {}),
  ])
})

function statusLabel(v: string): string {
  return notices.meta?.statuses?.find((s) => s.value === v)?.label ?? v
}

function typeLabel(v?: string | null): string {
  if (!v) return '未分类'
  return notices.meta?.notice_types?.find((t) => t.value === v)?.label ?? v
}
</script>

<template>
  <n-space vertical size="large">
    <n-card title="数据概览">
      <n-grid :cols="5" :x-gap="12">
        <n-grid-item>
          <n-statistic :label="`未提取 (${statusLabel('raw')})`">
            <template #default>{{ notices.statusCounts.raw }}</template>
          </n-statistic>
        </n-grid-item>
        <n-grid-item>
          <n-statistic :label="`已提取 (${statusLabel('extracted')})`">
            <template #default>{{ notices.statusCounts.extracted }}</template>
          </n-statistic>
        </n-grid-item>
        <n-grid-item>
          <n-statistic :label="`部分提取 (${statusLabel('partial')})`">
            <template #default>{{ notices.statusCounts.partial }}</template>
          </n-statistic>
        </n-grid-item>
        <n-grid-item>
          <n-statistic :label="`提取失败 (${statusLabel('failed')})`">
            <template #default>{{ notices.statusCounts.failed }}</template>
          </n-statistic>
        </n-grid-item>
        <n-grid-item>
          <n-statistic label="待处理提醒">
            <template #default>
              <span :style="{ color: reminders.pendingCount > 0 ? '#d03050' : undefined }">
                {{ reminders.pendingCount }}
              </span>
            </template>
          </n-statistic>
        </n-grid-item>
      </n-grid>
    </n-card>

    <n-card title="近期通知">
      <div v-if="notices.list.length === 0">暂无通知</div>
      <n-list v-else>
        <n-list-item v-for="item in notices.list" :key="item.id">
          <template #default>
            <n-space vertical size="small">
              <n-space align="center" size="small">
                <n-tag size="small" :bordered="false" type="info">{{ typeLabel(item.notice_type) }}</n-tag>
                <span>{{ item.title }}</span>
              </n-space>
              <div>{{ item.source }} · {{ item.published_at ?? item.crawled_at }}</div>
            </n-space>
          </template>
        </n-list-item>
      </n-list>
    </n-card>
  </n-space>
</template>