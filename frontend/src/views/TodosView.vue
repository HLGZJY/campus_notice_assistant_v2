<script setup lang="ts">
import { onMounted } from 'vue'
import { useTodosStore } from '../stores/useTodosStore'

const todos = useTodosStore()

onMounted(async () => {
  await todos.fetchTodos().catch(() => {})
  await todos.fetchStats().catch(() => {})
})

async function markDone(id: number) {
  await todos.updateStatus(id, 'done')
}
</script>

<template>
  <n-card title="待办清单">
    <div v-if="todos.list.length === 0">暂无待办</div>
    <n-list v-else>
      <n-list-item v-for="t in todos.list" :key="t.id">
        <template #title>{{ t.action }}</template>
        <template #desc>{{ t.notice_title }} · {{ t.due_at }}</template>
        <template #extra>
          <n-button size="small" @click="markDone(t.id)">完成</n-button>
        </template>
      </n-list-item>
    </n-list>
  </n-card>
</template>
