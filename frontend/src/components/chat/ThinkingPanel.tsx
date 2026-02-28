/**
 * 思考过程面板
 *
 * 以折叠面板展示 Agent 的推理过程（THINKING / TOOL_CALL / TOOL_RESULT 等事件）。
 *
 * Plan 模式下：
 *   - 步骤节点（step_start）作为分组标题，子循环事件嵌套在 children 中
 *   - 步骤节点可独立折叠/展开
 *   - 完成的步骤默认收起，当前步骤默认展开
 *
 * 两种使用场景：
 * 1. 流式进行中（isStreaming=true）：在消息底部实时展示，默认展开
 * 2. 完成后（嵌入 assistant 消息）：默认收起，点击可展开查看
 */
import { useEffect, useRef, useState } from 'react'
import type { ThinkingNode, ToolConfirmEvent } from '../../types'
import { useChatStore } from '../../stores/chatStore'

interface Props {
  nodes: ThinkingNode[]
  isStreaming?: boolean
  /** 初始是否展开，流式中默认 true，完成后默认 false */
  defaultExpanded?: boolean
}

function formatEvent(node: ThinkingNode): string {
  const e = node.event
  switch (e.type) {
    case 'thinking':
      return `🔄 第 ${e.iteration}/${e.max_iterations} 轮思考`
    case 'tool_call': {
      const argsPreview = JSON.stringify(e.tool_args).slice(0, 80)
      const parallel = e.parallel_total > 1 ? ` ⚡[${e.parallel_index}/${e.parallel_total}]` : ''
      return `🔧 调用工具: ${e.tool_name}${parallel} | ${argsPreview}${argsPreview.length >= 80 ? '...' : ''}`
    }
    case 'tool_confirm':
      return ''
    case 'tool_result': {
      const icon = e.success ? '✅' : '❌'
      const preview = e.tool_result_preview.replace(/\n/g, ' ').slice(0, 80)
      const parallel = e.parallel_total > 1 ? ` [${e.parallel_index}/${e.parallel_total}]` : ''
      return `${icon} 结果${parallel} (${e.duration_ms}ms): ${preview}${preview.length >= 80 ? '...' : ''}`
    }
    case 'answering':
      return '💡 正在生成回答...'
    case 'max_iterations':
      return '⚠️ 达到最大迭代次数，正在总结...'
    case 'error':
      return `❌ 错误: ${e.message}`
    case 'plan_created':
      return `📋 已生成执行计划 (${e.total_steps} 步)`
    case 'step_start':
      return `步骤 ${e.step_index + 1}/${e.total_steps}: ${e.message}`
    case 'step_done': {
      const statusIcon = e.step_status === 'completed' ? '✅' : e.step_status === 'failed' ? '❌' : '⏭️'
      return `${statusIcon} 步骤 ${e.step_index + 1}/${e.total_steps} ${e.step_status}: ${e.message.slice(0, 80)}`
    }
    case 'replan':
      return `🔄 ${e.message}`
    case 'status':
      return ''
    default:
      return ''
  }
}

/** 步骤状态图标 */
function stepStatusIcon(node: ThinkingNode, allNodes: ThinkingNode[]): string {
  if (node.event.type !== 'step_start') return '○'
  const stepIndex = node.event.step_index
  // 查找对应的 step_done 节点
  const doneNode = allNodes.find(
    (n) => n.event.type === 'step_done' && n.event.step_index === stepIndex,
  )
  if (!doneNode) return '▶️'
  const status = doneNode.event.type === 'step_done' ? doneNode.event.step_status : ''
  if (status === 'completed') return '✅'
  if (status === 'failed') return '❌'
  if (status === 'skipped') return '⏭️'
  return '▶️'
}

