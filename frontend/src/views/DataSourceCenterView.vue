<template>
  <div class="page-root">
    <!-- 顶部：视图切换 + 搜索/筛选（公共库） -->
    <n-card :bordered="false" class="filter-card">
      <template #header>
        <div class="section-title-wrap">
          <n-icon size="18" color="var(--primary)"><LibraryOutline /></n-icon>
          <span class="section-title-text">数据源</span>
          <span class="section-title-sub">公共数据源库 · 我的数据源 · 改完即存</span>
        </div>
      </template>
      <div class="view-tabs">
        <n-tabs v-model:value="activeView" type="segment" size="small" animated>
          <n-tab-pane name="catalog" tab="公共数据源" />
          <n-tab-pane name="mine" tab="我的数据源" />
        </n-tabs>
        <div v-if="activeView === 'catalog'" class="stat-line">
          <n-tag :bordered="false" type="info" size="small">共 {{ (store.overview.items ?? []).length }} 个公共数据源</n-tag>
          <n-tag :bordered="false" type="success" size="small">已选用 {{ store.overview.adopted_count }} 个</n-tag>
          <n-tag :bordered="false" type="warning" size="small">当前筛选 {{ store.filtered.length }} 个</n-tag>
        </div>
        <div v-else class="stat-line">
          <n-tag :bordered="false" type="success" size="small">我的数据源 {{ mySources.length }} 个</n-tag>
          <n-tag v-if="saveBusy" :bordered="false" type="warning" size="small">保存中…</n-tag>
          <n-tag v-else-if="hasPending" :bordered="false" type="error" size="small">有修改未保存</n-tag>
          <n-tag v-else :bordered="false" type="default" size="small">所有修改已生效</n-tag>
        </div>
      </div>
      <div v-if="activeView === 'catalog'" class="filter-bar">
        <n-input
          v-model:value="store.keyword"
          class="filter-search"
          placeholder="搜索数据源名称 / 组织 / 描述 / 标签"
          clearable
        >
          <template #prefix>
            <n-icon :component="SearchOutline" />
          </template>
        </n-input>
        <n-select
          v-model:value="store.tags"
          class="filter-tags"
          multiple
          placeholder="全部标签"
          clearable
          :options="tagOptions"
        />
        <n-select
          v-model:value="store.orgKey"
          class="filter-org-mobile"
          placeholder="全部组织"
          clearable
          :options="orgOptions"
        />
        <n-button quaternary size="small" @click="store.resetFilters()">重置</n-button>
      </div>
    </n-card>

    <!-- 视图一：公共数据源库（左三级组织树 + 右卡片网格） -->
    <div v-if="activeView === 'catalog'" class="center-layout">
      <n-card :bordered="false" class="org-card">
        <template #header>
          <div class="org-title">
            <n-icon size="15" color="var(--text-3)"><SchoolOutline /></n-icon>
            <span>学校组织架构</span>
          </div>
        </template>
        <div class="org-tree">
          <div
            class="tree-node root"
            :class="{ active: !store.orgKey }"
            @click="selectOrg(null)"
          >
            <span class="node-label">全部数据源</span>
            <span class="node-count">{{ (store.overview.items ?? []).length }}</span>
          </div>
          <template v-for="g in store.overview.tree ?? []" :key="g.key">
            <div class="tree-node group" :class="{ active: store.orgKey === g.key }" @click="selectOrg(g.key)">
              <span class="node-caret" @click.stop="toggleGroup(g.key)">
                {{ expandedGroups.has(g.key) ? '▾' : '▸' }}
              </span>
              <span class="node-label">{{ g.label }}</span>
              <span class="node-count">{{ g.count }}</span>
            </div>
            <div v-if="expandedGroups.has(g.key)" class="tree-children">
              <template v-for="o in g.children ?? []" :key="o.key">
                <div class="tree-node org" :class="{ active: store.orgKey === o.key }" @click="selectOrg(o.key)">
                  <span class="node-caret" @click.stop="toggleOrg(o.key)">
                    {{ expandedOrgs.has(o.key) ? '▾' : '▸' }}
                  </span>
                  <span class="node-label">{{ o.label }}</span>
                  <span class="node-count">{{ o.count }}</span>
                </div>
                <div v-if="expandedOrgs.has(o.key)" class="tree-children">
                  <div
                    v-for="c in o.children ?? []"
                    :key="c.key"
                    class="tree-node leaf"
                    :class="{ active: store.orgKey === c.key }"
                    @click="selectOrg(c.key)"
                  >
                    <span class="node-label">{{ c.label }}</span>
                  </div>
                </div>
              </template>
            </div>
          </template>
        </div>
      </n-card>

      <div class="cards-col">
        <div v-if="pagedItems.length" class="card-grid">
          <div v-for="it in pagedItems" :key="it.id" class="source-card">
            <div class="card-head">
              <div class="card-name" :title="`${it.org}-${it.name}`">{{ it.name }}</div>
              <div class="card-org" :title="it.org">{{ it.org }}</div>
            </div>
            <div class="card-updated">更新于 {{ it.updated_at }}</div>
            <div class="card-desc">{{ it.description }}</div>
            <div class="card-tags">
              <n-tag v-for="t in it.tags ?? []" :key="t" size="small" :bordered="false" round class="tag-chip">
                {{ t }}
              </n-tag>
            </div>
            <div class="card-actions">
              <n-button quaternary size="small" @click="openPreview(it)">
                <template #icon><n-icon><EyeOutline /></n-icon></template>
                预览
              </n-button>
              <n-button
                v-if="!it.adopted"
                type="primary"
                size="small"
                :loading="actingId === it.id"
                @click="onAdopt(it)"
              >
                <template #icon><n-icon><AddOutline /></n-icon></template>
                选用
              </n-button>
              <n-button v-else type="primary" tertiary size="small" @click="onRemove(it)">
                <template #icon><n-icon><CheckmarkCircleOutline /></n-icon></template>
                已选用
              </n-button>
            </div>
          </div>
        </div>
        <n-empty v-else description="没有符合条件的数据源" class="empty-box" />
        <div class="pager">
          <n-pagination
            v-model:page="page"
            :page-count="pageCount"
            :page-size="PAGE_SIZE"
            size="small"
          />
        </div>
      </div>
    </div>

    <!-- 视图二：我的数据源（改完即存） -->
    <div v-else class="mine-layout">
      <div class="mine-toolbar">
        <div class="mine-toolbar-left">
          <span class="mine-tip">参数修改后自动保存并立即生效，无需手动保存</span>
        </div>
        <n-button secondary size="small" @click="addSource">
          <template #icon><n-icon><AddOutline /></n-icon></template>
          添加数据源
        </n-button>
      </div>
      <n-spin :show="myLoading" class="mine-spin">
        <template v-if="mySources.length">
          <div class="mine-list">
            <n-card
              v-for="(s, idx) in mySources"
              :key="idx"
              size="small"
              class="mine-card"
              :class="{ 'mine-card-invalid': invalidIdx.has(idx) }"
            >
              <div class="mine-card-head" @click="toggleMine(idx)">
                <span class="header-index">#{{ idx + 1 }}</span>
                <span class="mine-card-title">{{ s.name || '未命名' }}</span>
                <n-tag
                  v-if="fromCatalogMap.get(s.list_url)"
                  size="small"
                  :bordered="false"
                  type="info"
                  class="mine-from-catalog"
                  @click.stop="jumpToCatalog(s.list_url)"
                  title="来自公共数据源库，点击定位"
                >
                  公共库
                </n-tag>
                <n-tag size="small" :bordered="false" :type="s.enabled ? 'success' : 'default'">
                  {{ s.enabled ? '已启用' : '已停用' }}
                </n-tag>
                <span class="mine-save-state" v-if="saveBusy && savingIdx === idx">
                  <n-spin :size="12" /> 保存中…
                </span>
                <span class="mine-save-state ok" v-else-if="lastSavedAt[idx]">已保存 {{ lastSavedAt[idx] }}</span>
                <span class="mine-save-state pending" v-else-if="pendingIdxSet.has(idx)">待保存…</span>
                <span class="header-spacer" />
                <n-button size="tiny" quaternary type="error" @click.stop="removeMine(idx)">删除</n-button>
                <n-icon size="14" color="var(--text-3)">
                  <component :is="expandedMine.has(idx) ? ChevronUpOutline : ChevronDownOutline" />
                </n-icon>
              </div>
              <div
                v-if="!expandedMine.has(idx)"
                class="mine-card-summary"
                :class="{ clickable: !!s.list_url }"
                :title="s.list_url ? '点击预览样例' : ''"
                @click="openPreviewUrl(s.name, s.list_url)"
              >
                <n-icon v-if="s.list_url" size="12" class="summary-icon"><EyeOutline /></n-icon>
                {{ s.list_url || '未填写列表地址' }}
              </div>
              <n-collapse-transition :show="expandedMine.has(idx)">
                <div class="mine-card-body">
                  <n-form label-placement="left" label-width="96">
                    <n-form-item label="名称">
                      <n-input v-model:value="s.name" placeholder="如 教务处-通知公告" @update:value="markDirty(idx)" />
                    </n-form-item>
                    <n-form-item label="启用">
                      <n-switch v-model:value="s.enabled" @update:value="markDirty(idx)" />
                      <span class="field-hint">停用后定时抓取与全量抓取会跳过该来源</span>
                    </n-form-item>
                    <n-form-item label="列表地址">
                      <n-input v-model:value="s.list_url" placeholder="https://..." @update:value="markDirty(idx)">
                        <template #suffix>
                          <a
                            v-if="s.list_url"
                            class="input-suffix-link"
                            :href="normalizeUrl(s.list_url)"
                            target="_blank"
                            rel="noopener noreferrer"
                            @click.stop
                            title="在新窗口打开列表页"
                          >
                            ↗
                          </a>
                          <span v-else class="input-suffix-disabled" title="请先填写列表地址">↗</span>
                        </template>
                      </n-input>
                    </n-form-item>
                    <n-form-item label="URL 模式">
                      <n-input
                        v-model:value="s.url_pattern"
                        placeholder="可选，正文链接正则；留空时点“测试链接”自动填充"
                        @update:value="markDirty(idx)"
                      />
                      <template #feedback>只抓取匹配该正则的链接；留空则抓取全部发现链接</template>
                    </n-form-item>
                    <n-form-item label="抓取模式">
                      <n-select v-model:value="s.crawl_mode" :options="crawlModeOptions" style="width: 320px" @update:value="markDirty(idx)" />
                    </n-form-item>
                    <n-form-item label="最近 N 天">
                      <n-input-number v-model:value="s.max_age_days" :min="1" clearable style="width: 120px" @update:value="markDirty(idx)" />
                      <template #feedback>留空 = 不限；只抓取发布时间在 N 天以内的通知</template>
                    </n-form-item>
                    <n-form-item label="最大页数">
                      <n-input-number v-model:value="s.max_pages" :min="1" style="width: 120px" @update:value="markDirty(idx)" />
                    </n-form-item>
                    <n-form-item label="抓取正文">
                      <n-switch v-model:value="s.fetch_detail" @update:value="markDirty(idx)" />
                      <span class="field-hint">关闭后仅入库标题与链接（节省流量）</span>
                    </n-form-item>
                    <n-form-item label="深度检查">
                      <n-switch v-model:value="s.deep_check" @update:value="markDirty(idx)" />
                      <span class="field-hint">增量模式下周期重抓详情页比对内容变更</span>
                    </n-form-item>
                    <n-form-item label=" ">
                      <div class="mine-actions-row">
                        <n-button size="small" secondary :loading="testBusyMine[idx]" @click="testMine(idx, s.list_url)">
                          测试链接
                        </n-button>
                        <n-button size="small" secondary :disabled="!s.list_url" @click="openPreviewUrl(s.name, s.list_url)">
                          <template #icon><n-icon><EyeOutline /></n-icon></template>
                          预览样例
                        </n-button>
                      </div>
                    </n-form-item>
                  </n-form>
                </div>
              </n-collapse-transition>
            </n-card>
          </div>
        </template>
        <n-empty v-else-if="!myLoading" class="empty-box">
          <template #description>
            <div class="mine-empty">
              <div>还没有「我的数据源」</div>
              <div class="mine-empty-sub">去公共数据源库一键选用，或点击右上角「添加数据源」手动创建</div>
            </div>
          </template>
          <template #extra>
            <n-button type="primary" size="small" @click="activeView = 'catalog'">
              <template #icon><n-icon><LibraryOutline /></n-icon></template>
              去公共数据源库选用
            </n-button>
          </template>
        </n-empty>
      </n-spin>
    </div>
  </div>

  <!-- 预览弹窗 -->
  <n-modal
    v-model:show="previewOpen"
    preset="card"
    class="preview-modal"
    :title="previewTitle"
    :style="{ width: 'min(640px, 92vw)' }"
  >
    <template v-if="previewItem">
      <div class="preview-meta">
        <n-tag v-for="t in previewItem.tags ?? []" :key="t" size="small" :bordered="false" round>{{ t }}</n-tag>
      </div>
      <div class="preview-desc">{{ previewItem.description }}</div>
    </template>
    <n-input
      :value="previewUrl"
      readonly
      size="small"
      class="preview-url"
      @click="copyUrl"
    />
    <n-divider style="margin: 12px 0" />
    <div class="preview-title">样例数据（抓取自列表页，仅预览不落库）</div>
    <n-spin :show="previewLoading">
      <n-alert v-if="previewError" type="warning" :show-icon="false" class="preview-error">
        {{ previewError }}
      </n-alert>
      <n-list v-else-if="previewItems.length" bordered class="preview-list">
        <n-list-item v-for="(s, i) in previewItems" :key="i">
          <div class="sample-row">
            <span class="sample-idx">{{ i + 1 }}</span>
            <a :href="s.url" target="_blank" rel="noopener" class="sample-title">{{ s.title }}</a>
            <span class="sample-date">{{ s.date ? s.date.slice(0, 10) : '' }}</span>
          </div>
        </n-list-item>
      </n-list>
      <n-empty v-else-if="!previewLoading" description="暂未解析到样例数据" :size="'small'" />
    </n-spin>
    <template #footer>
      <div class="preview-footer">
        <n-button @click="previewOpen = false">关闭</n-button>
        <n-button
          v-if="previewItem && !previewItem.adopted"
          type="primary"
          :loading="actingId === previewItem.id"
          @click="onAdopt(previewItem)"
        >
          <template #icon><n-icon><AddOutline /></n-icon></template>
          选用该数据源
        </n-button>
        <n-tag v-else-if="previewItem" :bordered="false" type="success">已在我的数据源中</n-tag>
      </div>
    </template>
  </n-modal>
