/**
 * UI 状态 Store
 *
 * 管理面板显示/隐藏、暗色模式等 UI 状态。
 */
import { create } from 'zustand'

/** 检测初始暗色模式偏好 */
function getInitialDarkMode(): boolean {
  const stored = localStorage.getItem('darkMode')
  if (stored !== null) return stored === 'true'
  return window.matchMedia('(prefers-color-scheme: dark)').matches
}

/** 同步 DOM 上的 dark class */
function applyDarkMode(dark: boolean) {
  document.documentElement.classList.toggle('dark', dark)
  localStorage.setItem('darkMode', String(dark))
}

interface UIState {
  sidebarOpen: boolean
  sidebarCollapsed: boolean
  statusPanelOpen: boolean
  darkMode: boolean
  toggleSidebar: () => void
  toggleSidebarCollapse: () => void
  toggleStatusPanel: () => void
  toggleDarkMode: () => void
}

// 初始化时立即应用
const initialDark = getInitialDarkMode()
applyDarkMode(initialDark)

export const useUIStore = create<UIState>((set) => ({
  sidebarOpen: true,
  sidebarCollapsed: false,
  statusPanelOpen: false,
  darkMode: initialDark,
  toggleSidebar: () => set((s) => ({ sidebarOpen: !s.sidebarOpen })),
  toggleSidebarCollapse: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
  toggleStatusPanel: () => set((s) => ({ statusPanelOpen: !s.statusPanelOpen })),
  toggleDarkMode: () => set((s) => {
    const next = !s.darkMode
    applyDarkMode(next)
    return { darkMode: next }
  }),
}))
