import { endpoints } from './endpoints'

export const EVENT_TYPES = {
  PAGE_VIEW: 'page_view',
  QA_ASK: 'qa_ask',
  TODO_GENERATE: 'todo_generate',
  TODO_DONE: 'todo_done',
  SERVICE_CLICK: 'service_button_click',
} as const

export function trackEvent(
  eventType: (typeof EVENT_TYPES)[keyof typeof EVENT_TYPES],
  refId?: number,
  note?: string,
): void {
  fetch(endpoints.events, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ event_type: eventType, ref_id: refId, note }),
  }).catch(() => {
    // fire-and-forget: 埋点失败不影响主流程
  })
}
