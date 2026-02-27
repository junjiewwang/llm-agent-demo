/**
 * API 通用类型与认证相关类型
 */
import type { ChatMessage } from './chat'
import type { ConversationInfo, StatusInfo } from './conversation'
import type { MessageUsage } from './chat'

/** API 统一响应 */
export interface ApiResponse<T = unknown> {
  success: boolean
  data: T
  error?: string
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

/** Token 认证响应 */
export interface TokenResponse {
  access_token: string
  token_type: string
  user_id: string
  username: string
}

/** 用户信息 */
export interface UserInfo {
  id: string
  username: string
  created_at: number
}

/** Skill 信息 */
export interface SkillInfo {
  name: string
  display_name: string
  description: string
  priority: number
  enabled: boolean
  required_tools: string[]
  tools_satisfied: boolean
  trigger_patterns: string[]
  has_resources: boolean
  resource_count: number
}

/** Skill 启停请求 */
export interface ToggleSkillRequest {
  enabled: boolean
}
