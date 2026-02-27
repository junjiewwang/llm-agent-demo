/**
 * 对话管理 Store
 *
 * 管理对话列表和 CRUD 操作。
 */
import { create } from 'zustand'
import type { ConversationInfo } from '../types'
import {
  createConversation as apiCreate,
  activateConversation as apiActivate,
  deleteConversation as apiDelete,
} from '../api/client'
import { syncConversationData, syncNewConversation } from './actions'

interface ConversationState {
  conversations: ConversationInfo[]
  activeId: string | null

  setConversations: (convs: ConversationInfo[]) => void
  createConversation: (tenantId: string) => Promise<void>
  switchConversation: (tenantId: string, convId: string) => Promise<void>
  deleteConversation: (tenantId: string, convId: string) => Promise<void>
}

export const useConversationStore = create<ConversationState>((set) => ({
  conversations: [],
  activeId: null,

  setConversations: (convs) => {
    const active = convs.find((c) => c.active)
    set({ conversations: convs, activeId: active?.id ?? null })
  },

  createConversation: async (tenantId) => {
    const res = await apiCreate(tenantId)
    if (res.success && res.data) {
      syncNewConversation(res.data.conversations, res.data.status)
    }
  },

  switchConversation: async (tenantId, convId) => {
    const res = await apiActivate(tenantId, convId)
    if (res.success && res.data) {
      syncConversationData(res.data.chat_history, res.data.conversations, res.data.status)
    }
  },

  deleteConversation: async (tenantId, convId) => {
    const res = await apiDelete(tenantId, convId)
    if (res.success && res.data) {
      syncConversationData(res.data.chat_history, res.data.conversations, res.data.status)
    }
  },
}))
