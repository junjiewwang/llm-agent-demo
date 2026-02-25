/**
 * 消息气泡组件
 *
 * assistant 消息如果关联了思考过程（thinkingNodes），
 * 会在回答上方显示可折叠的 ThinkingPanel（默认收起）。
 */
import type { ChatMessage } from '../../types'
import MarkdownRenderer from './MarkdownRenderer'
import ThinkingPanel from './ThinkingPanel'

interface Props {
  message: ChatMessage
}

/** 系统提示类消息（如 [对话已停止]），居中灰色小字 */
const SYSTEM_HINTS = new Set(['[对话已停止]'])

export default function MessageBubble({ message }: Props) {
  const isUser = message.role === 'user'

  // 系统提示：居中灰色小字，不使用气泡样式
  if (!isUser && SYSTEM_HINTS.has(message.content)) {
    return (
      <div className="flex justify-center mb-3">
        <span className="text-xs text-gray-400 dark:text-gray-500">
          {message.content}
        </span>
      </div>
    )
  }

  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'} mb-4`}>
      <div className={`max-w-[80%] ${isUser ? '' : 'space-y-2'}`}>
        {/* 思考过程：仅 assistant 消息，在回答上方，默认收起 */}
        {!isUser && message.thinkingNodes && message.thinkingNodes.length > 0 && (
          <ThinkingPanel nodes={message.thinkingNodes} defaultExpanded={false} />
        )}

        {/* 消息内容 */}
        <div
          className={`rounded-2xl px-4 py-3 ${
            isUser
              ? 'bg-blue-600 text-white rounded-br-md'
              : 'bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 shadow-sm border border-gray-100 dark:border-gray-700 rounded-bl-md'
          }`}
        >
          {isUser ? (
            <p className="whitespace-pre-wrap text-sm leading-relaxed">{message.content}</p>
          ) : (
            <div className="prose prose-sm dark:prose-invert max-w-none">
              <MarkdownRenderer content={message.content} />
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
