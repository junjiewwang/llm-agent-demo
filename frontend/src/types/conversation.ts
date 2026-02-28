/**
 * 对话与系统状态相关类型
 */
import type { ChatMessage } from './chat'

/** 对话信息 */
export interface ConversationInfo {
  id: string
  title: string
  active: boolean
  created_at?: number
}

/** Zone 级别 Token 分布 */
export interface ZoneBreakdown {
  system_tokens: number
  environment_tokens: number
  skill_tokens: number
  knowledge_tokens: number
  memory_tokens: number
  history_tokens: number
  input_budget: number
  skill_budget: number
  knowledge_budget: number
  memory_budget: number
  skill_truncated: boolean
  knowledge_truncated: boolean
  memory_truncated: boolean
}

/** 系统状态 */
export interface StatusInfo {
  initialized: boolean
  model?: string
  context_window: number
  max_output_tokens: number
  current_conversation?: {
    id: string
    title: string
    memory_tokens: number
    context_used_tokens: number
    history_budget: number
    compression_count: number
    zone_breakdown?: ZoneBreakdown
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
