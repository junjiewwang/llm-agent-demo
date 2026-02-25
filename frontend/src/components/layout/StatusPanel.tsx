/**
 * 系统状态面板
 */
import { useChatStore } from '../../stores/chatStore'
import { useUIStore } from '../../stores/uiStore'
import KnowledgePanel from './KnowledgePanel'

export default function StatusPanel() {
  const status = useChatStore((s) => s.status)
  const statusPanelOpen = useUIStore((s) => s.statusPanelOpen)

  if (!statusPanelOpen || !status) return null

  return (
    <aside className="w-72 flex-shrink-0 bg-gray-50 dark:bg-gray-900 border-l border-gray-200 dark:border-gray-700 p-4 overflow-y-auto">
      <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-4 flex items-center gap-2">
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
        </svg>
        系统状态
      </h3>

      <div className="space-y-3 text-sm">
        <StatusItem label="状态" value={status.initialized ? '✅ 已初始化' : '⚠️ 未初始化'} />
        {status.model && <StatusItem label="模型" value={status.model} />}
        {status.current_conversation && (
          <>
            <StatusItem label="当前对话" value={status.current_conversation.title} />
            <StatusItem label="短期记忆" value={`${status.current_conversation.memory_tokens} tokens`} />
          </>
        )}
        <StatusItem label="对话数" value={String(status.conversation_count)} />
        <StatusItem label="长期记忆" value={`${status.long_term_memory_count} 条`} />
        <StatusItem label="知识库" value={`${status.knowledge_base_chunks} 个文本块`} />
      </div>

      <KnowledgePanel />
    </aside>
  )
}

function StatusItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between items-center py-1.5 border-b border-gray-100 dark:border-gray-800">
      <span className="text-gray-500 dark:text-gray-400">{label}</span>
      <span className="text-gray-800 dark:text-gray-200 font-mono text-xs">{value}</span>
    </div>
  )
}
