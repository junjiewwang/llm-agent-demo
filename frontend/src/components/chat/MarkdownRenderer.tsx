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
import { useEffect, useState, useCallback, useMemo, useRef } from 'react'
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
  primaryTextColor: '#d2deea',
  secondaryTextColor: '#b7c5d6',
  tertiaryTextColor: '#93a6ba',
  // 节点：深色填充 + 淡色细边框
  primaryColor: '#1a2332',
  primaryBorderColor: '#5b7da2',
  secondaryColor: '#162029',
  // 连线：提高暗色下对比度
  lineColor: '#7a9dc2',
  textColor: '#d2deea',
  // 背景
  mainBkg: '#1a2332',
  nodeBorder: '#5b7da2',
  // 边标签：避免透明导致暗底可读性差
  edgeLabelBackground: '#0f1923cc',
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
      fontSize: 14,
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
      fontSize: 14,
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

/** 图表 UI 状态缓存（tab/zoom/mode），防止虚拟滚动卸载重挂载丢失交互状态 */
interface DiagramUiState {
  tab: 'chart' | 'code'
  zoom: number
  zoomMode: 'auto' | 'manual'
  fitVersion: number
}
const diagramUiCache = new Map<string, DiagramUiState>()

/** fit 策略版本号，每次调参 +1，可让旧缓存中的 auto 模式重新 fit */
const FIT_VERSION = 3

/**
 * 阅读基线缩放比：定义"用户体感 100%"对应的内部 actualZoom 值。
 * 即 actualZoom=0.5 时，UI 显示为 100%。
 */
const BASE_READING_ZOOM = 0.5

const MIN_ZOOM = 0.25
const MAX_ZOOM = 1.5
const ZOOM_STEP = 0.05

/** 首屏 fit 缩放参数 */
const FIT_TARGET_PX = 560
const FIT_TARGET_HEIGHT_PX = 420
const FIT_MIN = 0.35
const FIT_MAX = 0.5

/** 内部 actualZoom → 用户展示百分比 */
function toDisplayPercent(actualZoom: number): number {
  return Math.round((actualZoom / BASE_READING_ZOOM) * 100)
}

