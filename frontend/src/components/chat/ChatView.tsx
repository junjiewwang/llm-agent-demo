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
import type { PlanProgress } from '../../stores/chatStore'
import { useSessionStore } from '../../stores/sessionStore'
import MessageBubble from './MessageBubble'
import ThinkingPanel from './ThinkingPanel'
import InputBox from './InputBox'

/** Plan 执行进度条 */
function PlanProgressBar({ progress }: { progress: PlanProgress }) {
  const { totalSteps, currentStep, currentDescription, completedSteps, steps } = progress
  const pct = totalSteps > 0 ? Math.round((completedSteps / totalSteps) * 100) : 0

  return (
    <div className="max-w-6xl mx-auto px-4 py-2">
      <div className="px-3 py-2.5 rounded-lg bg-indigo-50 dark:bg-indigo-950/30 border border-indigo-200/60 dark:border-indigo-800/40">
        {/* 标题行 */}
        <div className="flex items-center justify-between mb-1.5">
          <span className="text-xs font-medium text-indigo-700 dark:text-indigo-300">
            📋 计划执行中 · 步骤 {currentStep}/{totalSteps}
          </span>
          <span className="text-xs text-indigo-500 dark:text-indigo-400">
            {completedSteps} 步完成 ({pct}%)
          </span>
        </div>
        {/* 进度条 */}
        <div className="w-full h-1.5 bg-indigo-100 dark:bg-indigo-900/50 rounded-full overflow-hidden mb-1.5">
          <div
            className="h-full bg-indigo-500 dark:bg-indigo-400 rounded-full transition-all duration-500 ease-out"
            style={{ width: `${pct}%` }}
          />
        </div>
        {/* 当前步骤描述 */}
        {currentDescription && (
          <div className="text-xs text-indigo-600/80 dark:text-indigo-400/80 truncate">
            ▶ {currentDescription}
          </div>
        )}
        {/* 步骤指示器 */}
        <div className="flex gap-1 mt-1.5">
          {steps.map((step, i) => {
            const colors: Record<string, string> = {
              completed: 'bg-green-500',
              running: 'bg-indigo-500 animate-pulse',
              failed: 'bg-red-500',
              skipped: 'bg-gray-300 dark:bg-gray-600',
              pending: 'bg-gray-200 dark:bg-gray-700',
            }
            return (
              <div
                key={step.id}
                className={`h-1 flex-1 rounded-full ${colors[step.status] || colors.pending}`}
                title={`步骤 ${i + 1}: ${step.description}`}
              />
            )
          })}
        </div>
      </div>
    </div>
  )
}

/** 快捷提示卡片数据 */
const QUICK_PROMPTS = [
  { icon: '🔍', title: 'K8s 集群检查', prompt: '检查一下当前 k8s 集群的状态', color: 'from-blue-500/10 to-cyan-500/10 border-blue-200/50 dark:border-blue-800/40' },
  { icon: '📊', title: '架构图绘制', prompt: '用 Mermaid 画一个微服务架构图', color: 'from-violet-500/10 to-purple-500/10 border-violet-200/50 dark:border-violet-800/40' },
  { icon: '🐛', title: '日志分析', prompt: '分析最近的错误日志，找出根因', color: 'from-amber-500/10 to-orange-500/10 border-amber-200/50 dark:border-amber-800/40' },
  { icon: '🚀', title: '服务部署', prompt: '帮我部署服务到测试环境', color: 'from-emerald-500/10 to-teal-500/10 border-emerald-200/50 dark:border-emerald-800/40' },
]

/** 空状态引导页 */
function EmptyState({ onPrompt }: { onPrompt: (msg: string) => void }) {
  return (
    <div className="h-full flex items-center justify-center px-6">
      <div className="max-w-2xl w-full text-center">
        {/* 品牌标识 */}
        <div className="mb-6">
          <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-gradient-to-br from-indigo-500 to-violet-600 shadow-lg shadow-indigo-500/25 mb-4">
            <span className="text-3xl">🤖</span>
          </div>
          <h2 className="text-xl font-semibold text-gray-800 dark:text-gray-200 mb-1" style={{ fontFamily: 'var(--font-heading)' }}>
            LLM ReAct Agent
          </h2>
          <p className="text-sm text-gray-400 dark:text-gray-500">
            智能 AI 助手，支持工具调用、知识库问答与图表生成
          </p>
        </div>

        {/* 快捷提示卡片 */}
        <div className="grid grid-cols-2 gap-3 max-w-lg mx-auto">
          {QUICK_PROMPTS.map((item) => (
            <button
              key={item.title}
              onClick={() => onPrompt(item.prompt)}
              className={`group text-left p-3.5 rounded-xl border bg-gradient-to-br ${item.color} hover:shadow-md hover:scale-[1.02] transition-all duration-200`}
            >
              <span className="text-lg mb-1.5 block">{item.icon}</span>
              <span className="text-sm font-medium text-gray-700 dark:text-gray-300 block mb-0.5">{item.title}</span>
              <span className="text-xs text-gray-400 dark:text-gray-500 line-clamp-2">{item.prompt}</span>
            </button>
          ))}
        </div>

        <p className="mt-6 text-xs text-gray-300 dark:text-gray-600">
          点击卡片快速开始，或在下方输入自定义问题
        </p>
      </div>
    </div>
  )
}

export default function ChatView() {
  const { messages, thinkingNodes, isStreaming, sendMessage, stopChat, statusMessage, planProgress } = useChatStore()
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
    <div className="flex flex-col h-full relative">
      {/* 消息区域 */}
      <div className="flex-1 overflow-hidden relative">
        {messages.length === 0 && !isStreaming ? (
          <EmptyState onPrompt={handleSend} />
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
              Footer: () => (
                <>
                  {/* Plan 模式进度条 */}
                  {isStreaming && planProgress && (
                    <PlanProgressBar progress={planProgress} />
                  )}
                  {/* 非 Plan 模式：状态提示条（如上下文压缩进度） */}
                  {!planProgress && statusMessage && (
                    <div className="max-w-6xl mx-auto px-4 py-2">
                      <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-300 text-sm">
                        <span className="animate-pulse">●</span>
                        <span>{statusMessage}</span>
                      </div>
                    </div>
                  )}
                  {isStreaming && thinkingNodes.length > 0 ? (
                    <div className="max-w-6xl mx-auto px-4 pb-4">
                      <div className="flex justify-start mb-4">
                        <div className="max-w-[80%]">
                          <ThinkingPanel nodes={thinkingNodes} isStreaming defaultExpanded />
                        </div>
                      </div>
                    </div>
                  ) : <div className="h-4" />}
                </>
              ),
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
