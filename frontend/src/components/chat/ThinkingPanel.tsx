/**
 * 思考过程面板
 *
 * 以折叠面板展示 Agent 的推理过程（THINKING / TOOL_CALL / TOOL_RESULT 等事件）。
 *
 * 核心组件：
 * - ToolCard：工具调用 + 结果 的结构化卡片（配对渲染）
 * - TOOL_META：工具图标 + 配色映射表
 * - IterationDivider：轮次分隔线
 * - PlanProgressBar：Plan 模式步骤进度条
 * - 摘要标题栏：mini 进度条 + 紧凑统计
 *
 * Plan 模式下：
 *   - 步骤节点（step_start）作为分组标题，子循环事件嵌套在 children 中
 *   - 步骤节点可独立折叠/展开
 *   - 完成的步骤默认收起，当前步骤默认展开
 */
import React, { useEffect, useRef, useState } from 'react'
import type { ThinkingNode, ToolConfirmEvent } from '../../types'
import type { ToolCallEvent, ToolResultEvent, PlanCreatedEvent, StepDoneEvent } from '../../types/events'
import { useChatStore } from '../../stores/chatStore'

// ── 工具元数据映射表 ──

interface ToolMeta {
  icon: React.ReactNode
  color: string
  label: string
}

const TOOL_META: Record<string, ToolMeta> = {
  execute_command: {
    icon: (
      <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M8 9l3 3-3 3m5 0h3M5 20h14a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
      </svg>
    ),
    color: 'text-blue-500',
    label: '命令执行',
  },
  kubectl: {
    icon: (
      <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M8 9l3 3-3 3m5 0h3M5 20h14a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
      </svg>
    ),
    color: 'text-blue-500',
    label: 'Kubernetes',
  },
  docker: {
    icon: (
      <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4" />
      </svg>
    ),
    color: 'text-sky-500',
    label: 'Docker',
  },
  curl: {
    icon: (
      <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M21 12a9 9 0 01-9 9m9-9a9 9 0 00-9-9m9 9H3m9 9a9 9 0 01-9-9m9 9c1.657 0 3-4.03 3-9s-1.343-9-3-9m0 18c-1.657 0-3-4.03-3-9s1.343-9 3-9m-9 9a9 9 0 019-9" />
      </svg>
    ),
    color: 'text-purple-500',
    label: 'HTTP 请求',
  },
  file_reader: {
    icon: (
      <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
      </svg>
    ),
    color: 'text-emerald-500',
    label: '文件读取',
  },
  file_writer: {
    icon: (
      <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
      </svg>
    ),
    color: 'text-orange-500',
    label: '文件写入',
  },
  web_search: {
    icon: (
      <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
      </svg>
    ),
    color: 'text-cyan-500',
    label: '网页搜索',
  },
  calculator: {
    icon: (
      <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M9 7h6m0 10v-3m-3 3h.01M9 17h.01M9 14h.01M12 14h.01M15 11h.01M12 11h.01M9 11h.01M7 21h10a2 2 0 002-2V5a2 2 0 00-2-2H7a2 2 0 00-2 2v14a2 2 0 002 2z" />
      </svg>
    ),
    color: 'text-gray-500',
    label: '计算器',
  },
  knowledge_search: {
    icon: (
      <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
      </svg>
    ),
    color: 'text-indigo-500',
    label: '知识库',
  },
  get_current_time: {
    icon: (
      <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
    ),
    color: 'text-gray-400',
    label: '获取时间',
  },
}

const DEFAULT_TOOL_META: ToolMeta = {
  icon: (
    <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
      <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
    </svg>
  ),
  color: 'text-gray-400',
  label: '工具',
}

/** MCP 外部工具的默认图标（插头 icon + 紫色） */
const MCP_TOOL_ICON = (
  <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
    <path strokeLinecap="round" strokeLinejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z" />
  </svg>
)

/**
 * 解析 MCP 工具名称：mcp__{server}__{tool}
 * 返回 { server, tool } 或 null（非 MCP 工具）
 */
function parseMCPToolName(name: string): { server: string; tool: string } | null {
  if (!name.startsWith('mcp__')) return null
  const rest = name.slice(5) // 去掉 "mcp__"
  const sepIdx = rest.indexOf('__')
  if (sepIdx < 0) return null
  return { server: rest.slice(0, sepIdx), tool: rest.slice(sepIdx + 2) }
}