/** 从 SVG viewBox 解析原始宽高 */
function parseSvgViewBoxSize(svgStr: string): { width: number; height: number } | null {
  const match = svgStr.match(/viewBox=["'][\d.e+-]+\s+[\d.e+-]+\s+([\d.e+-]+)\s+([\d.e+-]+)["']/i)
  if (!match) return null
  const width = parseFloat(match[1])
  const height = parseFloat(match[2])
  return (isFinite(width) && isFinite(height)) ? { width, height } : null
}

/** 根据 SVG 原始宽高计算首屏适配缩放比（宽高双约束） */
function calcInitialFitZoom(svgWidth: number, svgHeight: number): number {
  const fitW = FIT_TARGET_PX / svgWidth
  const fitH = FIT_TARGET_HEIGHT_PX / svgHeight
  const fit = Math.min(fitW, fitH, 1)
  return Math.min(Math.max(fit, FIT_MIN), FIT_MAX)
}

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
  return rawSvg.replace(/<svg\b([^>]*)>/i, (_, rawAttrs: string) => {
    // 移除根节点上的固定宽高属性
    const attrsWithoutSize = rawAttrs.replace(/\s(?:width|height)=(['"])[^'"]*\1/gi, '')

    // 拆出 style，去掉其中的宽高/最大宽高约束，再注入响应式样式
    const styleMatch = attrsWithoutSize.match(/\sstyle=(['"])(.*?)\1/i)
    const existingStyle = styleMatch?.[2] ?? ''
    const attrsWithoutStyle = attrsWithoutSize.replace(/\sstyle=(['"])(.*?)\1/i, '')

    const cleanedStyle = existingStyle
      .split(';')
      .map((item) => item.trim())
      .filter(Boolean)
      .filter((item) => !/^(width|height|max-width|max-height)\s*:/i.test(item))

    const mergedStyle = [
      ...cleanedStyle,
      'width: 100%',
      'height: auto',
      'max-width: none',
      'display: block',
    ].join('; ')

    return `<svg${attrsWithoutStyle} style="${mergedStyle}">`
  })
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
function ToolbarBtn({ onClick, title, active, disabled, label, children }: {
  onClick: () => void
  title: string
  active?: boolean
  disabled?: boolean
  label?: string
  children: React.ReactNode
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      disabled={disabled}
      className={`flex items-center gap-1.5 px-2 py-1.5 rounded transition-colors text-xs ${
        active
          ? 'text-blue-500 bg-blue-500/10'
          : disabled
            ? 'text-gray-300 dark:text-gray-600 cursor-not-allowed'
            : 'text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700/50'
      }`}
    >
      {children}
      {label && <span>{label}</span>}
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
  const diagramKey = useMemo(() => code.trim(), [code])
  const cachedUi = diagramUiCache.get(diagramKey)

  const [svg, setSvg] = useState<string>(() => svgCache.get(diagramKey) ?? '')
  const [error, setError] = useState<string | null>(null)
  const [showModal, setShowModal] = useState(false)
  const [tab, setTab] = useState<'chart' | 'code'>(cachedUi?.tab ?? 'chart')
  const [zoom, setZoom] = useState(cachedUi?.zoom ?? BASE_READING_ZOOM)
  const [zoomMode, setZoomMode] = useState<'auto' | 'manual'>(cachedUi?.zoomMode ?? 'auto')
  /** 用户是否手动缩放过（一旦为 true 则不再自动 fit） */
  const hasUserZoomedRef = useRef(cachedUi?.zoomMode === 'manual')

  // 异步渲染 SVG
  useEffect(() => {
    if (svg) return
    let cancelled = false
    renderMermaidSvg(code).then(
      (result) => { if (!cancelled) { setSvg(result); setError(null) } },
      (e) => { if (!cancelled) setError(e instanceof Error ? e.message : '图表渲染失败') },
    )
    return () => { cancelled = true }
  }, [code, svg])

  // 首屏自适配缩放：首次渲染 / fit 策略版本升级时触发
  useEffect(() => {
    if (!svg || hasUserZoomedRef.current) return
    // 有缓存且 zoomMode=manual -> 不动
    if (cachedUi && cachedUi.zoomMode === 'manual') return
    // 有缓存且 fitVersion 未变 -> 不重算
    if (cachedUi && cachedUi.fitVersion === FIT_VERSION) return

    const size = parseSvgViewBoxSize(svg)
    if (size && (size.width > FIT_TARGET_PX || size.height > FIT_TARGET_HEIGHT_PX)) {
      const fitZoom = calcInitialFitZoom(size.width, size.height)
      setZoom(Math.min(fitZoom, BASE_READING_ZOOM))
      setZoomMode('auto')
    }
  }, [svg, cachedUi])

  // 缓存写回
  useEffect(() => {
    diagramUiCache.set(diagramKey, { tab, zoom, zoomMode, fitVersion: FIT_VERSION })
  }, [diagramKey, tab, zoom, zoomMode])

  const responsiveSvg = useMemo(() => svg ? makeResponsiveSvg(svg) : '', [svg])
  const displayPercent = toDisplayPercent(zoom)
  const canZoomIn = zoom < MAX_ZOOM - 1e-6
  const canZoomOut = zoom > MIN_ZOOM + 1e-6

  const handleZoomIn = useCallback(() => {
    if (!canZoomIn) return
    hasUserZoomedRef.current = true
    setZoomMode('manual')
    setZoom((z) => Math.min(z + ZOOM_STEP, MAX_ZOOM))
  }, [canZoomIn])
  const handleZoomOut = useCallback(() => {
    if (!canZoomOut) return
    hasUserZoomedRef.current = true
    setZoomMode('manual')
    setZoom((z) => Math.max(z - ZOOM_STEP, MIN_ZOOM))
  }, [canZoomOut])
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
      <div className="my-2 max-w-[760px] border border-gray-200/60 dark:border-gray-700/30 rounded-lg overflow-hidden bg-white dark:bg-[#0f1923]">
        {/* 工具栏 */}
        <div className="flex items-center justify-between px-3 py-1 border-b border-gray-100/80 dark:border-gray-700/40 bg-gray-50/80 dark:bg-[#131f2e]/80">
          <div className="flex items-center gap-1">
            <TabBtn active={tab === 'code'} onClick={() => setTab('code')}>
              <span className="flex items-center gap-1"><IconCode />代码</span>
            </TabBtn>
            <TabBtn active={tab === 'chart'} onClick={() => setTab('chart')}>
              <span className="flex items-center gap-1"><IconChart />图表</span>
            </TabBtn>
          </div>
          {tab === 'chart' && (
            <div className="flex items-center gap-1">
              <span className="text-xs text-gray-400 dark:text-gray-500 tabular-nums">{displayPercent}%</span>
              <ToolbarBtn onClick={handleZoomOut} title="缩小" disabled={!canZoomOut} label="缩小"><IconZoomOut /></ToolbarBtn>
              <ToolbarBtn onClick={handleZoomIn} title="放大" disabled={!canZoomIn} label="放大"><IconZoomIn /></ToolbarBtn>
              <ToolbarBtn onClick={handleExpand} title="全屏查看" label="全屏"><IconExpand /></ToolbarBtn>
              <ToolbarBtn onClick={handleDownload} title="下载 SVG" label="下载"><IconDownload /></ToolbarBtn>
            </div>
          )}
        </div>
        {/* 内容区 */}
        {tab === 'chart' ? (
          <div className="relative group overflow-auto p-2">
            <div className="flex justify-start">
              <div
                className="transition-[width] duration-200 ease-out [&>svg]:w-full [&>svg]:h-auto [&>svg]:max-w-none"
                style={{ width: `${zoom * 100}%` }}
                dangerouslySetInnerHTML={{ __html: responsiveSvg }}
              />
            </div>
            {/* 悬浮操作条 */}
            <div className="pointer-events-none absolute bottom-3 right-3 opacity-0 group-hover:opacity-100 transition-opacity">
              <div className="pointer-events-auto flex items-center gap-1 rounded-full bg-white/90 dark:bg-[#0f1923]/90 border border-gray-200/70 dark:border-gray-700/60 shadow-sm px-2 py-1">
                <span className="text-[11px] text-gray-500 dark:text-gray-400 tabular-nums">{displayPercent}%</span>
                <button
                  onClick={handleZoomOut}
                  disabled={!canZoomOut}
                  title="缩小"
                  className={`p-1 rounded ${
                    canZoomOut
                      ? 'text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200'
                      : 'text-gray-300 dark:text-gray-600 cursor-not-allowed'
                  }`}
                >
                  <IconZoomOut />
                </button>
                <button
                  onClick={handleZoomIn}
                  disabled={!canZoomIn}
                  title="放大"
                  className={`p-1 rounded ${
                    canZoomIn
                      ? 'text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200'
                      : 'text-gray-300 dark:text-gray-600 cursor-not-allowed'
                  }`}
                >
                  <IconZoomIn />
                </button>
                <button
                  onClick={handleExpand}
                  title="全屏查看"
                  className="p-1 rounded text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200"
                >
                  <IconExpand />
                </button>
              </div>
            </div>
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
            return <div className="mermaid-diagram-wrapper"><MermaidBlock code={code} /></div>
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
