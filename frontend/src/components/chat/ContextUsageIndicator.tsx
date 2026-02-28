/**
 * 上下文用量指示器 — 微型仪表盘
 *
 * 位于对话区域右上角，SVG 圆环 + 紧凑数字展示上下文占用率。
 * Hover 弹出 Zone 详情卡片，含分段进度条和各 Zone 用量表。
 * 颜色随占用率变化：翡翠绿(< 60%) → 琥珀(60-80%) → 红色(> 80%)。
 */
import { useState, useRef, useEffect } from 'react'
import { useChatStore } from '../../stores/chatStore'
import type { ZoneBreakdown } from '../../types/conversation'

// ── 工具函数 ──

function formatTokens(n: number): string {
  if (n >= 100_000) return `${(n / 1000).toFixed(0)}k`
  if (n >= 1_000) return `${(n / 1000).toFixed(1)}k`
  return String(n)
}

function formatFull(n: number): string {
  return n.toLocaleString('en-US')
}

// ── 颜色系统 ──

interface ColorSet {
  ring: string      // SVG stroke 色
  text: string      // 数字色 (Tailwind)
  glow: string      // 光晕色 (CSS)
  trackBg: string   // 进度条轨道
}

function getColors(ratio: number): ColorSet {
  if (ratio >= 0.8) return {
    ring: '#ef4444',
    text: 'text-red-600 dark:text-red-400',
    glow: 'rgba(239,68,68,0.15)',
    trackBg: 'bg-red-500/8',
  }
  if (ratio >= 0.6) return {
    ring: '#f59e0b',
    text: 'text-amber-600 dark:text-amber-400',
    glow: 'rgba(245,158,11,0.12)',
    trackBg: 'bg-amber-500/8',
  }
  return {
    ring: '#10b981',
    text: 'text-emerald-600 dark:text-emerald-400',
    glow: 'rgba(16,185,129,0.12)',
    trackBg: 'bg-emerald-500/8',
  }
}

// ── Zone 配置 ──

interface ZoneConfig {
  key: keyof ZoneBreakdown
  label: string
  color: string       // Tailwind bg 色
  hex: string         // SVG / 渐变用
  budgetKey?: keyof ZoneBreakdown
  truncatedKey?: keyof ZoneBreakdown
}

const ZONES: ZoneConfig[] = [
  { key: 'system_tokens', label: 'System', color: 'bg-slate-400', hex: '#94a3b8' },
  { key: 'environment_tokens', label: 'Env', color: 'bg-gray-500', hex: '#6b7280' },
  { key: 'skill_tokens', label: 'Skill', color: 'bg-violet-400', hex: '#a78bfa', budgetKey: 'skill_budget', truncatedKey: 'skill_truncated' },
  { key: 'knowledge_tokens', label: 'KB', color: 'bg-blue-400', hex: '#60a5fa', budgetKey: 'knowledge_budget', truncatedKey: 'knowledge_truncated' },
  { key: 'memory_tokens', label: 'Memory', color: 'bg-cyan-400', hex: '#22d3ee', budgetKey: 'memory_budget', truncatedKey: 'memory_truncated' },
  { key: 'history_tokens', label: 'History', color: 'bg-emerald-400', hex: '#34d399' },
]

// ── SVG 圆环组件 ──

function RingGauge({ ratio, color, size = 26 }: { ratio: number; color: string; size?: number }) {
  const strokeWidth = 2.5
  const radius = (size - strokeWidth) / 2
  const circumference = 2 * Math.PI * radius
  const offset = circumference * (1 - Math.min(ratio, 1))

  return (
    <svg width={size} height={size} className="flex-shrink-0 -rotate-90">
      {/* 轨道 */}
      <circle
        cx={size / 2} cy={size / 2} r={radius}
        fill="none"
        stroke="currentColor"
        className="text-gray-300/50 dark:text-gray-700/40"
        strokeWidth={strokeWidth}
      />
      {/* 进度弧 */}
      <circle
        cx={size / 2} cy={size / 2} r={radius}
        fill="none"
        stroke={color}
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeDasharray={circumference}
        strokeDashoffset={offset}
        className="transition-all duration-700 ease-out"
        style={{ filter: `drop-shadow(0 0 3px ${color}40)` }}
      />
    </svg>
  )
}

