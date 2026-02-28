/**
 * 聊天 Store
 *
 * 管理当前对话的消息列表、思考过程事件流和流式状态。
 * Plan 模式下，子循环事件会嵌套到对应步骤节点的 children 中（树形结构）。
 */
import { create } from 'zustand'
import type { ChatMessage, ThinkingNode, StatusInfo, ToolConfirmEvent } from '../types'
import { chatSSE } from '../api/sse'
import { stopChat as apiStopChat, confirmTool as apiConfirmTool } from '../api/client'
import { syncDoneEvent } from './actions'

/** Plan 执行进度 */
export interface PlanProgress {
  totalSteps: number
  currentStep: number
  currentDescription: string
  completedSteps: number
  steps: { id: string; description: string; status: string }[]
}

/** Plan 模式下的子循环事件类型（会嵌套到步骤 children 中） */
const PLAN_CHILD_EVENT_TYPES = new Set([
  'thinking', 'tool_call', 'tool_confirm', 'tool_result',
  'answering', 'max_iterations', 'error',
])

interface ChatState {
  messages: ChatMessage[]
  thinkingNodes: ThinkingNode[]
  isStreaming: boolean
  streamingAnswer: string
  status: StatusInfo | null
  /** 当前等待用户确认的工具执行事件 */
  pendingConfirm: ToolConfirmEvent | null
  /** 临时状态提示消息（如上下文压缩进度） */
  statusMessage: string | null
  /** Plan 模式执行进度（null = 非 Plan 模式） */
  planProgress: PlanProgress | null

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
  statusMessage: null,
  planProgress: null,

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
      planProgress: null,
    }))

    abortController = chatSSE(tenantId, message, {
      onEvent: (event) => {
        // --- Plan 模式事件路由 ---

        // plan_created: 初始化 planProgress，创建顶层计划节点
        if (event.type === 'plan_created') {
          const planSteps = event.plan.steps.map((s) => ({
            id: s.id,
            description: s.description,
            status: s.status,
          }))
          set((s) => ({
            planProgress: {
              totalSteps: event.total_steps,
              currentStep: 0,
              currentDescription: '',
              completedSteps: 0,
              steps: planSteps,
            },
            thinkingNodes: [
              ...s.thinkingNodes,
              { id: `node-${++nodeCounter}`, event },
            ],
          }))
          return
        }

        // step_start: 更新进度，创建步骤节点（带空 children 数组，后续子循环事件挂载于此）
        if (event.type === 'step_start') {
          const stepNode: ThinkingNode = {
            id: `node-${++nodeCounter}`,
            event,
            children: [],
          }
          set((s) => ({
            planProgress: s.planProgress
              ? {
                  ...s.planProgress,
                  currentStep: event.step_index + 1,
                  currentDescription: event.message,
                  steps: s.planProgress.steps.map((step, i) =>
                    i === event.step_index ? { ...step, status: 'running' } : step,
                  ),
                }
              : null,
            thinkingNodes: [...s.thinkingNodes, stepNode],
          }))
          return
        }

        // step_done: 更新进度和步骤状态
        if (event.type === 'step_done') {
          set((s) => ({
            planProgress: s.planProgress
              ? {
                  ...s.planProgress,
                  completedSteps: s.planProgress.completedSteps + (event.step_status === 'completed' ? 1 : 0),
                  steps: s.planProgress.steps.map((step, i) =>
                    i === event.step_index ? { ...step, status: event.step_status } : step,
                  ),
                }
              : null,
            thinkingNodes: [
              ...s.thinkingNodes,
              { id: `node-${++nodeCounter}`, event },
            ],
          }))
          return
        }

        // replan: 更新进度（步骤列表会在下一次 plan_created 重建）
        if (event.type === 'replan') {
          set((s) => ({
            thinkingNodes: [
              ...s.thinkingNodes,
              { id: `node-${++nodeCounter}`, event },
            ],
          }))
          return
        }

        // --- Plan 模式下子循环事件嵌套到当前步骤的 children ---
        const currentPlanProgress = useChatStore.getState().planProgress
        if (currentPlanProgress && PLAN_CHILD_EVENT_TYPES.has(event.type)) {
          const childNode: ThinkingNode = {
            id: `node-${++nodeCounter}`,
            event,
          }
          set((s) => {
            // 找到最后一个 step_start 节点，将子事件挂到其 children
            const nodes = [...s.thinkingNodes]
            for (let i = nodes.length - 1; i >= 0; i--) {
              if (nodes[i].event.type === 'step_start' && nodes[i].children) {
                nodes[i] = {
                  ...nodes[i],
                  children: [...(nodes[i].children || []), childNode],
                }
                return { thinkingNodes: nodes }
              }
            }
            // 兜底：未找到步骤节点，直接追加到根层级
            return { thinkingNodes: [...nodes, childNode] }
          })

          // 确认事件仍需设置 pendingConfirm
          if (event.type === 'tool_confirm') {
            set({ pendingConfirm: event })
          }
          return
        }

        // --- 非 Plan 模式：保持原有扁平逻辑 ---
        const node: ThinkingNode = {
          id: `node-${++nodeCounter}`,
          event,
        }
        set((s) => ({ thinkingNodes: [...s.thinkingNodes, node] }))

        if (event.type === 'tool_confirm') {
          set({ pendingConfirm: event })
        }

        if (event.type === 'status') {
          set({ statusMessage: event.message })
        }
      },

      onDone: (data) => {
        // 将思考过程快照和 usage 附加到最后一条 assistant 消息
        const currentNodes = useChatStore.getState().thinkingNodes
        const history = [...data.chat_history]
        if (currentNodes.length > 0 || data.usage) {
          for (let i = history.length - 1; i >= 0; i--) {
            if (history[i].role === 'assistant') {
              history[i] = {
                ...history[i],
                ...(currentNodes.length > 0 ? { thinkingNodes: currentNodes } : {}),
                ...(data.usage ? { usage: data.usage } : {}),
              }
              break
            }
          }
        }
        set({ isStreaming: false, pendingConfirm: null, statusMessage: null, planProgress: null })
        syncDoneEvent(history, data.conversations, data.status)
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
          statusMessage: null,
          planProgress: null,
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
      statusMessage: null,
      planProgress: null,
    }))
  },

  clearThinking: () => set({ thinkingNodes: [], planProgress: null }),

  handleConfirm: async (confirmId, approved) => {
    try {
      await apiConfirmTool(confirmId, approved)
      set({ pendingConfirm: null })
    } catch (err) {
      console.error('确认请求失败:', err)
    }
  },
}))
