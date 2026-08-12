import { defineStore } from 'pinia'
import { ref } from 'vue'
import { endpoints } from '../api/endpoints'
import { get, post } from '../api/http'

export interface TodoItem {
  id: number
  notice_id: number
  notice_title?: string
  action: string
  due_at?: string
  priority: string
  status: string
  created_at: string
  completed_at?: string
}

export interface TodoStats {
  pending: number
  done: number
  skipped: number
  total: number
}

export const useTodosStore = defineStore('todos', () => {
  const list = ref<TodoItem[]>([])
  const stats = ref<TodoStats>({ pending: 0, done: 0, skipped: 0, total: 0 })

  async function fetchTodos(status?: string) {
    list.value = await get<TodoItem[]>(endpoints.todos.list, { status })
  }

  async function fetchStats() {
    stats.value = await get<TodoStats>(endpoints.todos.stats)
  }

  async function updateStatus(id: number, status: string) {
    await post(endpoints.todos.status(id), { status })
    await fetchTodos()
    await fetchStats()
  }

  return { list, stats, fetchTodos, fetchStats, updateStatus }
})
