/**
 * Markdown 渲染组件
 *
 * 基于 react-markdown + remark-gfm，支持 GFM 表格、代码块等。
 * 集成 Mermaid 渲染：自动识别 ```mermaid 代码块并渲染为 SVG 图表。
 *
 * Mermaid 优化：
 * - SVG 缓存：避免虚拟滚动卸载/重挂载时重复渲染
 * - 暗色模式：dark theme + 柔和低对比配色，视觉统一
 * - 工具栏：代码/图表切换、放大/缩小、下载 SVG
 * - 点击放大：全屏 Modal 查看大图
 */
import { useEffect, useState, useCallback, useMemo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import mermaid from 'mermaid'

// ── Mermaid 渲染引擎 ──

/** 检测当前是否为暗色模式 */
function isDarkMode(): boolean {
  return document.documentElement.classList.contains('dark')
}

/** 当前已初始化的 theme 标识 */
let currentThemeKey: string | null = null

/** 柔和暗色调色板（参考截图风格：深底 + 淡色细线 + 柔和文字） */
const DARK_THEME_VARS = {
  // 全局文字：柔和浅灰，不刺眼
  primaryTextColor: '#c8d6e5',
  secondaryTextColor: '#a0aec0',
  tertiaryTextColor: '#8899a6',
  // 节点：深色填充 + 淡色细边框
  primaryColor: '#1a2332',
  primaryBorderColor: '#3d5a80',
  secondaryColor: '#162029',
  // 连线：与边框同色系，柔和统一
  lineColor: '#4a6785',
  textColor: '#c8d6e5',
  // 背景
  mainBkg: '#1a2332',
  nodeBorder: '#3d5a80',
  // 边标签
  edgeLabelBackground: 'transparent',
  // 序列图
  actorTextColor: '#c8d6e5',
  actorBkg: '#1a2332',
  actorBorder: '#3d5a80',
  actorLineColor: '#3d5a80',
  signalColor: '#4a6785',
  signalTextColor: '#c8d6e5',
  labelTextColor: '#c8d6e5',
  loopTextColor: '#a0aec0',
  noteBkgColor: '#243447',
  noteTextColor: '#c8d6e5',
  noteBorderColor: '#3d5a80',
  activationBorderColor: '#3d5a80',
  sequenceNumberColor: '#c8d6e5',
  // 甘特图
  sectionBkgColor: '#1a2332',
  altSectionBkgColor: '#162029',
  taskTextColor: '#c8d6e5',
  taskTextDarkColor: '#c8d6e5',
  // 子图
  clusterBkg: '#162029',
  clusterBorder: '#3d5a80',
  titleColor: '#c8d6e5',
}

/** 确保 Mermaid 以正确的 theme 初始化 */
function ensureMermaidInit() {
  const dark = isDarkMode()
  const key = dark ? 'dark' : 'light'
  if (currentThemeKey === key) return

  if (dark) {
    mermaid.initialize({
      startOnLoad: false,
      theme: 'base',
      securityLevel: 'loose',
      fontFamily: 'system-ui, -apple-system, sans-serif',
      themeVariables: {
        ...DARK_THEME_VARS,
        background: '#0f1923',
      },
    })
  } else {
    mermaid.initialize({
      startOnLoad: false,
      theme: 'default',
      securityLevel: 'loose',
      fontFamily: 'system-ui, -apple-system, sans-serif',
    })
  }
  currentThemeKey = key
}

/**
 * SVG 渲染缓存：code → svg
 *
 * 解决 react-virtuoso 虚拟滚动导致的重复渲染问题：
 * 组件滚出视口被卸载 → 滚回来重挂载 → 直接读缓存，不重新调用 mermaid.render()
 */
const svgCache = new Map<string, string>()

/** 全局递增 ID，确保多个 Mermaid 块不冲突 */
let mermaidIdCounter = 0

/** 渲染 Mermaid 代码为 SVG（带缓存） */
async function renderMermaidSvg(code: string): Promise<string> {
  const trimmed = code.trim()
  const cached = svgCache.get(trimmed)
  if (cached) return cached

  ensureMermaidInit()
  const id = `mermaid-${++mermaidIdCounter}`
  try {
    const { svg } = await mermaid.render(id, trimmed)
    svgCache.set(trimmed, svg)
    return svg
  } catch (e) {
    // mermaid.render 失败时会在 DOM 中留下残留元素，清理它
    document.getElementById(id)?.remove()
    throw e
  }
}

// ── 全屏查看 Modal ──

/**
 * 处理 SVG 字符串：移除固定 width/height 属性，保留 viewBox，
 * 使 SVG 能自适应容器大小。
 */
function makeResponsiveSvg(rawSvg: string): string {
  return rawSvg
    // 移除 style 中的 max-width 限制
    .replace(/style="[^"]*"/i, (match) =>
      match.replace(/max-width:\s*[\d.]+px;?/gi, '').replace(/\s*;?\s*"/, '"')
    )
    // 移除 SVG 标签上的固定 width 属性（保留 viewBox）
    .replace(/(<svg[^>]*?)\s+width="[\d.]+(px)?"/i, '$1')
    // 移除 SVG 标签上的固定 height 属性
    .replace(/(<svg[^>]*?)\s+height="[\d.]+(px)?"/i, '$1')
}

function DiagramModal({ svg, onClose }: { svg: string; onClose: () => void }) {
  // ESC 关闭
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onClose])

  // 阻止背景滚动
  useEffect(() => {
    document.body.style.overflow = 'hidden'
    return () => { document.body.style.overflow = '' }
  }, [])

  const responsiveSvg = makeResponsiveSvg(svg)

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-8"
      onClick={onClose}
    >
      <div
        className="relative max-w-[90vw] max-h-[85vh] overflow-auto bg-white dark:bg-[#0f1923] rounded-xl p-8 shadow-2xl border border-gray-200 dark:border-gray-700/50"
        onClick={(e) => e.stopPropagation()}
      >
        {/* 关闭按钮 */}
        <button
          onClick={onClose}
          className="absolute top-3 right-3 z-10 w-8 h-8 flex items-center justify-center rounded-full bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors text-lg"
        >
          ✕
        </button>
        <div
          className="flex items-center justify-center [&>svg]:w-full [&>svg]:h-auto [&>svg]:max-h-[75vh]"
          dangerouslySetInnerHTML={{ __html: responsiveSvg }}
        />
      </div>
    </div>
  )
}