</template>

<script setup lang="ts">
import { computed, h, onMounted, ref, watch } from 'vue'
import { NButton, useDialog, useMessage, useNotification } from 'naive-ui'
import {
  AddOutline,
  CheckmarkCircleOutline,
  ChevronDownOutline,
  ChevronUpOutline,
  EyeOutline,
  LibraryOutline,
  SchoolOutline,
  SearchOutline,
} from '@vicons/ionicons5'
import { useSourceCenterStore } from '../stores/useSourceCenterStore'
import { useConfigStore } from '../stores/useConfigStore'
import type { SourceCenterItem, SourceCenterPreviewItem, SourceConfig } from '../api/schema'

const message = useMessage()
const notification = useNotification()
const dialog = useDialog()
const store = useSourceCenterStore()
const cfg = useConfigStore()

// ---------- 双视图切换 ----------
const activeView = ref<'catalog' | 'mine'>('catalog')

const previewOpen = ref(false)
const previewItem = ref<SourceCenterItem | null>(null)
const previewUrlName = ref('')
const previewUrlValue = ref('')
const previewLoading = ref(false)
const previewError = ref('')
const previewItems = ref<SourceCenterPreviewItem[]>([])
const actingId = ref<string | null>(null)

// 弹窗标题 / 展示 URL：公共库预览用目录条目，我的数据源预览用卡片 URL
const previewTitle = computed(() =>
  previewItem.value ? `${previewItem.value.org}-${previewItem.value.name}` : previewUrlName.value || '数据源预览',
)
const previewUrl = computed(() => previewItem.value?.list_url ?? previewUrlValue.value)

