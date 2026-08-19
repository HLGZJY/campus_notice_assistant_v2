/**
 * 契约类型索引：以 openapi.json 生成的 types.ts（components.schemas）为唯一类型源。
 *
 * 各 store / 视图只允许从这里引用类型，禁止手写契约，消灭漂移。
 * 不直接在 types.ts 头部加别名块，避免 npm run gen:api 重跑时被覆盖。
 */
import type { components } from './types'

export type NoticeSummary = components['schemas']['NoticeSummary']
export type NoticeDetail = components['schemas']['NoticeDetail']
export type NoticePage = components['schemas']['NoticePage']
export type StatusCounts = components['schemas']['StatusCounts']

export type NoticeMeta = components['schemas']['NoticeMeta']
export type NoticeMetaItem = components['schemas']['NoticeMetaItem']
export type NoticeBatchFilter = components['schemas']['NoticeBatchFilter']
export type NoticeBatchRequest = components['schemas']['NoticeBatchRequest']
export type NoticeResetRequest = components['schemas']['NoticeResetRequest']
export type NoticeMutationResult = components['schemas']['NoticeMutationResult']

export type TodoItem = components['schemas']['TodoItem']
export type TodoStats = components['schemas']['TodoStats']
export type TodoStatusUpdate = components['schemas']['TodoStatusUpdate']
export type TodoUpdateRequest = components['schemas']['TodoUpdateRequest']

export type ReminderItem = components['schemas']['ReminderItem']
export type ReminderStats = components['schemas']['ReminderStats']
export type ReminderStatusUpdate = components['schemas']['ReminderStatusUpdate']

export type SubscriptionItem = components['schemas']['SubscriptionItem']
export type SubscriptionStats = components['schemas']['SubscriptionStats']
export type SubscriptionPreview = components['schemas']['SubscriptionPreview']
export type SubscriptionPreviewRequest = components['schemas']['SubscriptionPreviewRequest']
export type SubscriptionCreateRequest = components['schemas']['SubscriptionCreateRequest']
export type SubscriptionUpdateRequest = components['schemas']['SubscriptionUpdateRequest']
export type SubscriptionToggleRequest = components['schemas']['SubscriptionToggleRequest']
export type SubscriptionMutationResult = components['schemas']['SubscriptionMutationResult']

export type MatchMapRequest = components['schemas']['MatchMapRequest']
export type MatchMapResult = components['schemas']['MatchMapResult']

export type ModelsConfig = components['schemas']['ModelsConfig']
export type ModelProfile = components['schemas']['ModelProfile']
export type ModelsView = components['schemas']['ModelsView']
export type ModelProfileView = components['schemas']['ModelProfileView']
export type ProviderConfig = components['schemas']['ProviderConfig']
export type ProviderView = components['schemas']['ProviderView']
export type SchoolConfig = components['schemas']['SchoolConfig']
export type SourceConfig = components['schemas']['SourceConfig']
export type CrawlConfig = components['schemas']['CrawlConfig']
export type ExtractConfig = components['schemas']['ExtractConfig']
export type ConfigView = components['schemas']['ConfigView']
export type ConfigMutationResult = components['schemas']['ConfigMutationResult']
export type ReloadResult = components['schemas']['ReloadResult']
export type DiskInfo = components['schemas']['DiskInfo']
export type TestSourceRequest = components['schemas']['TestSourceRequest']
export type TestSourceResult = components['schemas']['TestSourceResult']
export type TestModelRequest = components['schemas']['TestModelRequest']
export type TestModelResult = components['schemas']['TestModelResult']
export type ApiKeyRequest = components['schemas']['ApiKeyRequest']
export type ApiKeyResult = components['schemas']['ApiKeyResult']
export type ExtractPreviewItem = components['schemas']['ExtractPreviewItem']
export type ExtractPreviewResponse = components['schemas']['ExtractPreviewResponse']

export type TaskCreateRequest = components['schemas']['TaskCreateRequest']
export type TaskCreateResult = components['schemas']['TaskCreateResult']
export type TaskView = components['schemas']['TaskView']

export type IndexStatsView = components['schemas']['IndexStatsView']
export type SchedulerStatus = components['schemas']['SchedulerStatus']
export type SchedulerJobView = components['schemas']['SchedulerJobView']
export type EventCreateRequest = components['schemas']['EventCreateRequest']
export type EventCreateResult = components['schemas']['EventCreateResult']
export type TokenUsageRow = components['schemas']['TokenUsageRow']
export type TokenUsageSummary = components['schemas']['TokenUsageSummary']

/**
 * SSE done 事件负载里的来源引用（QAResult 的 as_source 转换后契约形态）。
 * 非响应模型，未出现在 openapi.json（sequelize 例外，路由层手动序列化），此处按 api/routes/qa.py 约定声明。
 */
export interface QaSourceRef {
  notice_id: number
  title: string
  url: string
  notice_type: string
  deadline?: string | null
}

/** SSE 事件类型（兼容 qa_service 流式 + 路由层 as_source 转换 + 缓存命中 + 阶段提示）。 */
export type QaStreamEvent =
  | { type: 'delta'; content: string }
  | { type: 'status'; stage: 'retrieval' | 'thinking' | 'generating' | 'cache_hit'; message: string; elapsed_ms: number; similarity?: number }
  | { type: 'done'; answer: string; sources: QaSourceRef[]; retrieved_chunks: number }
  | { type: 'error'; message: string }

/** 问答历史条目（GET /qa/history 返回）。 */
export type QaHistoryItem = components['schemas']['QaHistoryItem']

/** 历史分页响应。 */
export type QaHistoryPage = components['schemas']['QaHistoryPage']
// ---------- 数据源中心（阶段 8） ----------

export type SourceCenterNode = components['schemas']['SourceCenterNode']
export type SourceCenterItem = components['schemas']['SourceCenterItem']
export type SourceCenterOverview = components['schemas']['SourceCenterOverview']
export type SourceCenterAdoptResult = components['schemas']['SourceCenterAdoptResult']
export type SourceCenterPreviewItem = components['schemas']['SourceCenterPreviewItem']
export type SourceCenterPreview = components['schemas']['SourceCenterPreview']