// ── 工具栏图标 (内联 SVG，避免外部依赖) ──

function IconCode() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="16 18 22 12 16 6" /><polyline points="8 6 2 12 8 18" />
    </svg>
  )
}

function IconChart() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="18" height="18" rx="2" /><path d="M3 9h18" /><path d="M9 21V9" />
    </svg>
  )
}

function IconZoomIn() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" /><line x1="11" y1="8" x2="11" y2="14" /><line x1="8" y1="11" x2="14" y2="11" />
    </svg>
  )
}

function IconZoomOut() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" /><line x1="8" y1="11" x2="14" y2="11" />
    </svg>
  )
}

function IconDownload() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="7 10 12 15 17 10" /><line x1="12" y1="15" x2="12" y2="3" />
    </svg>
  )
}

function IconExpand() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="15 3 21 3 21 9" /><polyline points="9 21 3 21 3 15" /><line x1="21" y1="3" x2="14" y2="10" /><line x1="3" y1="21" x2="10" y2="14" />
    </svg>
  )
}

/** 工具栏按钮 */
function ToolbarBtn({ onClick, title, active, children }: {
  onClick: () => void; title: string; active?: boolean; children: React.ReactNode
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      className={`p-1.5 rounded transition-colors ${
        active
          ? 'text-blue-400 bg-blue-500/10'
          : 'text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700/50'
      }`}
    >
      {children}
    </button>
  )
}

/** 工具栏 Tab 按钮 */
function TabBtn({ active, onClick, children }: {
  active: boolean; onClick: () => void; children: React.ReactNode
}) {
  return (
    <button
      onClick={onClick}
      className={`px-3 py-1 text-xs rounded transition-colors ${
        active
          ? 'bg-gray-200 dark:bg-gray-700 text-gray-800 dark:text-gray-200'
          : 'text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300'
      }`}
    >
      {children}
    </button>
  )
}

