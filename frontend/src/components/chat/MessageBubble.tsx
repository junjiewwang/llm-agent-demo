/**
 * 消息气泡组件
 *
 * assistant 消息如果关联了思考过程（thinkingNodes），
 * 会在回答上方显示可折叠的 ThinkingPanel（默认收起）。
 * assistant 消息如果携带 usage，会在气泡底部显示 token 用量。
 */
import { useState } from 'react'
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
    <div className="flex items-center gap-2 mt-1.5 px-1 select-none flex-wrap">
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-800 text-[10px] text-gray-500 dark:text-gray-400" title="输入 Token">
        <svg className="w-2.5 h-2.5 opacity-60" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 10l7-7m0 0l7 7m-7-7v18" /></svg>
        {formatTokens(usage.input_tokens)}
      </span>
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-800 text-[10px] text-gray-500 dark:text-gray-400" title="输出 Token">
        <svg className="w-2.5 h-2.5 opacity-60" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 14l-7 7m0 0l-7-7m7 7V3" /></svg>
        {formatTokens(usage.output_tokens)}
      </span>
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-indigo-50 dark:bg-indigo-950/30 text-[10px] text-indigo-500 dark:text-indigo-400 font-medium" title="总计 Token">
        {formatTokens(usage.total_tokens)}
      </span>
      {usage.duration_ms > 0 && (
        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-800 text-[10px] text-gray-500 dark:text-gray-400" title="耗时">
          <svg className="w-2.5 h-2.5 opacity-60" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
          {(usage.duration_ms / 1000).toFixed(1)}s
        </span>
      )}
    </div>
  )
}

/** 复制按钮 */
function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  const handleCopy = async (e: React.MouseEvent) => {
    e.stopPropagation()
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch { /* ignore */ }
  }
  return (
    <button
      onClick={handleCopy}
      className="p-1 rounded hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors"
      title={copied ? '已复制' : '复制内容'}
    >
      {copied ? (
        <svg className="w-3.5 h-3.5 text-green-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
        </svg>
      ) : (
        <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
        </svg>
      )}
    </button>
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
        <div className="relative group/bubble">
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
          {/* hover 操作栏 */}
          {!SYSTEM_HINTS.has(message.content) && (
            <div className={`absolute ${isUser ? 'left-0' : 'right-0'} -bottom-1 translate-y-full opacity-0 group-hover/bubble:opacity-100 transition-opacity duration-150 flex items-center gap-1 pt-1`}>
              <CopyButton text={message.content} />
            </div>
          )}
        </div>

        {/* Token 用量（仅 assistant 消息，完成后显示） */}
        {!isUser && message.usage && <UsageBar usage={message.usage} />}
      </div>
    </div>
  )
}
