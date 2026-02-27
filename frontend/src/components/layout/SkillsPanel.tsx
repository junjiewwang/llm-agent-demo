/**
 * 技能管理面板
 *
 * 展示所有已注册 Skill，支持启用/禁用切换 + 展开详情。
 * 嵌入 StatusPanel 内部。
 */
import { useEffect, useState } from 'react'
import { useSkillStore } from '../../stores/skillStore'
import type { SkillInfo } from '../../types/index'

export default function SkillsPanel() {
  const { skills, loading, error, fetchSkills, toggle } = useSkillStore()

  useEffect(() => {
    fetchSkills()
  }, [fetchSkills])

  return (
    <div className="mt-5">
      <h4 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-3 flex items-center gap-2">
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
        </svg>
        技能管理
        <span className="text-xs text-gray-400 font-normal">
          {loading ? '加载中...' : `${skills.filter((s) => s.enabled).length}/${skills.length}`}
        </span>
      </h4>

      {error && (
        <div className="text-xs text-red-500 mb-2 flex items-center gap-1">
          <span>⚠</span>
          <span>{error}</span>
        </div>
      )}

      {skills.length === 0 && !loading && (
        <p className="text-xs text-gray-400">暂无已注册技能</p>
      )}

      <div className="space-y-2">
        {skills.map((skill) => (
          <SkillCard key={skill.name} skill={skill} onToggle={toggle} />
        ))}
      </div>
    </div>
  )
}

function SkillCard({
  skill,
  onToggle,
}: {
  skill: SkillInfo
  onToggle: (name: string, enabled: boolean) => void
}) {
  const [expanded, setExpanded] = useState(false)
  const toolsOk = skill.tools_satisfied

  return (
    <div
      className={`rounded-lg border p-2.5 transition-colors ${
        skill.enabled
          ? 'border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800'
          : 'border-gray-100 dark:border-gray-800 bg-gray-50 dark:bg-gray-900 opacity-60'
      }`}
    >
      {/* 标题行 */}
      <div className="flex items-center justify-between mb-1">
        <div
          className="flex items-center gap-1.5 min-w-0 cursor-pointer select-none"
          onClick={() => setExpanded((v) => !v)}
        >
          <svg
            className={`w-3 h-3 text-gray-400 transition-transform flex-shrink-0 ${expanded ? 'rotate-90' : ''}`}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
          </svg>
          <span className="text-xs font-medium text-gray-800 dark:text-gray-200 truncate">
            {skill.display_name}
          </span>
          {!toolsOk && (
            <span title="依赖工具未满足" className="text-amber-500 text-xs flex-shrink-0">⚠</span>
          )}
          {skill.has_resources && (
            <span
              title={`${skill.resource_count} 个附属资源`}
              className="text-xs text-gray-400 flex-shrink-0"
            >
              📎
            </span>
          )}
        </div>

        {/* Toggle 开关 */}
        <button
          onClick={() => onToggle(skill.name, !skill.enabled)}
          role="switch"
          aria-checked={skill.enabled}
          className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors flex-shrink-0 ${
            skill.enabled ? 'bg-blue-500' : 'bg-gray-300 dark:bg-gray-600'
          }`}
          title={skill.enabled ? '点击禁用' : '点击启用'}
        >
          <span
            className={`inline-block h-3.5 w-3.5 rounded-full bg-white shadow-sm transition-transform ${
              skill.enabled ? 'translate-x-[18px]' : 'translate-x-[3px]'
            }`}
          />
        </button>
      </div>

      {/* 描述 */}
      <p className="text-xs text-gray-500 dark:text-gray-400 leading-relaxed line-clamp-2 ml-[18px]">
        {skill.description}
      </p>

      {/* 触发关键词（始终展示前几个） */}
      {skill.trigger_patterns.length > 0 && (
        <div className="flex flex-wrap gap-1 mt-1.5 ml-[18px]">
          {skill.trigger_patterns.slice(0, expanded ? undefined : 4).map((kw) => (
            <span
              key={kw}
              className="text-[10px] px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400"
            >
              {kw}
            </span>
          ))}
          {!expanded && skill.trigger_patterns.length > 4 && (
            <span className="text-[10px] text-gray-400">
              +{skill.trigger_patterns.length - 4}
            </span>
          )}
        </div>
      )}

      {/* 展开详情 */}
      {expanded && (
        <div className="mt-2 ml-[18px] space-y-1.5 text-[11px] border-t border-gray-100 dark:border-gray-700 pt-2">
          <DetailRow label="标识" value={skill.name} />
          <DetailRow label="优先级" value={String(skill.priority)} />
          <DetailRow
            label="工具依赖"
            value={
              skill.required_tools.length > 0
                ? skill.required_tools.join(', ')
                : '无'
            }
            warn={!toolsOk}
          />
          <DetailRow
            label="依赖状态"
            value={toolsOk ? '✓ 全部满足' : '✕ 未满足'}
            warn={!toolsOk}
          />
          <DetailRow
            label="附属资源"
            value={skill.has_resources ? `${skill.resource_count} 个` : '无'}
          />
        </div>
      )}
    </div>
  )
}

function DetailRow({
  label,
  value,
  warn = false,
}: {
  label: string
  value: string
  warn?: boolean
}) {
  return (
    <div className="flex justify-between items-start gap-2">
      <span className="text-gray-400 dark:text-gray-500 flex-shrink-0">{label}</span>
      <span
        className={`text-right break-all ${
          warn
            ? 'text-amber-600 dark:text-amber-400'
            : 'text-gray-600 dark:text-gray-300'
        }`}
      >
        {value}
      </span>
    </div>
  )
}
