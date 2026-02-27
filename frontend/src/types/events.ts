/**
 * SSE 事件类型定义
 *
 * 包含所有 Agent 流式通信的事件类型及接口。
 */

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

/** 聊天过程中的 SSE 事件联合类型 */
export type AgentEvent =
  | ThinkingEvent
  | ToolCallEvent
  | ToolResultEvent
  | ToolConfirmEvent
  | AnsweringEvent
  | MaxIterationsEvent
  | ErrorEvent