// 每页条数（3 列 × 4 行），多余栏目进下一页
const PAGE_SIZE = 12

// 分页状态
const page = ref(1)
const pageCount = computed(() => Math.max(1, Math.ceil(store.filtered.length / PAGE_SIZE)))
const pagedItems = computed(() =>
  store.filtered.slice((page.value - 1) * PAGE_SIZE, page.value * PAGE_SIZE),
)

// 筛选条件变化 → 回到第一页；页码越界自动钳制
watch(
  () => [store.keyword, store.tags, store.orgKey],
  () => {
    page.value = 1
  },
)
watch(pageCount, (pc) => {
  if (page.value > pc) page.value = pc
})

// 组织树展开状态：一级分组 / 二级组织 各自独立
const expandedGroups = ref<Set<string>>(new Set())
const expandedOrgs = ref<Set<string>>(new Set())

const tagOptions = computed(() => store.allTags.map((t) => ({ label: t, value: t })))

const orgOptions = computed(() => {
  const opts: { label: string; value: string }[] = []
  for (const g of store.overview.tree ?? []) {
    for (const o of g.children ?? []) opts.push({ label: `${g.label} / ${o.label}`, value: o.key })
  }
  return opts
})

function selectOrg(key: string | null) {
  store.orgKey = key
}

