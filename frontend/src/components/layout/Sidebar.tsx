/**
 * 侧边栏 — 对话列表管理
 */
import { useConversationStore } from '../../stores/conversationStore'
import { useSessionStore } from '../../stores/sessionStore'
import { useChatStore } from '../../stores/chatStore'
import { useUIStore } from '../../stores/uiStore'

export default function Sidebar() {
  const tenantId = useSessionStore((s) => s.tenantId)
  const { conversations, activeId, createConversation, switchConversation, deleteConversation } =
    useConversationStore()
  const isStreaming = useChatStore((s) => s.isStreaming)
  const { sidebarOpen, sidebarCollapsed } = useUIStore()

  if (!sidebarOpen) return null

  const handleNew = () => {
    if (isStreaming) return
    createConversation(tenantId)
  }

  const handleSwitch = (convId: string) => {
    if (isStreaming || convId === activeId) return
    switchConversation(tenantId, convId)
  }

  const handleDelete = (e: React.MouseEvent, convId: string) => {
    e.stopPropagation()
    if (isStreaming) return
    deleteConversation(tenantId, convId)
  }

  return (
    <aside
      className={`${sidebarCollapsed ? 'w-16' : 'w-64'} flex-shrink-0 bg-gray-50 dark:bg-gray-900 border-r border-gray-200 dark:border-gray-700 flex flex-col h-full transition-[width] duration-200`}
    >
      {/* 头部 */}
      <div className={`${sidebarCollapsed ? 'p-2.5' : 'p-4'} border-b border-gray-200 dark:border-gray-700`}>
        <button
          onClick={handleNew}
          disabled={isStreaming}
          className="w-full flex items-center justify-center gap-2 h-10 rounded-xl text-white text-sm font-medium transition-colors disabled:bg-gray-400"
          style={{ backgroundColor: isStreaming ? undefined : 'var(--brand-primary)' }}
          onMouseEnter={(e) => { if (!isStreaming) e.currentTarget.style.backgroundColor = 'var(--brand-primary-hover)' }}
          onMouseLeave={(e) => { if (!isStreaming) e.currentTarget.style.backgroundColor = 'var(--brand-primary)' }}
          title="新建对话"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
          </svg>
          {!sidebarCollapsed && '新建对话'}
        </button>
      </div>

      {/* 对话列表 */}
      <div className={`${sidebarCollapsed ? 'p-1.5' : 'p-2'} flex-1 overflow-y-auto space-y-1`}>
        {conversations.length === 0 ? (
          sidebarCollapsed ? (
            <div className="h-8" />
          ) : (
            <p className="text-xs text-gray-400 text-center py-8">暂无对话</p>
          )
        ) : (
          conversations.map((conv) => (
            <div
              key={conv.id}
              onClick={() => handleSwitch(conv.id)}
              title={conv.title}
              className={`group flex items-center ${sidebarCollapsed ? 'justify-center px-2' : 'gap-2 px-3'} py-2.5 rounded-lg cursor-pointer text-sm transition-colors ${
                conv.id === activeId
                  ? 'text-indigo-700 dark:text-indigo-300 font-medium'
                  : 'text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800'
              }`}
              style={conv.id === activeId ? { backgroundColor: 'var(--brand-primary-light)' } : undefined}
            >
              <svg className="w-4 h-4 flex-shrink-0 opacity-60" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={1.5}
                  d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z"
                />
              </svg>
              {!sidebarCollapsed && <span className="truncate flex-1">{conv.title}</span>}
              {!sidebarCollapsed && (
                <button
                  onClick={(e) => handleDelete(e, conv.id)}
                  className="opacity-0 group-hover:opacity-100 p-1 rounded hover:bg-red-100 dark:hover:bg-red-900/30 text-gray-400 hover:text-red-500 transition-all"
                  title="删除对话"
                >
                  <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"
                    />
                  </svg>
                </button>
              )}
            </div>
          ))
        )}
      </div>
    </aside>
  )
}