/** 工具执行确认卡片 */
function ConfirmCard({ event }: { event: ToolConfirmEvent }) {
  const handleConfirm = useChatStore((s) => s.handleConfirm)
  const [isLoading, setIsLoading] = useState(false)

  const handleClick = async (approved: boolean) => {
    setIsLoading(true)
    await handleConfirm(event.confirm_id, approved)
  }

  const argsStr = JSON.stringify(event.tool_args, null, 2)

  return (
    <div className="mx-1 my-1.5 p-3 bg-amber-50 dark:bg-amber-950/40 border border-amber-200 dark:border-amber-800/60 rounded-lg">
      <div className="flex items-center gap-1.5 text-amber-700 dark:text-amber-400 font-medium text-xs mb-2">
        <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z" />
        </svg>
        工具执行确认
      </div>
      <div className="text-xs text-gray-600 dark:text-gray-300 space-y-1 mb-2.5">
        <div>
          <span className="text-gray-400 dark:text-gray-500">工具: </span>
          <span className="font-medium">{event.tool_name}</span>
        </div>
        <div>
          <span className="text-gray-400 dark:text-gray-500">参数: </span>
          <pre className="inline-block mt-0.5 p-1.5 bg-gray-100 dark:bg-gray-800 rounded text-[11px] max-h-24 overflow-y-auto whitespace-pre-wrap break-all">
            {argsStr.length > 300 ? argsStr.slice(0, 300) + '...' : argsStr}
          </pre>
        </div>
      </div>
      <div className="flex gap-2">
        <button
          onClick={() => handleClick(true)}
          disabled={isLoading}
          className="px-3 py-1 text-xs font-medium text-white bg-green-600 hover:bg-green-700 disabled:opacity-50 disabled:cursor-not-allowed rounded transition-colors"
        >
          {isLoading ? '处理中...' : '✅ 批准执行'}
        </button>
        <button
          onClick={() => handleClick(false)}
          disabled={isLoading}
          className="px-3 py-1 text-xs font-medium text-gray-600 dark:text-gray-300 bg-gray-200 dark:bg-gray-700 hover:bg-gray-300 dark:hover:bg-gray-600 disabled:opacity-50 disabled:cursor-not-allowed rounded transition-colors"
        >
          {isLoading ? '处理中...' : '❌ 拒绝'}
        </button>
      </div>
    </div>
  )
}

/** 渲染单个节点的子事件列表（用于 Plan 模式步骤的 children） */
function StepChildren({
  children,
  isStreaming,
  pendingConfirm,
}: {
  children: ThinkingNode[]
  isStreaming: boolean
  pendingConfirm: ToolConfirmEvent | null
}) {
  return (
    <div className="pl-4 border-l border-gray-200/40 dark:border-gray-700/40 ml-1 mt-0.5 space-y-0.5">
      {children.map((child) => {
        if (child.event.type === 'tool_confirm') {
          const isPending = isStreaming && pendingConfirm?.confirm_id === child.event.confirm_id
          if (isPending) {
            return <ConfirmCard key={child.id} event={child.event} />
          }
          return (
            <div key={child.id} className="py-0.5 text-amber-500 dark:text-amber-400">
              ⚠️ 已请求确认: {child.event.tool_name}
            </div>
          )
        }

        const text = formatEvent(child)
        if (!text) return null
        const isThinking = child.event.type === 'thinking'
        return (
          <div
            key={child.id}
            className={`py-0.5 ${
              isThinking
                ? 'font-medium text-gray-600 dark:text-gray-300'
                : 'text-gray-400 dark:text-gray-500 pl-2'
            }`}
          >
            {text}
          </div>
        )
      })}
    </div>
  )
}