function toggleExpanded<T>(set: Set<T>, key: T): Set<T> {
  const next = new Set(set)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  return next
}

function toggleGroup(key: string) {
  expandedGroups.value = toggleExpanded(expandedGroups.value, key)
}

function toggleOrg(key: string) {
  expandedOrgs.value = toggleExpanded(expandedOrgs.value, key)
}

async function openPreview(it: SourceCenterItem) {
  previewItem.value = it
  previewUrlName.value = ''
  previewUrlValue.value = ''
  previewOpen.value = true
  previewItems.value = []
  previewError.value = ''
  previewLoading.value = true
  try {
    const res = await store.preview(it.id, 10)
    if (res.ok) {
      previewItems.value = res.items ?? []
    } else {
      previewError.value = res.error || '预览失败'
    }
  } catch (e) {
    previewError.value = e instanceof Error ? e.message : String(e)
  } finally {
    previewLoading.value = false
  }
}

async function openPreviewUrl(name: string, url: string) {
  const u = (url || '').trim()
  if (!u) {
    message.warning('请先填写列表地址')
    return
  }
  previewItem.value = null
  previewUrlName.value = name
  previewUrlValue.value = u
  previewOpen.value = true
  previewItems.value = []
  previewError.value = ''
  previewLoading.value = true
  try {
    const res = await store.previewByUrl(u, 10)
    if (res.ok) {
      previewItems.value = res.items ?? []
    } else {
      previewError.value = res.error || '预览失败'
    }
  } catch (e) {
    previewError.value = e instanceof Error ? e.message : String(e)
  } finally {
    previewLoading.value = false
  }
}