function getToolMeta(name: string): ToolMeta {
  // 内置工具直接查表
  if (TOOL_META[name]) return TOOL_META[name]

  // MCP 外部工具：动态生成 meta
  const mcp = parseMCPToolName(name)
  if (mcp) {
    return {
      icon: MCP_TOOL_ICON,
      color: 'text-violet-500',
      label: `MCP:${mcp.server}`,
    }
  }

  return DEFAULT_TOOL_META
}

/** 从工具参数中提取有意义的标题文本 */
function getToolTitle(toolName: string, args: Record<string, unknown>): string {
  // MCP 工具：展示 server:tool + 首参数预览
  const mcp = parseMCPToolName(toolName)
  if (mcp) {
    const entries = Object.entries(args)
    if (entries.length > 0) {
      const val = typeof entries[0][1] === 'string' ? entries[0][1] : JSON.stringify(entries[0][1])
      const preview = val.length > 80 ? val.slice(0, 80) + '...' : val
      return `${mcp.tool} → ${preview}`
    }
    return mcp.tool
  }

  switch (toolName) {
    case 'execute_command':
      return (args.command as string) || toolName
    case 'curl':
      return `${(args.method as string) || 'GET'} ${(args.url as string) || ''}`.trim()
    case 'file_reader':
      return (args.path as string) || toolName
    case 'file_writer':
      return (args.path as string) || toolName
    case 'web_search':
      return (args.query as string) || toolName
    case 'knowledge_search':
      return (args.query as string) || toolName
    default: {
      const entries = Object.entries(args)
      if (entries.length === 1) {
        const val = typeof entries[0][1] === 'string' ? entries[0][1] : JSON.stringify(entries[0][1])
        return val || toolName
      }
      return toolName
    }
  }
}

/** 返回已在标题中展示的参数 key 集合，展开时跳过这些 key */
function getTitleKeys(toolName: string, args: Record<string, unknown>): Set<string> {
  switch (toolName) {
    case 'execute_command': return new Set(['command'])
    case 'curl': return new Set(['method', 'url'])
    case 'file_reader': return new Set(['path'])
    case 'file_writer': return new Set(['path'])
    case 'web_search': return new Set(['query'])
    case 'knowledge_search': return new Set(['query'])
    default: {
      const entries = Object.entries(args)
      if (entries.length === 1) return new Set([entries[0][0]])
      return new Set()
    }
  }
}

// ── 工具调用卡片 ──

