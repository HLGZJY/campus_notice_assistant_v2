<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useNoticesStore } from '../stores/useNoticesStore'
import { useTaskPoll } from '../composables/useTaskPoll'

const notices = useNoticesStore()
const { submitAndPoll } = useTaskPoll()
const page = ref(1)

onMounted(async () => {
  await notices.fetchFilters().catch(() => {})
  await notices.fetchNotices({ page: page.value, limit: 20 }).catch(() => {})
})

async function generateTodos(noticeId: number) {
  await submitAndPoll('generate_notice_todos', { notice_id: noticeId }, (_task) => {
    // progress handler placeholder
  })
}
</script>

<template>
  <n-card title="通知列表">
    <div v-if="notices.list.length === 0">暂无通知</div>
    <n-list v-else>
      <n-list-item v-for="item in notices.list" :key="item.id">
        <template #title>{{ item.title }}</template>
        <template #desc>
          {{ item.source }} · {{ item.published_at ?? item.crawled_at }}
          <n-button size="small" @click="generateTodos(item.id)" style="margin-left: 12px">生成待办</n-button>
        </template>
      </n-list-item>
    </n-list>
  </n-card>
</template>
