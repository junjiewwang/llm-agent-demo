/**
 * 根组件
 *
 * 初始化 tenant_id → 恢复会话 → 渲染主界面。
 */
import { useEffect, useState } from 'react'
import { useSessionStore } from './stores/sessionStore'
import { useChatStore } from './stores/chatStore'
import { useConversationStore } from './stores/conversationStore'
import { restoreSession } from './api/client'
import Header from './components/layout/Header'
import Sidebar from './components/layout/Sidebar'
import StatusPanel from './components/layout/StatusPanel'
import ChatView from './components/chat/ChatView'

export default function App() {
  const { tenantId, initTenantId } = useSessionStore()
  const setMessages = useChatStore((s) => s.setMessages)
  const setStatus = useChatStore((s) => s.setStatus)
  const setConversations = useConversationStore((s) => s.setConversations)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // 第一步：初始化 tenant_id
  useEffect(() => {
    initTenantId()
  }, [initTenantId])

  // 第二步：tenant_id 就绪后恢复会话
  useEffect(() => {
    if (!tenantId) return

    ;(async () => {
      try {
        const res = await restoreSession(tenantId)
        if (res.success && res.data) {
          setMessages(res.data.chat_history)
          setConversations(res.data.conversations)
          setStatus(res.data.status)
        } else if (res.error) {
          setError(res.error)
        }
      } catch (err) {
        setError((err as Error).message)
      } finally {
        setLoading(false)
      }
    })()
  }, [tenantId, setMessages, setConversations, setStatus])

  if (loading) {
    return (
      <div className="h-screen flex items-center justify-center bg-gray-50 dark:bg-gray-950">
        <div className="text-center">
          <div className="inline-block w-8 h-8 border-4 border-blue-600 border-t-transparent rounded-full animate-spin mb-4" />
          <p className="text-sm text-gray-500">正在初始化...</p>
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="h-screen flex items-center justify-center bg-gray-50 dark:bg-gray-950">
        <div className="text-center max-w-md px-6">
          <div className="text-4xl mb-4">⚠️</div>
          <h2 className="text-lg font-medium text-gray-800 dark:text-gray-200 mb-2">初始化失败</h2>
          <p className="text-sm text-gray-500 mb-4">{error}</p>
          <button
            onClick={() => window.location.reload()}
            className="px-4 py-2 rounded-lg bg-blue-600 text-white text-sm hover:bg-blue-700 transition-colors"
          >
            重试
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="h-screen flex flex-col bg-gray-100 dark:bg-gray-950 text-gray-900 dark:text-gray-100">
      <Header />
      <div className="flex flex-1 overflow-hidden">
        <Sidebar />
        <main className="flex-1 flex flex-col overflow-hidden bg-white dark:bg-gray-950">
          <ChatView />
        </main>
        <StatusPanel />
      </div>
    </div>
  )
}
