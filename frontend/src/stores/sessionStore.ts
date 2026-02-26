/**
 * 租户会话 Store
 *
 * 管理 tenant_id 的生成和持久化（sessionStorage）。
 */
import { create } from 'zustand'

interface SessionState {
  tenantId: string
  token: string | null
  user: { id: string; username: string } | null
  initTenantId: () => void
  setToken: (token: string, user: { id: string; username: string }) => void
  logout: () => void
}

function generateId(): string {
  if (typeof globalThis.crypto?.randomUUID === 'function') {
    return crypto.randomUUID().replace(/-/g, '')
  }
  const bytes = new Uint8Array(16)
  crypto.getRandomValues(bytes)
  return Array.from(bytes)
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')
}

export const useSessionStore = create<SessionState>((set) => ({
  tenantId: '',
  token: typeof localStorage !== 'undefined' ? localStorage.getItem('agent_token') : null,
  user: typeof localStorage !== 'undefined' ? JSON.parse(localStorage.getItem('agent_user') || 'null') : null,

  initTenantId: () => {
    // 优先使用 Token 中的 user_id 作为 tenant_id
    const token = localStorage.getItem('agent_token')
    const userStr = localStorage.getItem('agent_user')
    const user = userStr ? JSON.parse(userStr) : null

    if (token && user?.id) {
      set({ tenantId: user.id, token, user })
      return
    }

    // 访客模式：回退到 sessionStorage 中的临时 ID
    try {
      let tid = sessionStorage.getItem('agent_tenant_id') || ''
      if (!tid) {
        tid = generateId()
        sessionStorage.setItem('agent_tenant_id', tid)
      }
      set({ tenantId: tid, token: null, user: null })
    } catch {
      // 容错处理（如隐身模式禁用 sessionStorage）
      set({ tenantId: generateId(), token: null, user: null })
    }
  },

  setToken: (token, user) => {
    localStorage.setItem('agent_token', token)
    localStorage.setItem('agent_user', JSON.stringify(user))
    set({ token, user, tenantId: user.id })
    // 清除访客 ID，避免混淆
    sessionStorage.removeItem('agent_tenant_id')
  },

  logout: () => {
    localStorage.removeItem('agent_token')
    localStorage.removeItem('agent_user')
    // 登出后自动生成新的访客 ID
    const guestId = generateId()
    sessionStorage.setItem('agent_tenant_id', guestId)
    set({ token: null, user: null, tenantId: guestId })
  },
}))
