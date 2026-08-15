import { defineStore } from 'pinia'
import { ref } from 'vue'
import { endpoints } from '../api/endpoints'
import { get, patch, post } from '../api/http'
import { pollTask } from '../api/tasks'
import type { TaskCreateResult, TaskView, TodoItem, TodoStats, TodoUpdateRequest } from '../api/schema'

export const useTodosStore = defineStore('todos', () => {
  const list = ref<TodoItem[]>([])
  const stats = ref<TodoStats>({ pending: 0, done: 0, skipped: 0, total: 0 })
  const loading = ref(false)

  async function fetchTodos(status?: string) {
    loading.value = true
    try {
      list.value = await get<TodoItem[]>(endpoints.todos.list, { status })
    } finally {
      loading.value = false
    }
  }

  async function fetchStats() {
    stats.value = await get<TodoStats>(endpoints.todos.stats)
  }

  async function mark(id: number, status: string) {
    await post(endpoints.todos.status(id), { status })
    await fetchTodos()
    await fetchStats()
  }

  async function update(id: number, payload: TodoUpdateRequest) {
    await patch<TodoItem>(endpoints.todos.update(id), payload)
    await fetchTodos()
    await fetchStats()
  }

  async function generate(noticeId: number, onProgress?: (task: TaskView) => void) {
    const result = await post<TaskCreateResult>(endpoints.notices.todos(noticeId))
    return await pollTask(result.task_id, onProgress)
  }

  return { list, stats, loading, fetchTodos, fetchStats, mark, update, generate }
})