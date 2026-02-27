/**
 * Mermaid 图表渲染组件
 *
 * 带 SVG 缓存 + 工具栏（代码/图表切换、缩放、下载、全屏）+ 暗色模式。
 * 依赖 mermaid-config 提供渲染引擎和缓存，DiagramModal 提供全屏查看。
 */
import { useEffect, useState, useCallback, useMemo, useRef } from 'react'
import {
  svgCache,
  diagramUiCache,
  setDiagramUiCache,
  renderMermaidSvg,
  parseSvgViewBoxSize,
  calcInitialFitZoom,
  toDisplayPercent,
  makeResponsiveSvg,
  downloadSvg,
  BASE_READING_ZOOM,
  MIN_ZOOM,
  MAX_ZOOM,
  ZOOM_STEP,
  FIT_TARGET_PX,
  FIT_TARGET_HEIGHT_PX,
  FIT_VERSION,
} from './mermaid-config'
import DiagramModal from './DiagramModal'

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

// ── 工具栏子组件 ──

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

// ── 主组件 ──

export default function MermaidBlock({ code }: { code: string }) {
  const diagramKey = useMemo(() => code.trim(), [code])
  const cachedUi = diagramUiCache.get(diagramKey)

  const [svg, setSvg] = useState<string>(() => svgCache.get(diagramKey) ?? '')
  const [error, setError] = useState<string | null>(null)
  const [showModal, setShowModal] = useState(false)
  const [tab, setTab] = useState<'chart' | 'code'>(cachedUi?.tab ?? 'chart')
  const [zoom, setZoom] = useState(cachedUi?.zoom ?? BASE_READING_ZOOM)
  const [zoomMode, setZoomMode] = useState<'auto' | 'manual'>(cachedUi?.zoomMode ?? 'auto')
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

  // 首屏自适配缩放
  useEffect(() => {
    if (!svg || hasUserZoomedRef.current) return
    if (cachedUi && cachedUi.zoomMode === 'manual') return
    if (cachedUi && cachedUi.fitVersion === FIT_VERSION) return

    const size = parseSvgViewBoxSize(svg)
    if (size && (size.width > FIT_TARGET_PX || size.height > FIT_TARGET_HEIGHT_PX)) {
      const fitZoom = calcInitialFitZoom(size.width, size.height)
      setZoom(Math.min(fitZoom, BASE_READING_ZOOM))
      setZoomMode('auto')
    }
  }, [svg, cachedUi])

  // 缓存写回（LRU）
  useEffect(() => {
    setDiagramUiCache(diagramKey, { tab, zoom, zoomMode, fitVersion: FIT_VERSION })
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
