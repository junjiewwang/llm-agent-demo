/**
 * REST API 客户端
 */
import type {
  ApiResponse,
  SessionData,
  NewConversationData,
  ConversationActionData,
  ConversationInfo,
  StatusInfo,
  UploadData,
  TokenResponse,
  UserInfo,
  SkillInfo,
} from '../types/index'

import { useSessionStore } from '../stores/sessionStore'

const BASE_URL = '/api'

async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string>),
  }

  // 自动注入 JWT Token
  const token = useSessionStore.getState().token
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  const response = await fetch(`${BASE_URL}${path}`, {
    ...options,
    headers,
  })

  if (response.status === 401) {
    useSessionStore.getState().logout()
    // throw new Error('Unauthorized') // 让调用方处理
  }

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}))
    const errorMessage = errorData.detail || errorData.error || response.statusText
    throw new Error(errorMessage)
  }

  return response.json()
}

/** 注册 */
export async function register(username: string, password: string): Promise<ApiResponse<TokenResponse>> {
  return request('/auth/register', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  })
}

/** 登录 */
export async function login(username: string, password: string): Promise<ApiResponse<TokenResponse>> {
  return request('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  })
}

/** 获取当前用户信息 */
export async function getMe(): Promise<ApiResponse<UserInfo>> {
  return request('/auth/me')
}

/** 恢复会话 */
export async function restoreSession(tenantId: string): Promise<ApiResponse<SessionData>> {
  return request(`/session?tenant_id=${encodeURIComponent(tenantId)}`)
}

/** 获取对话列表 */
export async function listConversations(tenantId: string): Promise<ApiResponse<ConversationInfo[]>> {
  return request(`/conversations?tenant_id=${encodeURIComponent(tenantId)}`)
}

/** 新建对话 */
export async function createConversation(tenantId: string): Promise<ApiResponse<NewConversationData>> {
  return request(`/conversations?tenant_id=${encodeURIComponent(tenantId)}`, { method: 'POST' })
}

/** 切换对话 */
export async function activateConversation(
  tenantId: string,
  convId: string,
): Promise<ApiResponse<ConversationActionData>> {
  return request(`/conversations/${convId}/activate?tenant_id=${encodeURIComponent(tenantId)}`, {
    method: 'PUT',
  })
}

/** 删除对话 */
export async function deleteConversation(
  tenantId: string,
  convId: string,
): Promise<ApiResponse<ConversationActionData>> {
  return request(`/conversations/${convId}?tenant_id=${encodeURIComponent(tenantId)}`, {
    method: 'DELETE',
  })
}

/** 停止聊天 */
export async function stopChat(tenantId: string): Promise<ApiResponse<{ message: string }>> {
  return request(`/chat/stop?tenant_id=${encodeURIComponent(tenantId)}`, { method: 'POST' })
}

/** 确认或拒绝工具执行 */
export async function confirmTool(
  confirmId: string,
  approved: boolean,
): Promise<ApiResponse<{ message: string }>> {
  return request('/chat/confirm', {
    method: 'POST',
    body: JSON.stringify({ confirm_id: confirmId, approved }),
  })
}

/** 检查聊天状态 */
export async function chatStatus(tenantId: string): Promise<ApiResponse<{ is_running: boolean }>> {
  return request(`/chat/status?tenant_id=${encodeURIComponent(tenantId)}`)
}

/** 获取系统状态 */
export async function getStatus(tenantId: string): Promise<ApiResponse<StatusInfo>> {
  return request(`/status?tenant_id=${encodeURIComponent(tenantId)}`)
}

/** 上传文件到知识库 */
export async function uploadFiles(files: File[]): Promise<ApiResponse<UploadData>> {
  const formData = new FormData()
  files.forEach((f) => formData.append('files', f))

  const response = await fetch(`${BASE_URL}/knowledge/upload`, {
    method: 'POST',
    body: formData,
  })

  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${response.statusText}`)
  }

  return response.json()
}

/** 清空知识库 */
export async function clearKnowledgeBase(): Promise<ApiResponse<{ message: string }>> {
  return request('/knowledge', { method: 'DELETE' })
}

/** 获取 Skill 列表 */
export async function listSkills(): Promise<ApiResponse<SkillInfo[]>> {
  return request('/skills')
}

/** 启用/禁用 Skill */
export async function toggleSkill(
  name: string,
  enabled: boolean,
): Promise<ApiResponse<{ name: string; enabled: boolean }>> {
  return request(`/skills/${encodeURIComponent(name)}/toggle`, {
    method: 'POST',
    body: JSON.stringify({ enabled }),
  })
}