function ToolCard({
  callEvent,
  resultEvent,
}: {
  callEvent: ToolCallEvent
  resultEvent?: ToolResultEvent
}) {
  const [expanded, setExpanded] = useState(false)
  const [copied, setCopied] = useState(false)
  const meta = getToolMeta(callEvent.tool_name)
  const mcpInfo = parseMCPToolName(callEvent.tool_name)
  const isRunning = !resultEvent
  const isSuccess = resultEvent?.success ?? false
  const duration = resultEvent ? (resultEvent.duration_ms / 1000).toFixed(1) : null
  const parallel = callEvent.parallel_total > 1

  const fullTitle = getToolTitle(callEvent.tool_name, callEvent.tool_args)
  const titleKeys = getTitleKeys(callEvent.tool_name, callEvent.tool_args)
  const remainingArgs = Object.entries(callEvent.tool_args).filter(([key]) => !titleKeys.has(key))
  const hasExpandContent = remainingArgs.length > 0 || !!resultEvent

  const handleCopy = (e: React.MouseEvent) => {
    e.stopPropagation()
    navigator.clipboard.writeText(fullTitle).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    })
  }

  return (
    <div className="my-1 rounded-md border border-gray-200/70 dark:border-gray-700/50 bg-white/60 dark:bg-gray-800/40 overflow-hidden">
      {/* 标题行：图标 + 命令内容（可多行） + 复制 + 并发标记 + 状态 + 折叠箭头 */}
      <button
        onClick={() => hasExpandContent && setExpanded(!expanded)}
        className={`w-full text-left flex items-start gap-1.5 px-2.5 py-1.5 ${
          hasExpandContent ? 'cursor-pointer hover:bg-gray-50/80 dark:hover:bg-gray-700/30' : 'cursor-default'
        } transition-colors`}
      >
        <span className={`${meta.color} flex-shrink-0 mt-0.5`}>{meta.icon}</span>
        <span
          className="flex-1 min-w-0 font-medium text-gray-700 dark:text-gray-200 text-[11px] font-mono line-clamp-2 break-all"
          title={fullTitle}
        >
          {fullTitle}
        </span>
        <span className="flex items-center gap-1 flex-shrink-0 text-[11px] mt-0.5">
          <span
            onClick={handleCopy}
            className="p-0.5 rounded hover:bg-gray-200/60 dark:hover:bg-gray-600/40 transition-colors cursor-pointer"
            title="复制命令"
          >
            {copied ? (
              <svg className="w-3 h-3 text-emerald-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
              </svg>
            ) : (
              <svg className="w-3 h-3 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
              </svg>
            )}
          </span>
          {mcpInfo && (
            <span className="text-[9px] px-1 py-0.5 rounded bg-violet-100 dark:bg-violet-900/40 text-violet-600 dark:text-violet-400 font-medium">
              ⚡{mcpInfo.server}
            </span>
          )}
          {parallel && (
            <span className="text-[9px] px-1 py-0.5 rounded bg-violet-100 dark:bg-violet-900/40 text-violet-600 dark:text-violet-400 font-medium">
              ⚡{callEvent.parallel_index}/{callEvent.parallel_total}
            </span>
          )}
          {isRunning ? (
            <span className="flex items-center gap-1 text-blue-500">
              <svg className="w-3 h-3 animate-spin" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
              </svg>
            </span>
          ) : isSuccess ? (
            <span className="text-emerald-500">{duration}s ✓</span>
          ) : (
            <span className="text-red-500">{duration}s ✗</span>
          )}
          {hasExpandContent && (
            <svg
              className={`w-3 h-3 text-gray-400 transition-transform ${expanded ? 'rotate-180' : ''}`}
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
            >
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
            </svg>
          )}
        </span>
      </button>

      {/* 展开区：剩余参数 + 结果 */}
      {expanded && (
        <div className="border-t border-gray-100 dark:border-gray-700/40">
          {remainingArgs.length > 0 && (
            <div className="mx-2 mt-1.5 mb-1 px-2 py-1.5 rounded bg-gray-50 dark:bg-gray-900/50 text-[10.5px] font-mono text-gray-500 dark:text-gray-400 space-y-0.5">
              {remainingArgs.map(([key, val]) => {
                let valStr = typeof val === 'string' ? val : JSON.stringify(val)
                if (valStr.length > 120) valStr = valStr.slice(0, 120) + '...'
                return (
                  <div key={key} className="flex gap-1.5">
                    <span className="text-gray-400 dark:text-gray-500 flex-shrink-0">{key}:</span>
                    <span className="text-gray-600 dark:text-gray-300 break-all">{valStr}</span>
                  </div>
                )
              })}
            </div>
          )}
          {resultEvent && (
            <pre className="mx-2 my-1.5 px-2 py-1.5 rounded bg-gray-50 dark:bg-gray-900/60 text-[10px] font-mono text-gray-600 dark:text-gray-300 max-h-48 overflow-y-auto whitespace-pre-wrap break-all">
              {resultEvent.tool_result_preview || '(无输出)'}
            </pre>
          )}
        </div>
      )}
    </div>
  )
}

// ── 迭代轮次分隔线 ──

function IterationDivider({ iteration }: { iteration: number }) {
  return (
    <div className="flex items-center gap-2 my-1.5">
      <div className="flex-1 border-t border-gray-200/50 dark:border-gray-700/40" />
      <span className="text-[10px] text-gray-400 dark:text-gray-500 flex-shrink-0 font-medium">
        第 {iteration} 轮
      </span>
      <div className="flex-1 border-t border-gray-200/50 dark:border-gray-700/40" />
    </div>
  )
}

// ── Plan 模式步骤进度条 ──