// ── Zone 详情卡片 ──

function ZoneDetailsCard({
  breakdown,
  inputBudget,
  ratio,
  usedTokens,
  maxOutput,
  colors,
}: {
  breakdown?: ZoneBreakdown
  inputBudget: number
  ratio: number
  usedTokens: number
  maxOutput: number
  colors: ColorSet
}) {
  return (
    <div
      className="absolute right-0 bottom-full z-50 w-72 pb-1"
      style={{ animation: 'fadeSlideIn 150ms ease-out' }}
    >
      <div className="rounded-xl border border-gray-200 dark:border-gray-700/60 bg-white dark:bg-gray-900/95 backdrop-blur-xl shadow-2xl shadow-black/10 dark:shadow-black/40 overflow-hidden">
        {/* 顶部总览 */}
        <div className="px-4 pt-3.5 pb-3">
          <div className="flex items-center justify-between mb-2.5">
            <span className="text-[11px] font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">Context Usage</span>
            <span className={`text-xs font-semibold tabular-nums ${colors.text}`}>
              {Math.round(ratio * 100)}%
            </span>
          </div>

          {/* 分段进度条 */}
          <div className="relative h-2 rounded-full bg-gray-200 dark:bg-gray-800 overflow-hidden">
            <div className="absolute inset-0 flex">
              {breakdown ? (
                ZONES.map(({ key, hex }) => {
                  const tokens = breakdown[key] as number
                  const pct = inputBudget > 0 ? (tokens / inputBudget) * 100 : 0
                  if (pct < 0.2) return null
                  return (
                    <div
                      key={key}
                      className="h-full transition-all duration-500"
                      style={{ width: `${Math.min(pct, 100)}%`, backgroundColor: hex }}
                    />
                  )
                })
              ) : (
                <div
                  className="h-full transition-all duration-500"
                  style={{ width: `${Math.min(ratio * 100, 100)}%`, backgroundColor: colors.ring }}
                />
              )}
            </div>
          </div>

          <div className="flex justify-between mt-1.5 text-[10px] text-gray-500 tabular-nums">
            <span>{formatFull(usedTokens)}</span>
            <span>{formatFull(inputBudget)} tokens</span>
          </div>
        </div>

        {/* 分隔线 */}
        <div className="h-px bg-gray-200 dark:bg-gray-700/50" />

        {breakdown ? (
          <>
            {/* Zone 列表 */}
            <div className="px-4 py-2.5 space-y-1">
              {ZONES.map(({ key, label, hex, budgetKey, truncatedKey }) => {
                const tokens = breakdown[key] as number
                const budget = budgetKey ? (breakdown[budgetKey] as number) : 0
                const truncated = truncatedKey ? (breakdown[truncatedKey] as boolean) : false

                if (tokens === 0 && budget === 0) return null

                const zonePct = inputBudget > 0 ? (tokens / inputBudget) * 100 : 0
                const barWidth = budget > 0
                  ? Math.min((tokens / budget) * 100, 100)
                  : Math.min(zonePct * 5, 100)

                return (
                  <div key={key} className="group">
                    <div className="flex items-center gap-2 h-5">
                      <div
                        className="w-1.5 h-1.5 rounded-full flex-shrink-0"
                        style={{ backgroundColor: hex }}
                      />
                      <span className="text-[11px] text-gray-500 dark:text-gray-400 w-12 flex-shrink-0">{label}</span>
                      <div className="flex-1 h-1 rounded-full bg-gray-200 dark:bg-gray-800 overflow-hidden">
                        <div
                          className="h-full rounded-full transition-all duration-500"
                          style={{ width: `${barWidth}%`, backgroundColor: hex, opacity: 0.7 }}
                        />
                      </div>
                      <span className="text-[11px] text-gray-700 dark:text-gray-300 font-mono tabular-nums w-10 text-right">
                        {formatTokens(tokens)}
                      </span>
                      {budget > 0 && (
                        <span className="text-[10px] text-gray-400 dark:text-gray-600 font-mono tabular-nums">
                          /{formatTokens(budget)}
                        </span>
                      )}
                      {truncated && (
                        <span className="text-amber-500 text-[10px]" title="已截断以适应预算">⚠</span>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          </>
        ) : (
          <div className="px-4 py-3 text-[11px] text-gray-500 text-center">
            发送消息后展示 Zone 分布详情
          </div>
        )}

        {/* 底部信息 */}
        <div className="h-px bg-gray-200 dark:bg-gray-700/50" />
        <div className="px-4 py-2 flex justify-between text-[10px] text-gray-500">
          <span>输出预留 {formatTokens(maxOutput)}</span>
          <span>压缩 {breakdown ? '可用' : '—'}</span>
        </div>
      </div>

      {/* CSS 动画 */}
      <style>{`
        @keyframes fadeSlideIn {
          from { opacity: 0; transform: translateY(4px); }
          to { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </div>
  )
}

// ── 主组件 ──

export default function ContextUsageIndicator() {
  const status = useChatStore((s) => s.status)
  const [showDetails, setShowDetails] = useState(false)
  const hideTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  // 清理定时器
  useEffect(() => {
    return () => {
      if (hideTimer.current) clearTimeout(hideTimer.current)
    }
  }, [])

  if (!status?.initialized || !status.context_window || !status.current_conversation) {
    return null
  }

  const { context_window, max_output_tokens } = status
  const { context_used_tokens, zone_breakdown, compression_count } = status.current_conversation

  const inputBudget = context_window - max_output_tokens
  const ratio = inputBudget > 0 ? Math.min(context_used_tokens / inputBudget, 1) : 0
  const colors = getColors(ratio)

  const handleMouseEnter = () => {
    if (hideTimer.current) {
      clearTimeout(hideTimer.current)
      hideTimer.current = null
    }
    setShowDetails(true)
  }

  const handleMouseLeave = () => {
    hideTimer.current = setTimeout(() => setShowDetails(false), 150)
  }

  return (
    <div
      className="relative inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg cursor-pointer select-none transition-all duration-200 hover:bg-gray-200/60 dark:hover:bg-gray-800/60"
      style={{ backgroundColor: showDetails ? 'var(--ctx-hover-bg)' : 'transparent' }}
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
    >
      {/* 圆环 */}
      <RingGauge ratio={ratio} color={colors.ring} />

      {/* 数字 */}
      <div className="flex items-baseline gap-0.5">
        <span className={`text-[11px] font-semibold tabular-nums ${colors.text}`}>
          {formatTokens(context_used_tokens)}
        </span>
        <span className="text-[10px] text-gray-500 tabular-nums">
          /{formatTokens(inputBudget)}
        </span>
      </div>

      {/* 压缩次数标记 */}
      {compression_count > 0 && (
        <span
          className="text-[9px] px-1 py-px rounded bg-violet-500/20 text-violet-400 tabular-nums"
          title={`已压缩 ${compression_count} 次`}
        >
          ×{compression_count}
        </span>
      )}

      {/* Zone 详情弹窗 — zone_breakdown 为空时展示基础信息 */}
      {showDetails && (
        <ZoneDetailsCard
          breakdown={zone_breakdown}
          inputBudget={inputBudget}
          ratio={ratio}
          usedTokens={context_used_tokens}
          maxOutput={max_output_tokens}
          colors={colors}
        />
      )}
    </div>
  )
}
