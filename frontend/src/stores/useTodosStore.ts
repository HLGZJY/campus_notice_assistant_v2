import { defineStore } from 'pinia'
import { ref } from 'vue'
import { endpoints } from '../api/endpoints'
import { get, post } from '../api/http'
import { pollTask } from '../api/tasks'
import type { TaskCreateResult, TaskView, TodoItem, TodoStats } from '../api/schema'

export const useTodosStore = defineStore('todos', () => {
  const list = ref<TodoItem[]>([])
  const stats = ref<TodoStats>({ pending: 0, done: 0, skipped: 0, total: 0 })

  async function fetchTodos(status?: string) {
    list.value = await get<TodoItem[]>(endpoints.todos.list, { status })
  }

  async function fetchStats() {
    stats.value = await get<TodoStats>(endpoints.todos.stats)
  }

  async function mark(id: number, status: string) {
    await post(endpoints.todos.status(id), { status })
    await fetchTodos()
    await fetchStats()
  }

  async function generate(noticeId: number, onProgress?: (task: TaskView) => void) {
    const result = await post<TaskCreateResult>(endpoints.notices.todos(noticeId))
    return await pollTask(result.task_id, onProgress)
  }

  return { list, stats, fetchTodos, fetchStats, mark, generate }
})