/**
 * UI 状态 Store
 *
 * 管理面板显示/隐藏等 UI 状态。
 */
import { create } from 'zustand'

interface UIState {
  sidebarOpen: boolean
  statusPanelOpen: boolean
  toggleSidebar: () => void
  toggleStatusPanel: () => void
}

export const useUIStore = create<UIState>((set) => ({
  sidebarOpen: true,
  statusPanelOpen: false,
  toggleSidebar: () => set((s) => ({ sidebarOpen: !s.sidebarOpen })),
  toggleStatusPanel: () => set((s) => ({ statusPanelOpen: !s.statusPanelOpen })),
}))