async function onAdopt(it: SourceCenterItem) {
  if (actingId.value) return
  actingId.value = it.id
  try {
    const res = await store.adopt(it.id)
    if (res.ok) {
      if (res.already) {
        message.success('该数据源已在「我的数据源」中')
      } else {
        notification.success({
          title: '选用成功',
          content: `「${it.org}-${it.name}」已加入我的数据源，配置已写入并立即生效`,
          duration: 5000,
          action: () =>
            h(
              NButton,
              { size: 'small', type: 'primary', onClick: () => { notification.destroyAll(); switchToMine() } },
              { default: () => '去调整参数' },
            ),
        })
      }
    } else {
      message.error(res.error || '选用失败')
    }
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  } finally {
    actingId.value = null
  }
}

function onRemove(it: SourceCenterItem) {
  dialog.warning({
    title: '移除数据源',
    content: `确定从「我的数据源」中移除「${it.org}-${it.name}」吗？目录中的条目不受影响，可随时重新选用。`,
    positiveText: '移除',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await store.remove(it.id)
        message.success('已移除，可在数据源中心重新选用')
      } catch (e) {
        message.error(e instanceof Error ? e.message : String(e))
      }
    },
  })
}

async function copyUrl() {
  const u = previewUrl.value
  if (!u) return
  try {
    await navigator.clipboard.writeText(u)
    message.success('链接已复制')
  } catch {
    message.info(u)
  }
}

// ---------- 我的数据源（改完即存） ----------

const mySources = ref<SourceConfig[]>([])
const myLoading = ref(false)
const mineLoaded = ref(false)
const expandedMine = ref<Set<number>>(new Set())
const invalidIdx = ref<Set<number>>(new Set())
const pendingIdxSet = ref<Set<number>>(new Set())
const lastSavedAt = ref<Record<number, string>>({})
const saveBusy = ref(false)
const savingIdx = ref<number | null>(null)
const testBusyMine = ref<Record<number, boolean>>({})

let saveTimer: ReturnType<typeof setTimeout> | undefined
let pendingResave = false

const crawlModeOptions = [
  { label: '增量（自动早停）', value: 'incremental' },
  { label: '全量（翻页+变更检测）', value: 'full' },
  { label: '仅列表（不抓详情）', value: 'list_only' },
]

// list_url → 公共库目录条目（用于「来自公共库」标记与跳转）
const fromCatalogMap = computed<Map<string, SourceCenterItem>>(() => {
  const m = new Map<string, SourceCenterItem>()
  for (const it of store.overview.items ?? []) m.set(it.list_url, it)
  return m
})

const hasPending = computed(() => pendingIdxSet.value.size > 0)

