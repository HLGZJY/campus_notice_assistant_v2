import { get, del } from './http'
import { endpoints } from './endpoints'
import type { QaHistoryPage } from './schema'

export function fetchQaHistory(page = 1, pageSize = 20, userSessionId?: string) {
  return get<QaHistoryPage>(endpoints.qa.history, { page, page_size: pageSize, user_session_id: userSessionId })
}

export function deleteQaHistory(id: number) {
  return del<{ ok: boolean; id: number }>(endpoints.qa.historyDelete(id))
}

export function clearQaHistory(userSessionId?: string) {
  return del<{ ok: boolean; deleted: number }>(endpoints.qa.historyClear, { user_session_id: userSessionId })
}