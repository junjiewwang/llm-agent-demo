/**
 * 对话与系统状态相关类型
 */
import type { ChatMessage } from './chat'

/** 对话信息 */
export interface ConversationInfo {
  id: string
  title: string
  active: boolean
}

/** 系统状态 */
export interface StatusInfo {
  initialized: boolean
  model?: string
  current_conversation?: {
    id: string
    title: string
    memory_tokens: number
  }
  conversation_count: number
  long_term_memory_count: number
  knowledge_base_chunks: number
}

/** 会话恢复数据 */
export interface SessionData {
  chat_history: ChatMessage[]
  conversations: ConversationInfo[]
  status: StatusInfo
}

/** 新建对话返回 */
export interface NewConversationData {
  conversation: ConversationInfo
  conversations: ConversationInfo[]
  status: StatusInfo
}

/** 对话操作返回 */
export interface ConversationActionData {
  chat_history: ChatMessage[]
  conversations: ConversationInfo[]
  status: StatusInfo
}
