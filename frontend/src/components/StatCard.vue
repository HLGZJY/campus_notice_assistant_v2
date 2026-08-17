<script setup lang="ts">
import { InformationCircleOutline } from '@vicons/ionicons5'

defineProps<{
  icon: unknown
  label: string
  value?: number | string
  color?: 'primary' | 'success' | 'warning' | 'error' | 'info' | 'violet'
  hint?: string
}>()
</script>

<template>
  <n-card size="small" class="stat-card" :bordered="false">
    <div class="stat-inner">
      <div class="stat-icon" :class="`stat-icon--${color ?? 'primary'}`">
        <n-icon size="22"><component :is="icon" /></n-icon>
      </div>
      <div class="stat-meta">
        <div class="stat-label">
          {{ label }}
          <n-tooltip v-if="hint" trigger="hover" placement="top">
            <template #trigger>
              <span class="stat-hint"><n-icon size="14"><InformationCircleOutline /></n-icon></span>
            </template>
            {{ hint }}
          </n-tooltip>
        </div>
        <div class="stat-value">
          <slot name="value">{{ value ?? 0 }}</slot>
        </div>
      </div>
    </div>
  </n-card>
</template>

<style scoped>
.stat-card {
  box-shadow: var(--shadow-1);
  transition: transform 0.15s ease, box-shadow 0.15s ease;
  cursor: default;
}
.stat-card:hover {
  transform: translateY(-2px);
  box-shadow: var(--shadow-2);
}
.stat-inner {
  display: flex;
  align-items: center;
  gap: 14px;
}
.stat-icon {
  flex-shrink: 0;
  width: 46px;
  height: 46px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.stat-icon--primary {
  background: var(--primary-soft);
  color: var(--primary);
}
.stat-icon--violet {
  background: rgba(139, 92, 246, 0.12);
  color: var(--violet);
}
.stat-icon--success {
  background: var(--success-soft);
  color: var(--success);
}
.stat-icon--warning {
  background: var(--warning-soft);
  color: var(--warning);
}
.stat-icon--error {
  background: var(--error-soft);
  color: var(--error);
}
.stat-icon--info {
  background: var(--info-soft);
  color: var(--info);
}
.stat-label {
  font-size: 13px;
  color: var(--text-2);
  display: flex;
  align-items: center;
  white-space: nowrap;
}
.stat-value {
  font-size: 26px;
  font-weight: 700;
  color: var(--text-1);
  line-height: 1.2;
  font-variant-numeric: tabular-nums;
}
.stat-hint {
  color: var(--text-3);
  cursor: help;
  margin-left: 3px;
  font-size: 12px;
}
</style>