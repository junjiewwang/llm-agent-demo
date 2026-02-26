/** SSE 事件类型 */
export type SSEEventType =
  | 'thinking'
  | 'tool_call'
  | 'tool_confirm'
  | 'tool_result'
  | 'answering'
  | 'done'
  | 'error'
  | 'max_iterations'
  // 预留扩展
  | 'answer_token'
  | 'plan'

/** 思考事件 */
export interface ThinkingEvent {
  type: 'thinking'
  iteration: number
  max_iterations: number
}

/** 工具调用事件 */
export interface ToolCallEvent {
  type: 'tool_call'
  tool_name: string
  tool_args: Record<string, unknown>
  parallel_total: number
  parallel_index: number
}

/** 工具结果事件 */
export interface ToolResultEvent {
  type: 'tool_result'
  tool_name: string
  success: boolean
  duration_ms: number
  tool_result_preview: string
  parallel_total: number
  parallel_index: number
}

/** 回答中事件 */
export interface AnsweringEvent {
  type: 'answering'
}

/** 最大迭代事件 */
export interface MaxIterationsEvent {
  type: 'max_iterations'
  message: string
}

/** 错误事件 */
export interface ErrorEvent {
  type: 'error'
  message: string
}

/** 工具确认事件 */
export interface ToolConfirmEvent {
  type: 'tool_confirm'
  confirm_id: string
  tool_name: string
  tool_args: Record<string, unknown>
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

/** 完成事件 */
export interface DoneEvent {
  type: 'done'
  content: string
  stopped: boolean
  chat_history: ChatMessage[]
  conversations: ConversationInfo[]
  status: StatusInfo
  usage?: MessageUsage
}

/** 聊天过程中的 SSE 事件联合类型 */
export type AgentEvent =
  | ThinkingEvent
  | ToolCallEvent
  | ToolResultEvent
  | ToolConfirmEvent
  | AnsweringEvent
  | MaxIterationsEvent
  | ErrorEvent

/** 聊天消息 */
export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  /** 关联的思考过程（仅 assistant 消息，聊天完成后快照） */
  thinkingNodes?: ThinkingNode[]
  /** 本次回答的 token 用量（仅 assistant 消息，聊天完成后填充） */
  usage?: MessageUsage
}

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

/** API 统一响应 */
export interface ApiResponse<T = unknown> {
  success: boolean
  data: T
  error?: string
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

/** 文件上传结果 */
export interface UploadData {
  results: Array<{
    file: string
    chunks: number
    error?: string
  }>
  total_chunks: number
  error?: string
}

/** ThinkingPanel 树节点（预留 children 用于 Plan-and-Execute） */
export interface ThinkingNode {
  id: string
  event: AgentEvent
  children?: ThinkingNode[]
}
