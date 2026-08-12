import { ref } from 'vue'
import { endpoints } from '../api/endpoints'
import { get, post } from '../api/http'
import type { TaskView } from '../api/tasks'

export function useTaskPoll() {
  const polling = ref(false)

  async function poll(taskId: number, onProgress?: (task: TaskView) => void, signal?: AbortSignal): Promise<TaskView> {
    polling.value = true
    return new Promise((resolve, reject) => {
      const tick = async () => {
        if (signal?.aborted) {
          polling.value = false
          return reject(new DOMException('aborted', 'AbortError'))
        }
        try {
          const task = await get<TaskView>(endpoints.tasks.detail(taskId), undefined, { signal })
          onProgress?.(task)
          if (task.status === 'success' || task.status === 'failed') {
            polling.value = false
            resolve(task)
          } else {
            setTimeout(tick, 600)
          }
        } catch (e) {
          polling.value = false
          reject(e)
        }
      }
      tick()
    })
  }

  async function submitAndPoll(type: string, params?: Record<string, unknown>, onProgress?: (task: TaskView) => void, signal?: AbortSignal) {
    // Use shared post helper so signal/other options can be passed
    const task = await post<TaskView>(endpoints.tasks.list, { type, params: params ?? {} }, { signal })
    return poll(task.id, onProgress, signal)
  }

  return { polling, poll, submitAndPoll }
}
