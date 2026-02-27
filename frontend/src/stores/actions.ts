/**
 * 跨 Store 操作层
 *
 * 将涉及多个 Store 联动的业务操作集中到此模块，
 * 消除 Store 之间的直接耦合（A.getState().B），保持各 Store 单一职责。
 */
import type { ChatMessage, ConversationInfo, StatusInfo } from '../types'
import { useChatStore } from './chatStore'
import { useConversationStore } from './conversationStore'

/**
 * 同步 done 事件数据到各 Store
 * 由 chatStore.onDone 回调调用
 */
export function syncDoneEvent(
  history: ChatMessage[],
  conversations: ConversationInfo[],
  status: StatusInfo,
): void {
  useChatStore.getState().setMessages(history)
  useChatStore.getState().setStatus(status)
  useConversationStore.getState().setConversations(conversations)
}

/**
 * 同步对话切换/删除/新建后的数据到各 Store
 * 由 conversationStore 的 CRUD 操作调用
 */
export function syncConversationData(
  chatHistory: ChatMessage[],
  conversations: ConversationInfo[],
  status: StatusInfo,
): void {
  useConversationStore.getState().setConversations(conversations)
  useChatStore.getState().setMessages(chatHistory)
  useChatStore.getState().setStatus(status)
  useChatStore.getState().clearThinking()
}

/**
 * 新建对话后的数据同步（无聊天历史，仅清空）
 */
export function syncNewConversation(
  conversations: ConversationInfo[],
  status: StatusInfo,
): void {
  useConversationStore.getState().setConversations(conversations)
  useChatStore.getState().setMessages([])
  useChatStore.getState().setStatus(status)
}
