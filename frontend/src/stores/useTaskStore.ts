import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { endpoints } from '../api/endpoints'
import { get, post } from '../api/http'
import type { TaskCreateResult, TaskView } from '../api/schema'
import type { TaskStatus } from '../api/tasks'

const POLL_FAST = 1500 // 有 running 时高频轮询
const POLL_SLOW = 10000 // 无 running 时低频轮询
const POLL_INTERVAL = 600 // 单任务轮询间隔

export const useTaskStore = defineStore('task', () => {
  const tasks = ref<TaskView[]>([])
  const drawerOpen = ref(false)

  const runningCount = computed(
    () => tasks.value.filter((t) => t.status === 'running' || t.status === 'queued').length,
  )
  const hasRunning = computed(() => runningCount.value > 0)

  // ---------- 列表获取 ----------

  async function fetchList(status?: TaskStatus) {
    try {
      tasks.value = await get<TaskView[]>(endpoints.tasks.list, { status, limit: 50 })
    } catch {
      // 静默失败，不打断 UI
    }
  }

  async function refreshRunning() {
    try {
      const running = await get<TaskView[]>(endpoints.tasks.list, { status: 'running', limit: 50 })
      const runningIds = new Set(running.map((t) => t.id))
      // 替换 running 项，保留其它状态项
      const others = tasks.value.filter((t) => !runningIds.has(t.id))
      tasks.value = [...running, ...others].sort((a, b) => b.id - a.id)
    } catch {
      // 静默
    }
  }

  // ---------- 单任务轮询 ----------

  function updateTaskInList(task: TaskView) {
    const idx = tasks.value.findIndex((t) => t.id === task.id)
    if (idx >= 0) {
      tasks.value[idx] = task
    } else {
      tasks.value.unshift(task)
    }
  }

  function pollSingle(
    taskId: number,
    onProgress?: (task: TaskView) => void,
  ): Promise<TaskView> {
    return new Promise((resolve, reject) => {
      const tick = async () => {
        try {
          const task = await get<TaskView>(endpoints.tasks.detail(taskId))
          updateTaskInList(task)
          onProgress?.(task)
          if (task.status === 'success' || task.status === 'failed') {
            resolve(task)
          } else {
            setTimeout(tick, POLL_INTERVAL)
          }
        } catch (e) {
          reject(e)
        }
      }
      tick()
    })
  }

  // ---------- 提交任务（后端幂等去重） ----------

  async function submit(
    type: string,
    params?: Record<string, unknown>,
    onProgress?: (task: TaskView) => void,
  ): Promise<TaskView> {
    const result = await post<TaskCreateResult>(endpoints.tasks.list, { type, params: params ?? {} })
    // 拉取最新列表把新任务纳入展示
    await fetchList()
    // 启动单任务轮询（即使后端幂等返回了已有任务，也正常轮询其进度）
    return pollSingle(result.task_id, onProgress)
  }

  // ---------- 全局轮询 ----------

  let pollTimer: ReturnType<typeof setTimeout> | undefined
  let polling = false

  async function globalTick() {
    if (!polling) return
    if (hasRunning.value) {
      await refreshRunning()
      pollTimer = setTimeout(globalTick, POLL_FAST)
    } else {
      // 无 running 时低频拉全列表（捕获后端直接入库的任务）
      await fetchList()
      pollTimer = setTimeout(globalTick, POLL_SLOW)
    }
  }

  function startGlobalPolling() {
    if (polling) return
    polling = true
    globalTick()
  }

  function stopGlobalPolling() {
    polling = false
    if (pollTimer) {
      clearTimeout(pollTimer)
      pollTimer = undefined
    }
  }

  // ---------- 抽屉 ----------

  function openDrawer() {
    drawerOpen.value = true
  }
  function closeDrawer() {
    drawerOpen.value = false
  }
  function toggleDrawer() {
    drawerOpen.value = !drawerOpen.value
  }

  function clearFinished() {
    tasks.value = tasks.value.filter((t) => t.status === 'running' || t.status === 'queued')
  }

  return {
    tasks,
    drawerOpen,
    runningCount,
    hasRunning,
    fetchList,
    refreshRunning,
    pollSingle,
    submit,
    startGlobalPolling,
    stopGlobalPolling,
    openDrawer,
    closeDrawer,
    toggleDrawer,
    clearFinished,
  }
})
