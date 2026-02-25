/**
 * 租户会话 Store
 *
 * 管理 tenant_id 的生成和持久化（sessionStorage）。
 */
import { create } from 'zustand'

interface SessionState {
  tenantId: string
  initTenantId: () => void
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

  initTenantId: () => {
    try {
      let tid = sessionStorage.getItem('agent_tenant_id') || ''
      if (!tid) {
        tid = generateId()
        sessionStorage.setItem('agent_tenant_id', tid)
      }
      set({ tenantId: tid })
    } catch {
      set({ tenantId: generateId() })
    }
  },
}))
