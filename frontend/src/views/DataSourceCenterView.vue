<template>
  <div class="page-root">
    <!-- 顶部：搜索 + 筛选 -->
    <n-card :bordered="false" class="filter-card">
      <template #header>
        <div class="section-title-wrap">
          <n-icon size="18" color="var(--primary)"><LibraryOutline /></n-icon>
          <span class="section-title-text">数据源中心</span>
          <span class="section-title-sub">公共数据源库 · 一键选用即加入「我的数据源」</span>
        </div>
      </template>
      <div class="filter-bar">
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
      <div class="stat-line">
        <n-tag :bordered="false" type="info" size="small">共 {{ (store.overview.items ?? []).length }} 个公共数据源</n-tag>
        <n-tag :bordered="false" type="success" size="small">已选用 {{ store.overview.adopted_count }} 个</n-tag>
        <n-tag :bordered="false" type="warning" size="small">当前筛选 {{ store.filtered.length }} 个</n-tag>
      </div>
    </n-card>

    <!-- 主体：左三级组织树 + 右卡片网格（固定高度 + 分页） -->
    <div class="center-layout">
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
            <!-- 一级：校级机构 / 教学科研单位 -->
            <div class="tree-node group" :class="{ active: store.orgKey === g.key }" @click="selectOrg(g.key)">
              <span class="node-caret" @click.stop="toggleGroup(g.key)">
                {{ expandedGroups.has(g.key) ? '▾' : '▸' }}
              </span>
              <span class="node-label">{{ g.label }}</span>
              <span class="node-count">{{ g.count }}</span>
            </div>
            <div v-if="expandedGroups.has(g.key)" class="tree-children">
              <template v-for="o in g.children ?? []" :key="o.key">
                <!-- 二级：学院 / 部门 -->
                <div class="tree-node org" :class="{ active: store.orgKey === o.key }" @click="selectOrg(o.key)">
                  <span class="node-caret" @click.stop="toggleOrg(o.key)">
                    {{ expandedOrgs.has(o.key) ? '▾' : '▸' }}
                  </span>
                  <span class="node-label">{{ o.label }}</span>
                  <span class="node-count">{{ o.count }}</span>
                </div>
                <div v-if="expandedOrgs.has(o.key)" class="tree-children">
                  <!-- 三级：具体栏目 -->
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
  </div>

  <!-- 预览弹窗 -->
  <n-modal
    v-model:show="previewOpen"
    preset="card"
    class="preview-modal"
    :title="previewItem ? `${previewItem.org}-${previewItem.name}` : '数据源预览'"
    :style="{ width: 'min(640px, 92vw)' }"
  >
    <template v-if="previewItem">
      <div class="preview-meta">
        <n-tag v-for="t in previewItem.tags ?? []" :key="t" size="small" :bordered="false" round>{{ t }}</n-tag>
      </div>
      <div class="preview-desc">{{ previewItem.description }}</div>
      <n-input
        :value="previewItem.list_url"
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
    </template>
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
        <n-tag v-else :bordered="false" type="success">已在我的数据源中</n-tag>
      </div>
    </template>
  </n-modal>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useDialog, useMessage } from 'naive-ui'
import {
  AddOutline,
  CheckmarkCircleOutline,
  EyeOutline,
  LibraryOutline,
  SchoolOutline,
  SearchOutline,
} from '@vicons/ionicons5'
import { useSourceCenterStore } from '../stores/useSourceCenterStore'
import type { SourceCenterItem, SourceCenterPreviewItem } from '../api/schema'

const message = useMessage()
const dialog = useDialog()
const store = useSourceCenterStore()

const previewOpen = ref(false)
const previewItem = ref<SourceCenterItem | null>(null)
const previewLoading = ref(false)
const previewError = ref('')
const previewItems = ref<SourceCenterPreviewItem[]>([])
const actingId = ref<string | null>(null)

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

function toggleExpanded(set: Set<string>, key: string): Set<string> {
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

async function onAdopt(it: SourceCenterItem) {
  if (actingId.value) return
  actingId.value = it.id
  try {
    const res = await store.adopt(it.id)
    if (res.ok) {
      message.success(res.already ? '该数据源已在「我的数据源」中' : `已选用「${it.org}-${it.name}」，可在系统配置-数据源中管理`)
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
  if (!previewItem.value) return
  try {
    await navigator.clipboard.writeText(previewItem.value.list_url)
    message.success('链接已复制')
  } catch {
    message.info(previewItem.value.list_url)
  }
}

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
.filter-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
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
.stat-line {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
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
</style>
