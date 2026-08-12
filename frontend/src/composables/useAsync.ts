import { ref, type Ref } from 'vue'

export interface AsyncState<T> {
  data: Ref<T | undefined>
  loading: Ref<boolean>
  error: Ref<Error | undefined>
  run: (signal?: AbortSignal) => Promise<T>
  cancel: () => void
}

export function useAsync<T>(fn: (signal?: AbortSignal) => Promise<T>) {
  const data = ref<T>()
  const loading = ref(false)
  const error = ref<Error>()
  let controller: AbortController | null = null

  const run = async (signal?: AbortSignal) => {
    loading.value = true
    error.value = undefined
    // if caller passed a signal, use it; otherwise create our own to allow cancel
    controller = signal ? null : new AbortController()
    const activeSignal = signal ?? controller?.signal
    try {
      const res = await fn(activeSignal)
      data.value = res
      return res
    } catch (e) {
      if ((e as any)?.name === 'AbortError') {
        error.value = new Error('aborted')
      } else {
        error.value = e instanceof Error ? e : new Error(String(e))
      }
      throw e
    } finally {
      loading.value = false
    }
  }

  const cancel = () => {
    if (controller) {
      controller.abort()
      controller = null
    }
  }

  return { data, loading, error, run, cancel }
}
