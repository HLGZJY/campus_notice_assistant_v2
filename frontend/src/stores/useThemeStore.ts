import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { darkTheme } from 'naive-ui'

export type ThemeMode = 'light' | 'dark' | 'auto'

const STORAGE_KEY = 'campus-assistant-theme'

function systemPrefersDark(): boolean {
  if (typeof window === 'undefined' || !window.matchMedia) return false
  return window.matchMedia('(prefers-color-scheme: dark)').matches
}

function readStoredMode(): ThemeMode {
  try {
    const v = localStorage.getItem(STORAGE_KEY) as ThemeMode | null
    return v === 'light' || v === 'dark' || v === 'auto' ? v : 'auto'
  } catch {
    return 'auto'
  }
}

export const useThemeStore = defineStore('theme', () => {
  const mode = ref<ThemeMode>(readStoredMode())
  const systemDark = ref(systemPrefersDark())
  const isDark = computed(() => mode.value === 'dark' || (mode.value === 'auto' && systemDark.value))
  const naiveTheme = computed(() => (isDark.value ? darkTheme : null))

  function applyHtmlFlag() {
    document.documentElement.classList.toggle('dark', isDark.value)
    document.documentElement.style.colorScheme = isDark.value ? 'dark' : 'light'
  }

  function listen() {
    applyHtmlFlag()
    if (!window.matchMedia) return
    const mql = window.matchMedia('(prefers-color-scheme: dark)')
    const handler = (e: MediaQueryListEvent) => {
      systemDark.value = e.matches
      applyHtmlFlag()
    }
    if (mql.addEventListener) mql.addEventListener('change', handler)
    else mql.addListener(handler)
  }

  function setMode(m: ThemeMode) {
    mode.value = m
    try {
      localStorage.setItem(STORAGE_KEY, m)
    } catch {
      /* ignore */
    }
    applyHtmlFlag()
  }

  function toggle() {
    setMode(isDark.value ? 'light' : 'dark')
  }

  return { mode, isDark, naiveTheme, setMode, toggle, listen, applyHtmlFlag }
})