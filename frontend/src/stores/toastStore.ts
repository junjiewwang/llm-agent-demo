/**
 * 轻量 Toast 通知 Store
 *
 * 提供全局 toast 方法，零外部依赖。
 * 使用方式：
 *   import { toast } from '../stores/toastStore'
 *   toast.success('操作成功')
 *   toast.error('操作失败')
 */
import { create } from 'zustand'

export type ToastType = 'success' | 'error' | 'info'

export interface ToastItem {
  id: number
  type: ToastType
  message: string
}

interface ToastState {
  toasts: ToastItem[]
  add: (type: ToastType, message: string) => void
  remove: (id: number) => void
}

let nextId = 0

export const useToastStore = create<ToastState>((set) => ({
  toasts: [],

  add: (type, message) => {
    const id = ++nextId
    set((s) => ({ toasts: [...s.toasts, { id, type, message }] }))

    // 自动消失
    setTimeout(() => {
      set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }))
    }, 3000)
  },

  remove: (id) => {
    set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }))
  },
}))

/** 便捷调用 */
export const toast = {
  success: (msg: string) => useToastStore.getState().add('success', msg),
  error: (msg: string) => useToastStore.getState().add('error', msg),
  info: (msg: string) => useToastStore.getState().add('info', msg),
}
