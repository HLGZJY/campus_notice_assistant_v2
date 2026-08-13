import { endpoints } from './endpoints'
import { get, post } from './http'
import type { TaskCreateResult, TaskView } from './schema'

export type TaskStatus = 'queued' | 'running' | 'success' | 'failed'

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
  const result = await post<TaskCreateResult>(endpoints.tasks.list, { type, params: params ?? {} })
  return pollTask(result.task_id, undefined, 600)
}