function normalizeUrl(url: string): string {
  const u = (url || '').trim()
  if (!u) return u
  return /^https?:\/\//i.test(u) ? u : `https://${u}`
}

async function loadMySources() {
  if (mineLoaded.value) return
  myLoading.value = true
  try {
    await cfg.fetchSources()
    mySources.value = (cfg.sources?.sources ?? []).map((s) => ({ ...s }))
    mineLoaded.value = true
    // 默认收起：卡片仅展示头部摘要，点击标题/箭头展开编辑
    expandedMine.value = new Set<number>()
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  } finally {
    myLoading.value = false
  }
}

function toggleMine(idx: number) {
  const next = new Set(expandedMine.value)
  if (next.has(idx)) next.delete(idx)
  else next.add(idx)
  expandedMine.value = next
}

function markDirty(idx: number) {
  invalidIdx.value = new Set(invalidIdx.value)
  invalidIdx.value.delete(idx)
  pendingIdxSet.value = new Set(pendingIdxSet.value)
  pendingIdxSet.value.add(idx)
  const saved = { ...lastSavedAt.value }
  delete saved[idx]
  lastSavedAt.value = saved
  scheduleSave()
}

function validateMine(): boolean {
  const bad: number[] = []
  mySources.value.forEach((s, i) => {
    if (!(s.name || '').trim() || !(s.list_url || '').trim()) bad.push(i)
  })
  invalidIdx.value = new Set(bad)
  return bad.length === 0
}

function scheduleSave() {
  if (!validateMine()) return
  if (saveTimer) clearTimeout(saveTimer)
  saveTimer = setTimeout(flushSave, 800)
}

async function flushSave() {
  if (saveBusy.value) {
    pendingResave = true
    return
  }
  if (!validateMine()) return
  if (saveTimer) {
    clearTimeout(saveTimer)
    saveTimer = undefined
  }
  saveBusy.value = true
  savingIdx.value = mySources.value.findIndex((_, i) => pendingIdxSet.value.has(i))
  try {
    const res = await cfg.updateSources(mySources.value)
    if (res.ok) {
      const now = new Date().toTimeString().slice(0, 8)
      const m: Record<number, string> = {}
      mySources.value.forEach((_, i) => {
        m[i] = now
      })
      lastSavedAt.value = m
      pendingIdxSet.value = new Set()
      invalidIdx.value = new Set()
    } else {
      message.error(res.error || '保存失败')
      await rollbackMine()
    }
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
    await rollbackMine()
  } finally {
    saveBusy.value = false
    savingIdx.value = null
    if (pendingResave) {
      pendingResave = false
      scheduleSave()
    }
  }
}

async function rollbackMine() {
  // 保存失败：以服务端配置为准回滚本地编辑，避免静默漂移
  try {
    await cfg.fetchSources()
    mySources.value = (cfg.sources?.sources ?? []).map((s) => ({ ...s }))
  } catch {
    /* 回滚失败则保留本地编辑，下次保存再试 */
  }
  pendingIdxSet.value = new Set()
  invalidIdx.value = new Set()
  lastSavedAt.value = {}
}

function addSource() {
  mySources.value.push({
    name: '',
    type: 'web',
    list_url: '',
    url_pattern: null,
    max_pages: 5,
    enabled: true,
    crawl_mode: 'incremental',
    max_age_days: null,
    fetch_detail: true,
    deep_check: false,
  })
  const idx = mySources.value.length - 1
  expandedMine.value = new Set([...expandedMine.value, idx])
}

function removeMine(idx: number) {
  const s = mySources.value[idx]
  dialog.warning({
    title: '删除数据源',
    content: `确定从「我的数据源」中删除「${s.name || '未命名'}」吗？已抓取入库的通知不会受影响，删除立即生效。`,
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: async () => {
      mySources.value.splice(idx, 1)
      // 删除后索引整体前移，重建本地状态
      const re = (m: Record<number, unknown>) => {
        const next: Record<number, unknown> = {}
        for (const k of Object.keys(m)) {
          const n = Number(k)
          if (n < idx) next[n] = m[n]
          else if (n > idx) next[n - 1] = m[n]
        }
        return next
      }
      pendingIdxSet.value = new Set([...pendingIdxSet.value].filter((i) => i !== idx).map((i) => (i > idx ? i - 1 : i)))
      lastSavedAt.value = re(lastSavedAt.value) as Record<number, string>
      invalidIdx.value = new Set([...invalidIdx.value].filter((i) => i !== idx).map((i) => (i > idx ? i - 1 : i)))
      expandedMine.value = new Set([...expandedMine.value].filter((i) => i !== idx).map((i) => (i > idx ? i - 1 : i)))
      testBusyMine.value = re(testBusyMine.value) as Record<number, boolean>
      await flushSave()
    },
  })
}

