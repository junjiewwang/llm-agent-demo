/**
 * 消息气泡组件
 *
 * assistant 消息如果关联了思考过程（thinkingNodes），
 * 会在回答上方显示可折叠的 ThinkingPanel（默认收起）。
 * assistant 消息如果携带 usage，会在气泡底部显示 token 用量。
 */
import type { ChatMessage, MessageUsage } from '../../types'
import MarkdownRenderer from './MarkdownRenderer'
import ThinkingPanel from './ThinkingPanel'

interface Props {
  message: ChatMessage
}

/** 系统提示类消息（如 [对话已停止]），居中灰色小字 */
const SYSTEM_HINTS = new Set(['[对话已停止]'])

/** 格式化 token 数字（千位分隔） */
function formatTokens(n: number): string {
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n)
}

/** token 用量展示条 */
function UsageBar({ usage }: { usage: MessageUsage }) {
  return (
    <div className="flex items-center gap-3 mt-1.5 px-1 text-[11px] text-gray-400 dark:text-gray-500 select-none">
      <span title="输入 Token">↑{formatTokens(usage.input_tokens)}</span>
      <span title="输出 Token">↓{formatTokens(usage.output_tokens)}</span>
      <span title="总计 Token">Σ{formatTokens(usage.total_tokens)}</span>
      {usage.duration_ms > 0 && (
        <span title="耗时">{(usage.duration_ms / 1000).toFixed(1)}s</span>
      )}
    </div>
  )
}

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

  // 判断是否包含 Mermaid 图表（用于内层宽度分类）
  const hasMermaid = !isUser && /```mermaid\b/.test(message.content)

  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'} mb-4 animate-[msgFadeIn_0.3s_ease-out]`}>
      {/* 外层：assistant 固定宽度防缩放抖动；user 保持内容驱动 */}
      <div className={
        isUser
          ? 'max-w-[80%]'
          : hasMermaid
            ? 'w-full max-w-[84%] space-y-2'
            : 'w-full max-w-[78%] space-y-2'
      }>
        {/* 思考过程：仅 assistant 消息，在回答上方，默认收起 */}
        {!isUser && message.thinkingNodes && message.thinkingNodes.length > 0 && (
          <ThinkingPanel nodes={message.thinkingNodes} defaultExpanded={false} />
        )}

        {/* 消息内容 */}
        <div
          className={`rounded-2xl px-4 py-3 ${
            isUser
              ? 'text-white rounded-br-md'
              : 'bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 shadow-sm border border-gray-100 dark:border-gray-700 rounded-bl-md'
          }`}
          style={isUser ? { backgroundColor: 'var(--brand-primary)' } : undefined}
        >
          {isUser ? (
            <p className="whitespace-pre-wrap text-sm leading-relaxed">{message.content}</p>
          ) : (
            <div className="prose prose-sm dark:prose-invert max-w-none">
              <MarkdownRenderer content={message.content} />
            </div>
          )}
        </div>

        {/* Token 用量（仅 assistant 消息，完成后显示） */}
        {!isUser && message.usage && <UsageBar usage={message.usage} />}
      </div>
    </div>
  )
}
