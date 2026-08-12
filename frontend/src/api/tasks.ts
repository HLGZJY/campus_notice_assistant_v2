import { endpoints } from './endpoints'
import { get } from './http'

export type TaskStatus = 'queued' | 'running' | 'success' | 'failed'

export interface TaskView {
  id: number
  type: string
  params?: Record<string, unknown>
  status: TaskStatus
  progress: number
  result?: Record<string, unknown>
  error?: string
  created_at: string
  updated_at: string
}

export function pollTask(
  taskId: number,
  onProgress?: (task: TaskView) => void,
  interval = 600,
): Promise<TaskView> {
  return new Promise((resolve, reject) => {
    const tick = async () => {
      try {
        const task = await get<TaskView>(endpoints.tasks.detail(taskId))
        onProgress?.(task)
        if (task.status === 'success' || task.status === 'failed') {
          resolve(task)
        } else {
          setTimeout(tick, interval)
        }
      } catch (e) {
        reject(e)
      }
    }
    tick()
  })
}

export async function submitTask(type: string, params?: Record<string, unknown>): Promise<TaskView> {
  const res = await fetch(endpoints.tasks.list, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ type, params: params ?? {} }),
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '提交任务失败')
    throw new Error(`${res.status}: ${text}`)
  }
  const task = (await res.json()) as TaskView
  return new Promise((resolve, reject) => {
    pollTask(task.id, undefined, 600)
      .then(resolve)
      .catch(reject)
  })
}