async function testMine(idx: number, url: string) {
  if (!(url || '').trim()) {
    message.warning('请先填写列表地址')
    return
  }
  testBusyMine.value = { ...testBusyMine.value, [idx]: true }
  try {
    const res = await cfg.testSource({ url, timeout: 15 })
    if (res.ok) {
      message.success(`链接可达（${res.latency_ms}ms，发现 ${res.link_count} 条链接）`)
      const s = mySources.value[idx]
      if (res.suggested_pattern && !s.url_pattern) {
        s.url_pattern = res.suggested_pattern
        message.info(`已根据页面自动填充 URL 模式：${res.suggested_pattern}`)
        markDirty(idx)
      }
    } else {
      message.error(res.error || '链接测试失败')
    }
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  } finally {
    testBusyMine.value = { ...testBusyMine.value, [idx]: false }
  }
}

function switchToMine() {
  activeView.value = 'mine'
  loadMySources()
}

function jumpToCatalog(listUrl: string) {
  const it = fromCatalogMap.value.get(listUrl)
  activeView.value = 'catalog'
  if (it) store.orgKey = `item:${it.id}`
}

watch(activeView, (v) => {
  if (v === 'mine') {
    loadMySources()
  } else {
    // 切回公共库时刷新 adopted 状态（我的数据源可能刚增删）
    store.fetchOverview().catch(() => {})
  }
})

onMounted(async () => {
  await store.fetchOverview().catch(() => {})
  expandedGroups.value = new Set((store.overview.tree ?? []).map((g) => g.key))
})
</script>

<style scoped>
/* 页面固定高度：视口 - 顶栏(60) - content padding(24+48)，超出部分交给分页/内部滚动 */
.page-root {
  display: flex;
  flex-direction: column;
  gap: 14px;
  height: calc(100vh - 140px);
  min-height: 460px;
}

.section-title-wrap {
  display: flex;
  align-items: center;
  gap: 8px;
}
.section-title-text {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-1);
}
.section-title-sub {
  font-size: 12px;
  color: var(--text-3);
  font-weight: 400;
  margin-left: 4px;
}

.filter-card {
  flex-shrink: 0;
}
.view-tabs {
  margin-top: 2px;
}
.stat-line {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
}
.filter-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
  margin-top: 10px;
}
.filter-search {
  width: 340px;
  max-width: 100%;
}
.filter-tags {
  width: 220px;
}
.filter-org-mobile {
  display: none;
  width: 200px;
}

.center-layout {
  display: flex;
  gap: 14px;
  align-items: stretch;
  flex: 1;
  min-height: 0;
}
.org-card {
  width: 250px;
  flex-shrink: 0;
  overflow: auto;
}
.org-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-1);
}

/* ---- 三级组织树（点击即筛选，展开独立控制） ---- */
.org-tree {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: 13px;
}
.tree-node {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  border-radius: var(--radius-md);
  color: var(--text-2);
  cursor: pointer;
  user-select: none;
  transition: background 0.15s, color 0.15s;
}
.tree-node:hover {
  background: var(--bg-soft);
}
.tree-node.active {
  background: var(--primary-soft);
  color: var(--primary);
  font-weight: 600;
}
.tree-node.root {
  font-weight: 600;
  color: var(--text-1);
}
.tree-node.group {
  margin-top: 4px;
  font-weight: 600;
  color: var(--text-1);
}
.tree-node.org {
  padding-left: 22px;
}
.node-caret {
  width: 14px;
  flex-shrink: 0;
  font-size: 10px;
  color: var(--text-3);
  text-align: center;
}
.tree-node.leaf {
  padding-left: 44px;
}
.node-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.node-count {
  font-size: 11px;
  color: var(--text-3);
  background: var(--bg-soft);
  border-radius: 8px;
  padding: 0 6px;
  line-height: 16px;
  flex-shrink: 0;
}
.tree-node.active .node-count {
  background: var(--primary-soft);
  color: var(--primary);
}

