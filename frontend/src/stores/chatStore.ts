/**
 * 聊天 Store
 *
 * 管理当前对话的消息列表、思考过程事件流和流式状态。
 */
import { create } from 'zustand'
import type { ChatMessage, ThinkingNode, StatusInfo, ToolConfirmEvent } from '../types'
import { chatSSE } from '../api/sse'
import { stopChat as apiStopChat, confirmTool as apiConfirmTool } from '../api/client'
import { useConversationStore } from './conversationStore'

interface ChatState {
  messages: ChatMessage[]
  thinkingNodes: ThinkingNode[]
  isStreaming: boolean
  streamingAnswer: string
  status: StatusInfo | null
  /** 当前等待用户确认的工具执行事件 */
  pendingConfirm: ToolConfirmEvent | null

  setMessages: (msgs: ChatMessage[]) => void
  setStatus: (status: StatusInfo) => void
  sendMessage: (tenantId: string, message: string) => void
  stopChat: (tenantId: string) => void
  clearThinking: () => void
  /** 处理用户对工具执行的确认决策 */
  handleConfirm: (confirmId: string, approved: boolean) => Promise<void>
}

let abortController: AbortController | null = null
let nodeCounter = 0

export const useChatStore = create<ChatState>((set) => ({
  messages: [],
  thinkingNodes: [],
  isStreaming: false,
  streamingAnswer: '',
  status: null,
  pendingConfirm: null,

  setMessages: (msgs) => set({ messages: msgs }),
  setStatus: (status) => set({ status }),

  sendMessage: (tenantId, message) => {
    const userMsg: ChatMessage = { role: 'user', content: message }
    set((s) => ({
      messages: [...s.messages, userMsg],
      thinkingNodes: [],
      isStreaming: true,
      streamingAnswer: '',
      pendingConfirm: null,
    }))

    abortController = chatSSE(tenantId, message, {
      onEvent: (event) => {
        const node: ThinkingNode = {
          id: `node-${++nodeCounter}`,
          event,
        }
        set((s) => ({ thinkingNodes: [...s.thinkingNodes, node] }))

        // 如果是确认事件，设置 pendingConfirm 状态
        if (event.type === 'tool_confirm') {
          set({ pendingConfirm: event })
        }
      },

      onDone: (data) => {
        // 将思考过程快照附加到最后一条 assistant 消息
        const currentNodes = useChatStore.getState().thinkingNodes
        const history = [...data.chat_history]
        if (currentNodes.length > 0) {
          for (let i = history.length - 1; i >= 0; i--) {
            if (history[i].role === 'assistant') {
              history[i] = { ...history[i], thinkingNodes: currentNodes }
              break
            }
          }
        }
        set({
          messages: history,
          isStreaming: false,
          status: data.status,
          pendingConfirm: null,
        })
        useConversationStore.getState().setConversations(data.conversations)
        abortController = null
      },

      onError: (error) => {
        const errorMsg: ChatMessage = {
          role: 'assistant',
          content: `❌ ${error}`,
        }
        set((s) => ({
          messages: [...s.messages, errorMsg],
          isStreaming: false,
          pendingConfirm: null,
        }))
        abortController = null
      },
    })
  },

  stopChat: async (tenantId) => {
    abortController?.abort()
    abortController = null
    try {
      await apiStopChat(tenantId)
    } catch {
      // 忽略停止请求失败
    }
    // 追加停止提示消息，触发 messages 变化 → ChatView 自动滚底
    set((s) => ({
      messages: [...s.messages, { role: 'assistant' as const, content: '[对话已停止]' }],
      isStreaming: false,
      streamingAnswer: '',
      pendingConfirm: null,
    }))
  },

  clearThinking: () => set({ thinkingNodes: [] }),

  handleConfirm: async (confirmId, approved) => {
    try {
      await apiConfirmTool(confirmId, approved)
      set({ pendingConfirm: null })
    } catch (err) {
      console.error('确认请求失败:', err)
    }
  },
}))