/** 可折叠的步骤节点 */
function StepNode({
  node,
  allNodes,
  isStreaming,
  pendingConfirm,
  defaultExpanded,
}: {
  node: ThinkingNode
  allNodes: ThinkingNode[]
  isStreaming: boolean
  pendingConfirm: ToolConfirmEvent | null
  defaultExpanded: boolean
}) {
  const [expanded, setExpanded] = useState(defaultExpanded)
  const children = node.children || []
  const hasChildren = children.length > 0
  const icon = stepStatusIcon(node, allNodes)
  const text = formatEvent(node)

  // 当前正在执行的步骤自动展开
  useEffect(() => {
    if (defaultExpanded) setExpanded(true)
  }, [defaultExpanded])

  return (
    <div className="mt-1">
      <button
        onClick={() => hasChildren && setExpanded(!expanded)}
        className={`w-full text-left flex items-center gap-1 py-0.5 font-medium text-indigo-600 dark:text-indigo-400 ${
          hasChildren ? 'cursor-pointer hover:text-indigo-800 dark:hover:text-indigo-300' : 'cursor-default'
        }`}
      >
        <span className="flex-shrink-0">{icon}</span>
        <span className="flex-1 truncate">{text}</span>
        {hasChildren && (
          <svg
            className={`w-3 h-3 flex-shrink-0 transition-transform ${expanded ? 'rotate-180' : ''}`}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </svg>
        )}
      </button>
      {expanded && hasChildren && (
        <StepChildren children={children} isStreaming={isStreaming} pendingConfirm={pendingConfirm} />
      )}
    </div>
  )
}

