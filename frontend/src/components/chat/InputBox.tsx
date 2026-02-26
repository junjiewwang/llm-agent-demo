/**
 * 消息输入框组件
 *
 * 卡片式布局：textarea 在上，操作栏（提示 + 按钮）在下。
 * 支持自动伸缩（最大 240px）和手动拖拽（最大 50vh）。
 */
import { useState, useRef, useEffect, useCallback } from 'react'

interface Props {
  onSend: (message: string) => void
  onStop: () => void
  isStreaming: boolean
  disabled?: boolean
}

/** 自动伸缩的上限（px），超出后需手动拖拽 */
const AUTO_GROW_MAX = 200

export default function InputBox({ onSend, onStop, isStreaming, disabled }: Props) {
  const [input, setInput] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  /** 用户是否手动拖拽过大小；拖拽后暂停自动伸缩，避免冲突 */
  const userResizedRef = useRef(false)
  /** IME 组合输入进行中（中文/日文等输入法选词阶段） */
  const isComposingRef = useRef(false)

  // 自动伸缩高度（仅在用户未手动拖拽时生效）
  useEffect(() => {
    const el = textareaRef.current
    if (!el || userResizedRef.current) return
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, AUTO_GROW_MAX) + 'px'
  }, [input])

  // 监听手动拖拽：mouseup 时检测高度是否被用户改变
  useEffect(() => {
    const el = textareaRef.current
    if (!el) return
    const observer = new ResizeObserver(() => {
      // ResizeObserver 会在自动伸缩时也触发，用 raf 延后判断
      // 这里只标记 flag；发送清空时重置
    })
    const onMouseUp = () => { userResizedRef.current = true }
    el.addEventListener('mouseup', onMouseUp)
    observer.observe(el)
    return () => {
      el.removeEventListener('mouseup', onMouseUp)
      observer.disconnect()
    }
  }, [])

  const handleSend = useCallback(() => {
    const msg = input.trim()
    if (!msg || isStreaming || disabled) return
    onSend(msg)
    setInput('')
    // 发送后重置：清除手动拖拽标记 & 恢复初始高度
    userResizedRef.current = false
    const el = textareaRef.current
    if (el) el.style.height = 'auto'
  }, [input, isStreaming, disabled, onSend])

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    // IME 组合输入中（如中文选词），Enter 用于确认候选词，不触发发送
    if (e.key === 'Enter' && !e.shiftKey && !isComposingRef.current) {
      e.preventDefault()
      handleSend()
    }
  }, [handleSend])

  const canSend = input.trim().length > 0 && !disabled

  return (
    <div className="bg-white dark:bg-gray-900 px-4 pb-4 pt-2">
      <div className="max-w-6xl mx-auto">
        {/* 卡片容器 */}
        <div className="relative rounded-2xl border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 shadow-lg focus-within:ring-2 focus-within:ring-blue-500 focus-within:border-transparent transition">
          {/* textarea */}
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            onCompositionStart={() => { isComposingRef.current = true }}
            onCompositionEnd={() => { isComposingRef.current = false }}
            placeholder="输入消息..."
            rows={3}
            disabled={disabled}
            className="block w-full resize-y rounded-2xl bg-transparent px-4 pt-3 pb-10 text-sm text-gray-900 dark:text-gray-100 placeholder-gray-400 focus:outline-none disabled:opacity-50"
            style={{ minHeight: '80px', maxHeight: '50vh' }}
          />

          {/* 底部操作栏：绝对定位在 textarea 内部底部 */}
          <div className="absolute bottom-2 left-3 right-3 flex items-center justify-between pointer-events-none">
            {/* 左侧提示 */}
            <span className="text-[11px] text-gray-400/70 select-none">
              Shift+Enter 换行
            </span>

            {/* 右侧按钮 */}
            <div className="pointer-events-auto">
              {isStreaming ? (
                <button
                  onClick={onStop}
                  className="flex items-center justify-center w-9 h-9 rounded-full bg-red-500 hover:bg-red-600 text-white transition-colors"
                  title="停止生成"
                >
                  <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
                    <rect x="6" y="6" width="12" height="12" rx="2" />
                  </svg>
                </button>
              ) : (
                <button
                  onClick={handleSend}
                  disabled={!canSend}
                  className="flex items-center justify-center w-9 h-9 rounded-full bg-blue-600 hover:bg-blue-700 disabled:bg-gray-300 dark:disabled:bg-gray-600 text-white transition-colors disabled:cursor-not-allowed"
                  title="发送 (Enter)"
                >
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 10.5L12 3m0 0l7.5 7.5M12 3v18" />
                  </svg>
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