/** 下载 SVG 文件 */
function downloadSvg(svgContent: string) {
  const blob = new Blob([svgContent], { type: 'image/svg+xml' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `mermaid_${new Date().toISOString().slice(0, 19).replace(/[:-]/g, '')}.svg`
  a.click()
  URL.revokeObjectURL(url)
}

// ── Mermaid 图表组件 ──

/** Mermaid 图表渲染组件（带缓存 + 工具栏 + 暗色模式 + 点击放大） */
function MermaidBlock({ code }: { code: string }) {
  const [svg, setSvg] = useState<string>(() => svgCache.get(code.trim()) ?? '')
  const [error, setError] = useState<string | null>(null)
  const [showModal, setShowModal] = useState(false)
  const [tab, setTab] = useState<'chart' | 'code'>('chart')
  const [zoom, setZoom] = useState(1)

  useEffect(() => {
    if (svg) return
    let cancelled = false
    renderMermaidSvg(code).then(
      (result) => { if (!cancelled) { setSvg(result); setError(null) } },
      (e) => { if (!cancelled) setError(e instanceof Error ? e.message : '图表渲染失败') },
    )
    return () => { cancelled = true }
  }, [code, svg])

  const responsiveSvg = useMemo(() => svg ? makeResponsiveSvg(svg) : '', [svg])

  const handleZoomIn = useCallback(() => setZoom((z) => Math.min(z + 0.25, 3)), [])
  const handleZoomOut = useCallback(() => setZoom((z) => Math.max(z - 0.25, 0.5)), [])
  const handleExpand = useCallback(() => { if (svg) setShowModal(true) }, [svg])
  const handleDownload = useCallback(() => { if (svg) downloadSvg(svg) }, [svg])

  if (error) {
    return (
      <div className="my-2">
        <div className="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg p-3 text-sm text-red-600 dark:text-red-400 mb-2">
          Mermaid 渲染失败: {error}
        </div>
        <pre className="bg-gray-900 text-gray-100 rounded-lg p-4 overflow-x-auto text-sm">
          <code>{code}</code>
        </pre>
      </div>
    )
  }

  if (!svg) {
    return (
      <div className="my-2 bg-gray-50 dark:bg-gray-800/50 border border-gray-200 dark:border-gray-700/50 rounded-lg p-8 flex items-center justify-center">
        <span className="text-gray-400 text-sm">图表渲染中...</span>
      </div>
    )
  }

  return (
    <>
      <div className="my-2 border border-gray-200 dark:border-gray-700/50 rounded-lg overflow-hidden bg-white dark:bg-[#0f1923]">
        {/* 工具栏 */}
        <div className="flex items-center justify-between px-3 py-1.5 border-b border-gray-100 dark:border-gray-700/50 bg-gray-50 dark:bg-[#131f2e]">
          <div className="flex items-center gap-1">
            <TabBtn active={tab === 'code'} onClick={() => setTab('code')}>
              <span className="flex items-center gap-1"><IconCode />代码</span>
            </TabBtn>
            <TabBtn active={tab === 'chart'} onClick={() => setTab('chart')}>
              <span className="flex items-center gap-1"><IconChart />图表</span>
            </TabBtn>
          </div>
          {tab === 'chart' && (
            <div className="flex items-center gap-0.5">
              <ToolbarBtn onClick={handleZoomOut} title="缩小"><IconZoomOut /></ToolbarBtn>
              <ToolbarBtn onClick={handleZoomIn} title="放大"><IconZoomIn /></ToolbarBtn>
              <ToolbarBtn onClick={handleExpand} title="全屏查看"><IconExpand /></ToolbarBtn>
              <ToolbarBtn onClick={handleDownload} title="下载 SVG"><IconDownload /></ToolbarBtn>
            </div>
          )}
        </div>
        {/* 内容区 */}
        {tab === 'chart' ? (
          <div className="overflow-auto p-4">
            <div
              className="flex justify-center [&>svg]:h-auto transition-transform origin-top"
              style={{ transform: `scale(${zoom})`, transformOrigin: 'top center' }}
              dangerouslySetInnerHTML={{ __html: responsiveSvg }}
            />
          </div>
        ) : (
          <pre className="bg-gray-900 dark:bg-[#0c1520] text-gray-300 p-4 overflow-x-auto text-sm m-0 rounded-none">
            <code>{code}</code>
          </pre>
        )}
      </div>
      {showModal && <DiagramModal svg={svg} onClose={() => setShowModal(false)} />}
    </>
  )
}

interface Props {
  content: string
}

export default function MarkdownRenderer({ content }: Props) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        // 代码块
        code({ className, children, ...props }) {
          const match = /language-(\w+)/.exec(className || '')
          const isInline = !match && !className

          if (isInline) {
            return (
              <code
                className="bg-gray-100 dark:bg-gray-800 text-pink-600 dark:text-pink-400 px-1.5 py-0.5 rounded text-sm"
                {...props}
              >
                {children}
              </code>
            )
          }

          // Mermaid 代码块：渲染为 SVG 图表
          if (match && match[1] === 'mermaid') {
            const code = String(children).replace(/\n$/, '')
            return <MermaidBlock code={code} />
          }

          return (
            <div className="relative group my-2">
              {match && (
                <span className="absolute top-2 right-2 text-xs text-gray-400 opacity-0 group-hover:opacity-100 transition-opacity">
                  {match[1]}
                </span>
              )}
              <pre className="bg-gray-900 text-gray-100 rounded-lg p-4 overflow-x-auto text-sm">
                <code className={className} {...props}>
                  {children}
                </code>
              </pre>
            </div>
          )
        },
        // 表格
        table({ children }) {
          return (
            <div className="overflow-x-auto my-2">
              <table className="min-w-full border-collapse border border-gray-300 dark:border-gray-600 text-sm">
                {children}
              </table>
            </div>
          )
        },
        th({ children }) {
          return (
            <th className="border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-800 px-3 py-2 text-left font-medium">
              {children}
            </th>
          )
        },
        td({ children }) {
          return (
            <td className="border border-gray-300 dark:border-gray-600 px-3 py-2">
              {children}
            </td>
          )
        },
        // 链接
        a({ href, children }) {
          return (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="text-blue-600 dark:text-blue-400 hover:underline"
            >
              {children}
            </a>
          )
        },
      }}
    >
      {content}
    </ReactMarkdown>
  )
}
