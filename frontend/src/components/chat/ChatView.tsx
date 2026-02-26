/**
 * 聊天主视图
 *
 * 组合 MessageBubble + ThinkingPanel + InputBox。
 * 使用 react-virtuoso 进行虚拟滚动。
 *
 * 思考过程的两种显示模式：
 * 1. 流式中：在消息列表底部（Footer）实时展示，默认展开
 * 2. 完成后：作为 assistant 消息的一部分内联展示，默认收起
 */
import { useCallback, useEffect, useRef } from 'react'
import { Virtuoso, type VirtuosoHandle } from 'react-virtuoso'
import { useChatStore } from '../../stores/chatStore'
import { useSessionStore } from '../../stores/sessionStore'
import MessageBubble from './MessageBubble'
import ThinkingPanel from './ThinkingPanel'
import InputBox from './InputBox'

export default function ChatView() {
  const { messages, thinkingNodes, isStreaming, sendMessage, stopChat } = useChatStore()
  const tenantId = useSessionStore((s) => s.tenantId)
  const virtuosoRef = useRef<VirtuosoHandle>(null)

  /** 是否处于底部附近（用户未主动上滚） */
  const isAtBottomRef = useRef(true)
  /** 上一次 messages.length，用于判断是否有新消息 */
  const prevMsgCountRef = useRef(messages.length)
  /**
   * 标记"需要滚到底部"。
   * 当 Virtuoso 因虚拟列表高度修正（如长 Markdown 表格渲染后实际高度 > 估算高度）
   * 导致滚动位置偏移时，isScrolling 回调在滚动停止后会检查此标记并补一次 scrollTo。
   */
  const needScrollRef = useRef(false)

  /** 执行一次 smooth scrollTo 底部 */
  const doScroll = useCallback(() => {
    virtuosoRef.current?.scrollTo({
      top: Number.MAX_SAFE_INTEGER,
      behavior: 'smooth',
    })
  }, [])

  /**
   * 可靠地平滑滚到 Virtuoso 容器的绝对底部。
   *
   * 设置 needScrollRef 标记 + 双重 RAF 触发滚动。
   * 如果 Virtuoso 在高度修正后导致位置偏移，isScrolling 回调
   * 会在滚动停止时检测标记并补一次 scrollTo，确保最终到底。
   */
  const scrollToBottom = useCallback(() => {
    needScrollRef.current = true
    requestAnimationFrame(() => {
      requestAnimationFrame(doScroll)
    })
  }, [doScroll])

  /**
   * Virtuoso isScrolling 回调：响应滚动状态变化。
   * 当滚动停止（scrolling=false）且 needScrollRef 为 true 时，
   * 补一次 scrollTo 确保到底——解决 Virtuoso 高度修正导致的"回弹"问题。
   * needScrollRef 执行后立即置 false，防止循环触发。
   */
  const handleIsScrolling = useCallback((scrolling: boolean) => {
    if (!scrolling && needScrollRef.current) {
      needScrollRef.current = false
      doScroll()
    }
  }, [doScroll])

  // 消息数量变化（用户发送 / assistant 回复）→ 平滑滚到底
  useEffect(() => {
    if (messages.length !== prevMsgCountRef.current) {
      prevMsgCountRef.current = messages.length
      isAtBottomRef.current = true
      scrollToBottom()
    }
  }, [messages.length, scrollToBottom])

  // 流式进行中，思考节点更新 → 仅当用户在底部时跟随滚动
  useEffect(() => {
    if (isStreaming && isAtBottomRef.current) {
      scrollToBottom()
    }
  }, [thinkingNodes.length, isStreaming, scrollToBottom])

  /**
   * followOutput 回调：Virtuoso 内部在 data 变化时判断是否跟随。
   * 用函数形式而非字符串，确保只在用户处于底部时触发跟随。
   */
  const handleFollowOutput = useCallback((atBottom: boolean) => {
    if (atBottom || isAtBottomRef.current) return 'smooth' as const
    return false as const
  }, [])

  const handleAtBottomStateChange = useCallback((atBottom: boolean) => {
    isAtBottomRef.current = atBottom
  }, [])

  const handleSend = useCallback((message: string) => {
    sendMessage(tenantId, message)
    isAtBottomRef.current = true
    scrollToBottom()
  }, [tenantId, sendMessage, scrollToBottom])

  const handleStop = useCallback(() => {
    stopChat(tenantId)
  }, [tenantId, stopChat])

  return (
    <div className="flex flex-col h-full">
      {/* 消息区域 */}
      <div className="flex-1 overflow-hidden">
        {messages.length === 0 && !isStreaming ? (
          <div className="h-full flex items-center justify-center px-6">
            <div className="max-w-2xl w-full text-center">
              <p className="text-sm text-gray-400/80 dark:text-gray-500/80">输入消息开始对话，支持 Mermaid 图表、工具调用与知识库问答</p>
            </div>
          </div>
        ) : (
          <Virtuoso
            ref={virtuosoRef}
            className="h-full"
            data={messages}
            followOutput={handleFollowOutput}
            initialTopMostItemIndex={messages.length - 1}
            atBottomStateChange={handleAtBottomStateChange}
            atBottomThreshold={150}
            isScrolling={handleIsScrolling}
            itemContent={(_, msg) => (
              <div className="max-w-6xl mx-auto px-4">
                <MessageBubble message={msg} />
              </div>
            )}
            components={{
              // 流式进行中：在底部实时展示思考过程（默认展开）
              Footer: () =>
                isStreaming && thinkingNodes.length > 0 ? (
                  <div className="max-w-6xl mx-auto px-4 pb-2">
                    <div className="flex justify-start mb-4">
                      <div className="max-w-[80%]">
                        <ThinkingPanel nodes={thinkingNodes} isStreaming defaultExpanded />
                      </div>
                    </div>
                  </div>
                ) : null,
            }}
          />
        )}
      </div>

      {/* 输入框 */}
      <InputBox
        onSend={handleSend}
        onStop={handleStop}
        isStreaming={isStreaming}
        disabled={!tenantId}
      />
    </div>
  )
}
