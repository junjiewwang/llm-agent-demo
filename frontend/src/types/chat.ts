/**
 * 聊天与消息相关类型
 */
import type { AgentEvent } from './events'

/** ThinkingPanel 树节点（预留 children 用于 Plan-and-Execute） */
export interface ThinkingNode {
  id: string
  event: AgentEvent
  children?: ThinkingNode[]
}

/** 消息级 token 用量 */
export interface MessageUsage {
  input_tokens: number
  output_tokens: number
  total_tokens: number
  llm_calls: number
  tool_calls: number
  duration_ms: number
}

/** 聊天消息 */
export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  /** 关联的思考过程（仅 assistant 消息，聊天完成后快照） */
  thinkingNodes?: ThinkingNode[]
  /** 本次回答的 token 用量（仅 assistant 消息，聊天完成后填充） */
  usage?: MessageUsage
}