.cards-col {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.card-grid {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 12px;
  align-content: start;
  padding: 2px;
}
.source-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  transition: box-shadow 0.15s, transform 0.15s;
}
.source-card:hover {
  box-shadow: var(--shadow-2);
  transform: translateY(-1px);
}
.card-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  min-width: 0;
}
.card-name {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
  flex: 1;
}
.card-org {
  font-size: 12px;
  color: var(--text-3);
  flex-shrink: 0;
  max-width: 40%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.card-updated {
  font-size: 11px;
  color: var(--text-3);
}
.card-desc {
  font-size: 12px;
  color: var(--text-2);
  line-height: 1.55;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  min-height: 37px;
}
.card-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.tag-chip {
  font-size: 11px;
}
.card-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 6px;
  border-top: 1px dashed var(--border);
  padding-top: 8px;
}

.empty-box {
  padding: 60px 0;
}

.pager {
  flex-shrink: 0;
  padding-top: 10px;
  display: flex;
  justify-content: center;
}

/* ---- 我的数据源 ---- */
.mine-layout {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.mine-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-shrink: 0;
}
.mine-toolbar-left {
  min-width: 0;
}
.mine-tip {
  font-size: 12px;
  color: var(--text-3);
}
.mine-spin {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}
.mine-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.mine-card {
  border: 1px solid var(--border);
  transition: border-color 0.15s;
}
.mine-card-invalid {
  border-color: var(--error-color, #d03050);
}
.mine-card-head {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  cursor: pointer;
  user-select: none;
}
.header-index {
  font-size: 11px;
  color: var(--text-3);
  flex-shrink: 0;
}
.mine-card-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
  flex: 1;
}
.mine-from-catalog {
  cursor: pointer;
  flex-shrink: 0;
}
.mine-save-state {
  font-size: 11px;
  color: var(--text-3);
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}
.mine-save-state.ok {
  color: var(--success-color, #18a058);
}
.mine-save-state.pending {
  color: var(--warning-color, #f0a020);
}
.header-spacer {
  flex: 1;
}
.mine-card-summary {
  margin-top: 6px;
  font-size: 11px;
  color: var(--text-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  display: flex;
  align-items: center;
  gap: 4px;
}
.mine-card-summary.clickable {
  color: var(--primary);
  cursor: pointer;
}
.mine-card-summary.clickable:hover {
  text-decoration: underline;
}
.summary-icon {
  flex-shrink: 0;
}
.mine-actions-row {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.mine-card-body {
  padding-top: 12px;
  border-top: 1px dashed var(--border);
  margin-top: 8px;
}
.field-hint {
  margin-left: 8px;
  color: var(--text-3);
  font-size: 12px;
}
.input-suffix-link {
  color: var(--primary);
  text-decoration: none;
  font-size: 13px;
}
.input-suffix-disabled {
  color: var(--text-4, #c2c2c2);
  font-size: 13px;
}
.mine-empty-sub {
  font-size: 12px;
  color: var(--text-3);
  margin-top: 4px;
}

.preview-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.preview-desc {
  font-size: 13px;
  color: var(--text-2);
  margin: 8px 0;
  line-height: 1.6;
}
.preview-url {
  cursor: copy;
}
.preview-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-1);
  margin-bottom: 8px;
}
.preview-error {
  margin: 4px 0;
}
.preview-list {
  max-height: 320px;
  overflow: auto;
}
.sample-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.sample-idx {
  width: 18px;
  height: 18px;
  border-radius: 5px;
  background: var(--bg-soft);
  color: var(--text-3);
  font-size: 11px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.sample-title {
  font-size: 13px;
  color: var(--text-1);
  text-decoration: none;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
  min-width: 0;
}
.sample-title:hover {
  color: var(--primary);
}
.sample-date {
  font-size: 11px;
  color: var(--text-3);
  flex-shrink: 0;
}
.preview-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 8px;
}

/* 响应式：960 以下树收起为下拉 */
@media (max-width: 960px) {
  .page-root {
    height: auto;
    min-height: 0;
  }
  .center-layout {
    flex-direction: column;
  }
  .org-card {
    display: none;
  }
  .cards-col {
    min-height: 60vh;
  }
  .filter-org-mobile {
    display: inline-flex;
  }
  .filter-search {
    width: 100%;
  }
}
@media (max-width: 720px) {
  .mine-card-body :deep(.n-form-item) {
    display: block;
  }
  .mine-card-body :deep(.n-form-item-label) {
    margin-bottom: 4px;
  }
}
</style>