function PlanProgressBar({
  steps,
  stepDoneNodes,
  currentStepIndex,
}: {
  steps: { id: string; description: string }[]
  stepDoneNodes: ThinkingNode[]
  currentStepIndex: number
}) {
  const getStepStatus = (index: number): 'completed' | 'running' | 'failed' | 'pending' | 'skipped' => {
    const done = stepDoneNodes.find(
      (n) => n.event.type === 'step_done' && n.event.step_index === index,
    )
    if (done && done.event.type === 'step_done') {
      const s = done.event.step_status
      if (s === 'completed') return 'completed'
      if (s === 'failed') return 'failed'
      if (s === 'skipped') return 'skipped'
    }
    if (index === currentStepIndex) return 'running'
    return 'pending'
  }

  const dotStyle: Record<string, string> = {
    completed: 'bg-emerald-500',
    running: 'bg-blue-500 animate-pulse',
    failed: 'bg-red-500',
    pending: 'bg-gray-300 dark:bg-gray-600',
    skipped: 'bg-gray-300 dark:bg-gray-600 opacity-50',
  }

  return (
    <div className="my-2 px-1">
      {/* 圆点连线 */}
      <div className="flex items-center">
        {steps.map((step, i) => (
          <div key={step.id} className="flex items-center flex-1 last:flex-initial">
            <div
              className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${dotStyle[getStepStatus(i)]}`}
              title={step.description}
            />
            {i < steps.length - 1 && (
              <div
                className={`flex-1 h-[1.5px] mx-0.5 ${
                  getStepStatus(i) === 'completed'
                    ? 'bg-emerald-400 dark:bg-emerald-600'
                    : 'bg-gray-200 dark:bg-gray-700'
                }`}
              />
            )}
          </div>
        ))}
      </div>
      {/* 步骤简称 */}
      <div className="flex mt-0.5">
        {steps.map((step, i) => (
          <div key={step.id} className="flex-1 last:flex-initial">
            <span
              className={`text-[9px] leading-tight ${
                getStepStatus(i) === 'running'
                  ? 'text-blue-500 font-medium'
                  : getStepStatus(i) === 'completed'
                    ? 'text-emerald-500'
                    : 'text-gray-400 dark:text-gray-500'
              }`}
            >
              {step.description.slice(0, 6)}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── 工具执行确认卡片 ──

function ConfirmCard({ event }: { event: ToolConfirmEvent }) {
  const handleConfirm = useChatStore((s) => s.handleConfirm)
  const [isLoading, setIsLoading] = useState(false)

  const handleClick = async (approved: boolean) => {
    setIsLoading(true)
    await handleConfirm(event.confirm_id, approved)
  }

  const meta = getToolMeta(event.tool_name)
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
        <div className="flex items-center gap-1.5">
          <span className="text-gray-400 dark:text-gray-500">工具: </span>
          <span className={meta.color}>{meta.icon}</span>
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

// ── 渲染事件列表（工具卡片化 + 迭代分隔线） ──

function EventList({
  nodes,
  isStreaming,
  pendingConfirm,
}: {
  nodes: ThinkingNode[]
  isStreaming: boolean
  pendingConfirm: ToolConfirmEvent | null
}) {
  const elements: React.ReactElement[] = []
  let i = 0

  while (i < nodes.length) {
    const node = nodes[i]
    const e = node.event

    // 迭代轮次 → 分隔线
    if (e.type === 'thinking') {
      elements.push(<IterationDivider key={node.id} iteration={e.iteration} />)
      i++
      continue
    }

    // 工具确认卡片
    if (e.type === 'tool_confirm') {
      const isPending = isStreaming && pendingConfirm?.confirm_id === e.confirm_id
      if (isPending) {
        elements.push(<ConfirmCard key={node.id} event={e} />)
      } else {
        elements.push(
          <div key={node.id} className="py-0.5 text-amber-500 dark:text-amber-400 text-[11px]">
            ⚠️ 已请求确认: {e.tool_name}
          </div>,
        )
      }
      i++
      continue
    }

    // 工具调用 → 查找配对的 tool_result → 合并为 ToolCard
    if (e.type === 'tool_call') {
      const callEvent = e as ToolCallEvent
      // 向后查找对应的 tool_result（同名工具，parallel_index 匹配）
      let resultEvent: ToolResultEvent | undefined
      for (let j = i + 1; j < nodes.length; j++) {
        const candidate = nodes[j].event
        if (
          candidate.type === 'tool_result' &&
          candidate.tool_name === callEvent.tool_name &&
          candidate.parallel_index === callEvent.parallel_index
        ) {
          resultEvent = candidate as ToolResultEvent
          break
        }
      }
      elements.push(
        <ToolCard key={node.id} callEvent={callEvent} resultEvent={resultEvent} />,
      )
      i++
      continue
    }

    // tool_result 单独出现时跳过（已在 ToolCard 中配对渲染）
    if (e.type === 'tool_result') {
      i++
      continue
    }

    // answering
    if (e.type === 'answering') {
      elements.push(
        <div key={node.id} className="py-0.5 text-blue-500 text-[11px]">
          💡 正在生成回答...
        </div>,
      )
      i++
      continue
    }

    // max_iterations
    if (e.type === 'max_iterations') {
      elements.push(
        <div key={node.id} className="py-0.5 text-amber-500 text-[11px]">
          ⚠️ {e.message}
        </div>,
      )
      i++
      continue
    }

    // error
    if (e.type === 'error') {
      elements.push(
        <div key={node.id} className="py-0.5 text-red-500 text-[11px]">
          ❌ {e.message}
        </div>,
      )
      i++
      continue
    }

    i++
  }

  return <>{elements}</>
}

// ── 步骤状态图标 ──

function stepStatusIcon(node: ThinkingNode, allNodes: ThinkingNode[]): React.ReactNode {
  if (node.event.type !== 'step_start') return <span>○</span>
  const stepIndex = node.event.step_index
  const doneNode = allNodes.find(
    (n) => n.event.type === 'step_done' && n.event.step_index === stepIndex,
  )
  if (!doneNode) {
    return (
      <span className="inline-block w-3 h-3">
        <svg className="w-3 h-3 animate-spin text-blue-500" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
        </svg>
      </span>
    )
  }
  const status = doneNode.event.type === 'step_done' ? (doneNode.event as StepDoneEvent).step_status : ''
  if (status === 'completed') return <span className="text-emerald-500">✓</span>
  if (status === 'failed') return <span className="text-red-500">✗</span>
  if (status === 'skipped') return <span className="text-gray-400">⏭</span>
  return (
    <span className="inline-block w-3 h-3">
      <svg className="w-3 h-3 animate-spin text-blue-500" fill="none" viewBox="0 0 24 24">
        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
      </svg>
    </span>
  )
}

// ── 可折叠的步骤节点 ──

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

  const e = node.event
  const text = e.type === 'step_start' ? `步骤 ${e.step_index + 1}/${e.total_steps}: ${e.message}` : ''

  useEffect(() => {
    if (defaultExpanded) setExpanded(true)
  }, [defaultExpanded])

  return (
    <div className="mt-1">
      <button
        onClick={() => hasChildren && setExpanded(!expanded)}
        className={`w-full text-left flex items-center gap-1.5 py-0.5 font-medium text-[11px] text-indigo-600 dark:text-indigo-400 ${
          hasChildren ? 'cursor-pointer hover:text-indigo-800 dark:hover:text-indigo-300' : 'cursor-default'
        }`}
      >
        <span className="flex-shrink-0 w-3.5 text-center">{icon}</span>
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
        <div className="pl-4 border-l border-gray-200/40 dark:border-gray-700/40 ml-1.5 mt-0.5">
          <EventList nodes={children} isStreaming={isStreaming} pendingConfirm={pendingConfirm} />
        </div>
      )}
    </div>
  )
}

// ── 主面板 Props ──

interface Props {
  nodes: ThinkingNode[]
  isStreaming?: boolean
  defaultExpanded?: boolean
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
  const stepDoneNodes = nodes.filter((n) => n.event.type === 'step_done')
  const stepsCompleted = stepDoneNodes.length
  const allChildNodes = isPlanMode ? nodes.flatMap((n) => n.children || []) : []
  const toolCalls = isPlanMode
    ? allChildNodes.filter((n) => n.event.type === 'tool_call').length
    : nodes.filter((n) => n.event.type === 'tool_call').length
  const iterations = isPlanMode
    ? allChildNodes.filter((n) => n.event.type === 'thinking').length
    : nodes.filter((n) => n.event.type === 'thinking').length

  // 计算总耗时（从第一个到最后一个 tool_result 的 duration 之和）
  const allToolResults = (isPlanMode ? allChildNodes : nodes).filter(
    (n) => n.event.type === 'tool_result',
  )
  const totalDurationMs = allToolResults.reduce(
    (sum, n) => sum + (n.event.type === 'tool_result' ? n.event.duration_ms : 0),
    0,
  )

  // Plan 模式进度数据
  let totalSteps = 0
  let planSteps: { id: string; description: string }[] = []
  let planGoal = ''
  if (isPlanMode && planNode.event.type === 'plan_created') {
    const pe = planNode.event as PlanCreatedEvent
    totalSteps = pe.total_steps
    planGoal = pe.plan.goal
    planSteps = pe.plan.steps.map((s) => ({ id: s.id, description: s.description }))
  }

  const progressPercent = totalSteps > 0 ? Math.round((stepsCompleted / totalSteps) * 100) : 0

  // 找到最后一个正在执行的步骤索引
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

  // 摘要文本
  const summaryParts: string[] = []
  if (isPlanMode) {
    summaryParts.push(`${stepsCompleted}/${totalSteps} 步`)
  } else {
    summaryParts.push(`${iterations} 轮`)
  }
  if (toolCalls) summaryParts.push(`${toolCalls} 次工具调用`)
  if (totalDurationMs > 0) summaryParts.push(`${(totalDurationMs / 1000).toFixed(1)}s`)

  return (
    <div
      className={`border border-gray-200/60 dark:border-gray-700/60 rounded-lg overflow-hidden ${
        isStreaming ? 'bg-blue-50/30 dark:bg-blue-950/20' : 'bg-gray-50/50 dark:bg-gray-900/50'
      }`}
    >
      {/* 标题栏 */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full text-left hover:bg-gray-100/50 dark:hover:bg-gray-800/50 transition-colors"
      >
        <div className="flex items-center justify-between px-3 py-2 text-xs text-gray-500 dark:text-gray-400">
          <span className="flex items-center gap-1.5">
            {isStreaming && (
              <span className="inline-block w-1.5 h-1.5 bg-blue-500 rounded-full animate-pulse" />
            )}
            <span>{isPlanMode ? '📋 计划执行' : '💭 思考过程'}</span>
            <span className="text-gray-400 dark:text-gray-500">───</span>
            <span>{summaryParts.join(' · ')}</span>
          </span>
          <svg
            className={`w-3.5 h-3.5 transition-transform ${expanded ? 'rotate-180' : ''}`}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </svg>
        </div>
        {/* Mini 进度条（Plan 模式下展示） */}
        {isPlanMode && (
          <div className="h-[2px] bg-gray-200/60 dark:bg-gray-700/60">
            <div
              className="h-full bg-emerald-500 transition-all duration-500"
              style={{ width: `${progressPercent}%` }}
            />
          </div>
        )}
      </button>

      {/* 展开内容 */}
      {expanded && (
        <div className="px-3 pb-2 text-xs font-mono text-gray-500 dark:text-gray-400 max-h-[60vh] overflow-y-auto border-t border-gray-200/40 dark:border-gray-700/40">
          {isPlanMode ? (
            <>
              {/* Plan 目标 */}
              {planGoal && (
                <div className="py-1.5 text-[11px] font-medium text-blue-600 dark:text-blue-400">
                  🎯 {planGoal}
                </div>
              )}

              {/* 步骤进度条 */}
              {planSteps.length > 0 && (
                <PlanProgressBar
                  steps={planSteps}
                  stepDoneNodes={stepDoneNodes}
                  currentStepIndex={lastRunningStepIndex}
                />
              )}

              {/* 步骤列表 */}
              {nodes.map((node) => {
                if (node.event.type === 'plan_created') return null
                if (node.event.type === 'step_done') return null

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

                if (node.event.type === 'replan') {
                  return (
                    <div
                      key={node.id}
                      className="py-1 font-medium text-amber-600 dark:text-amber-400 text-[11px] mt-1"
                    >
                      🔄 {node.event.message}
                    </div>
                  )
                }

                // 其他顶层事件
                if (node.event.type === 'answering') {
                  return (
                    <div key={node.id} className="py-0.5 text-blue-500 text-[11px]">
                      💡 正在生成回答...
                    </div>
                  )
                }

                return null
              })}
            </>
          ) : (
            <EventList nodes={nodes} isStreaming={isStreaming} pendingConfirm={pendingConfirm} />
          )}

          {isStreaming && !pendingConfirm && (
            <div className="py-1 text-blue-500 animate-pulse text-[11px]">⏳ 思考中...</div>
          )}
          {isStreaming && pendingConfirm && (
            <div className="py-1 text-amber-500 animate-pulse text-[11px]">⏳ 等待确认...</div>
          )}
          <div ref={bottomRef} />
        </div>
      )}
    </div>
  )
}
