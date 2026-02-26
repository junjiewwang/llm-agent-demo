/**
 * UI 状态 Store
 *
 * 管理面板显示/隐藏等 UI 状态。
 */
import { create } from 'zustand'

interface UIState {
  sidebarOpen: boolean
  sidebarCollapsed: boolean
  statusPanelOpen: boolean
  toggleSidebar: () => void
  toggleSidebarCollapse: () => void
  toggleStatusPanel: () => void
}

export const useUIStore = create<UIState>((set) => ({
  sidebarOpen: true,
  sidebarCollapsed: false,
  statusPanelOpen: false,
  toggleSidebar: () => set((s) => ({ sidebarOpen: !s.sidebarOpen })),
  toggleSidebarCollapse: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
  toggleStatusPanel: () => set((s) => ({ statusPanelOpen: !s.statusPanelOpen })),
}))