export default function ThinkingPanel({ nodes, isStreaming = false, defaultExpanded }: Props) {
  const [expanded, setExpanded] = useState(defaultExpanded ?? isStreaming)
  const pendingConfirm = useChatStore((s) => s.pendingConfirm)
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (expanded && isStreaming) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
    }
  }, [nodes.length, expanded, isStreaming])

  if (nodes.length === 0 && !isStreaming) return null

  // 检测 Plan 模式
  const planNode = nodes.find((n) => n.event.type === 'plan_created')
  const isPlanMode = !!planNode

  // 统计信息
  const stepsCompleted = nodes.filter((n) => n.event.type === 'step_done').length
  const allChildNodes = isPlanMode
    ? nodes.flatMap((n) => n.children || [])
    : []
  const iterations = isPlanMode
    ? allChildNodes.filter((n) => n.event.type === 'thinking').length
    : nodes.filter((n) => n.event.type === 'thinking').length
  const toolCalls = isPlanMode
    ? allChildNodes.filter((n) => n.event.type === 'tool_call').length
    : nodes.filter((n) => n.event.type === 'tool_call').length
  const parallelCalls = isPlanMode
    ? allChildNodes.filter((n) => n.event.type === 'tool_call' && n.event.parallel_total > 1).length
    : nodes.filter((n) => n.event.type === 'tool_call' && n.event.parallel_total > 1).length

  let summary: string
  if (isPlanMode) {
    const totalSteps = planNode.event.type === 'plan_created' ? planNode.event.total_steps : 0
    summary = `📋 计划执行 (${stepsCompleted}/${totalSteps} 步完成`
    if (toolCalls) summary += `, ${toolCalls} 次工具调用`
    summary += ')'
  } else {
    summary = `💭 思考过程 (${iterations} 轮迭代`
    if (toolCalls) {
      summary += `, ${toolCalls} 次工具调用`
      if (parallelCalls) summary += `, 含 ${parallelCalls} 次并发`
    }
    summary += ')'
  }

  // 找到最后一个正在执行的步骤索引（用于自动展开当前步骤）
  const lastRunningStepIndex = (() => {
    for (let i = nodes.length - 1; i >= 0; i--) {
      const evt = nodes[i].event
      if (evt.type === 'step_start') {
        const stepIdx = evt.step_index
        const hasDone = nodes.some(
          (n) => n.event.type === 'step_done' && n.event.step_index === stepIdx,
        )
        if (!hasDone) return stepIdx
      }
    }
    return -1
  })()

  return (
    <div
      className={`border border-gray-200/60 dark:border-gray-700/60 rounded-lg overflow-hidden ${
        isStreaming ? 'bg-blue-50/30 dark:bg-blue-950/20' : 'bg-gray-50/50 dark:bg-gray-900/50'
      }`}
    >
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between px-3 py-2 text-xs text-gray-500 dark:text-gray-400 hover:bg-gray-100/50 dark:hover:bg-gray-800/50 transition-colors"
      >
        <span className="flex items-center gap-1.5">
          {isStreaming && (
            <span className="inline-block w-1.5 h-1.5 bg-blue-500 rounded-full animate-pulse" />
          )}
          {summary}
        </span>
        <svg
          className={`w-3.5 h-3.5 transition-transform ${expanded ? 'rotate-180' : ''}`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {expanded && (
        <div className="px-3 pb-2 space-y-0.5 text-xs font-mono text-gray-500 dark:text-gray-400 max-h-[60vh] overflow-y-auto border-t border-gray-200/40 dark:border-gray-700/40">
          {isPlanMode ? (
            /* ---- Plan 模式：树形分组展示 ---- */
            <>
              {nodes.map((node) => {
                // plan_created: 计划标题
                if (node.event.type === 'plan_created') {
                  return (
                    <div
                      key={node.id}
                      className="py-0.5 font-medium text-blue-600 dark:text-blue-400 mt-1 first:mt-0.5"
                    >
                      {formatEvent(node)}
                    </div>
                  )
                }

                // step_start: 可折叠步骤节点
                if (node.event.type === 'step_start') {
                  const stepIdx = node.event.step_index
                  const isCurrentStep = stepIdx === lastRunningStepIndex
                  return (
                    <StepNode
                      key={node.id}
                      node={node}
                      allNodes={nodes}
                      isStreaming={isStreaming}
                      pendingConfirm={pendingConfirm}
                      defaultExpanded={isCurrentStep}
                    />
                  )
                }

                // step_done: 在步骤节点末尾作为状态标记，不单独渲染
                if (node.event.type === 'step_done') {
                  return null
                }

                // replan: 重新规划提示
                if (node.event.type === 'replan') {
                  return (
                    <div
                      key={node.id}
                      className="py-0.5 font-medium text-amber-600 dark:text-amber-400 mt-1"
                    >
                      {formatEvent(node)}
                    </div>
                  )
                }

                // 其他顶层事件（如 answering）
                const text = formatEvent(node)
                if (!text) return null
                return (
                  <div key={node.id} className="py-0.5 text-gray-400 dark:text-gray-500 pl-2">
                    {text}
                  </div>
                )
              })}
            </>
          ) : (
            /* ---- ReAct 模式：扁平展示 ---- */
            <>
              {nodes.map((node) => {
                if (node.event.type === 'tool_confirm') {
                  const isPending = isStreaming && pendingConfirm?.confirm_id === node.event.confirm_id
                  if (isPending) {
                    return <ConfirmCard key={node.id} event={node.event} />
                  }
                  return (
                    <div key={node.id} className="py-0.5 pl-3 text-amber-500 dark:text-amber-400">
                      ⚠️ 已请求确认: {node.event.tool_name}
                    </div>
                  )
                }

                const text = formatEvent(node)
                if (!text) return null
                const isThinking = node.event.type === 'thinking'
                return (
                  <div
                    key={node.id}
                    className={`py-0.5 ${
                      isThinking
                        ? 'font-medium text-gray-600 dark:text-gray-300 mt-1.5 first:mt-1'
                        : 'pl-4 text-gray-400 dark:text-gray-500'
                    }`}
                  >
                    {text}
                  </div>
                )
              })}
            </>
          )}
          {isStreaming && !pendingConfirm && (
            <div className="py-0.5 pl-3 text-blue-500 animate-pulse">⏳ 思考中...</div>
          )}
          {isStreaming && pendingConfirm && (
            <div className="py-0.5 pl-3 text-amber-500 animate-pulse">⏳ 等待确认...</div>
          )}
          <div ref={bottomRef} />
        </div>
      )}
    </div>
  )
}
