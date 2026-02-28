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
  | 'status'
  | 'answer_token'
  | 'plan_created'
  | 'step_start'
  | 'step_done'
  | 'replan'

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

/** 状态提示事件（如上下文压缩进度） */
export interface StatusEvent {
  type: 'status'
  message: string
}

/** 计划步骤 */
export interface PlanStepData {
  id: string
  description: string
  status: 'pending' | 'running' | 'completed' | 'failed' | 'skipped'
  result_summary: string
  tool_hint: string | null
}

/** 计划数据 */
export interface PlanData {
  goal: string
  steps: PlanStepData[]
  current_step_index: number
  replan_count: number
}

/** 计划创建事件 */
export interface PlanCreatedEvent {
  type: 'plan_created'
  plan: PlanData
  total_steps: number
  message: string
}

/** 步骤开始事件 */
export interface StepStartEvent {
  type: 'step_start'
  step_id: string
  step_index: number
  total_steps: number
  message: string
}

/** 步骤完成事件 */
export interface StepDoneEvent {
  type: 'step_done'
  step_id: string
  step_index: number
  total_steps: number
  step_status: string
  message: string
}

/** 重新规划事件 */
export interface ReplanEvent {
  type: 'replan'
  step_index: number
  total_steps: number
  message: string
}

/** 聊天过程中的 SSE 事件联合类型 */
export type AgentEvent =
  | ThinkingEvent
  | ToolCallEvent
  | ToolResultEvent
  | ToolConfirmEvent
  | StatusEvent
  | AnsweringEvent
  | MaxIterationsEvent
  | ErrorEvent
  | PlanCreatedEvent
  | StepStartEvent
  | StepDoneEvent
  | ReplanEvent
