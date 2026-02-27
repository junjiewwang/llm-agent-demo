/**
 * Mermaid 渲染引擎配置与工具函数
 *
 * 集中管理 Mermaid 初始化、主题变量、SVG 缓存、UI 状态缓存及通用工具函数。
 * 所有 Mermaid 相关的纯逻辑/无 JSX 的部分归于此模块。
 */
import mermaid from 'mermaid'

// ── 暗色主题变量 ──

export const DARK_THEME_VARS = {
  primaryTextColor: '#d2deea',
  secondaryTextColor: '#b7c5d6',
  tertiaryTextColor: '#93a6ba',
  primaryColor: '#1a2332',
  primaryBorderColor: '#5b7da2',
  secondaryColor: '#162029',
  lineColor: '#7a9dc2',
  textColor: '#d2deea',
  mainBkg: '#1a2332',
  nodeBorder: '#5b7da2',
  edgeLabelBackground: '#0f1923cc',
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
  sectionBkgColor: '#1a2332',
  altSectionBkgColor: '#162029',
  taskTextColor: '#c8d6e5',
  taskTextDarkColor: '#c8d6e5',
  clusterBkg: '#162029',
  clusterBorder: '#3d5a80',
  titleColor: '#c8d6e5',
} as const

// ── 缩放参数 ──

/** 阅读基线缩放比：actualZoom=0.5 → 用户体感 100% */
export const BASE_READING_ZOOM = 0.5
export const MIN_ZOOM = 0.25
export const MAX_ZOOM = 1.5
export const ZOOM_STEP = 0.05

/** 首屏 fit 缩放参数 */
export const FIT_TARGET_PX = 560
export const FIT_TARGET_HEIGHT_PX = 420
export const FIT_MIN = 0.35
export const FIT_MAX = 0.5

/** fit 策略版本号，每次调参 +1，可让旧缓存中的 auto 模式重新 fit */
export const FIT_VERSION = 3

// ── 缓存 ──

/** LRU 缓存上限 */
const SVG_CACHE_MAX = 128
const UI_CACHE_MAX = 128

/**
 * SVG 渲染缓存（LRU）：code → svg
 *
 * 解决 react-virtuoso 虚拟滚动导致的重复渲染问题：
 * 组件滚出视口被卸载 → 滚回来重挂载 → 直接读缓存，不重新调用 mermaid.render()
 */
export const svgCache = new Map<string, string>()

/** 图表 UI 状态缓存（LRU），防止虚拟滚动卸载重挂载丢失交互状态 */
export interface DiagramUiState {
  tab: 'chart' | 'code'
  zoom: number
  zoomMode: 'auto' | 'manual'
  fitVersion: number
}
export const diagramUiCache = new Map<string, DiagramUiState>()

/** 插入缓存并自动淘汰最旧条目（LRU） */
function lruSet<K, V>(map: Map<K, V>, key: K, value: V, maxSize: number): void {
  if (map.has(key)) {
    map.delete(key)
  } else if (map.size >= maxSize) {
    // Map 迭代按插入顺序，删除第一个即最旧
    const oldest = map.keys().next().value
    if (oldest !== undefined) map.delete(oldest)
  }
  map.set(key, value)
}

export function setSvgCache(key: string, value: string): void {
  lruSet(svgCache, key, value, SVG_CACHE_MAX)
}

export function setDiagramUiCache(key: string, value: DiagramUiState): void {
  lruSet(diagramUiCache, key, value, UI_CACHE_MAX)
}

// ── Mermaid 初始化 ──

function isDarkMode(): boolean {
  return document.documentElement.classList.contains('dark')
}

let currentThemeKey: string | null = null

export function ensureMermaidInit(): void {
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
      themeVariables: { ...DARK_THEME_VARS, background: '#0f1923' },
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

// ── 渲染与工具函数 ──

let mermaidIdCounter = 0

/** 渲染 Mermaid 代码为 SVG（带 LRU 缓存） */
export async function renderMermaidSvg(code: string): Promise<string> {
  const trimmed = code.trim()
  const cached = svgCache.get(trimmed)
  if (cached) return cached

  ensureMermaidInit()
  const id = `mermaid-${++mermaidIdCounter}`
  try {
    const { svg } = await mermaid.render(id, trimmed)
    setSvgCache(trimmed, svg)
    return svg
  } catch (e) {
    document.getElementById(id)?.remove()
    throw e
  }
}

/** 从 SVG viewBox 解析原始宽高 */
export function parseSvgViewBoxSize(svgStr: string): { width: number; height: number } | null {
  const match = svgStr.match(/viewBox=["'][\d.e+-]+\s+[\d.e+-]+\s+([\d.e+-]+)\s+([\d.e+-]+)["']/i)
  if (!match) return null
  const width = parseFloat(match[1])
  const height = parseFloat(match[2])
  return (isFinite(width) && isFinite(height)) ? { width, height } : null
}

/** 根据 SVG 原始宽高计算首屏适配缩放比（宽高双约束） */
export function calcInitialFitZoom(svgWidth: number, svgHeight: number): number {
  const fitW = FIT_TARGET_PX / svgWidth
  const fitH = FIT_TARGET_HEIGHT_PX / svgHeight
  const fit = Math.min(fitW, fitH, 1)
  return Math.min(Math.max(fit, FIT_MIN), FIT_MAX)
}

/** 内部 actualZoom → 用户展示百分比 */
export function toDisplayPercent(actualZoom: number): number {
  return Math.round((actualZoom / BASE_READING_ZOOM) * 100)
}

/**
 * 处理 SVG 字符串：移除固定 width/height 属性，保留 viewBox，
 * 使 SVG 能自适应容器大小。
 */
export function makeResponsiveSvg(rawSvg: string): string {
  return rawSvg.replace(/<svg\b([^>]*)>/i, (_, rawAttrs: string) => {
    const attrsWithoutSize = rawAttrs.replace(/\s(?:width|height)=(['"])[^'"]*\1/gi, '')
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

/** 下载 SVG 文件 */
export function downloadSvg(svgContent: string): void {
  const blob = new Blob([svgContent], { type: 'image/svg+xml' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `mermaid_${new Date().toISOString().slice(0, 19).replace(/[:-]/g, '')}.svg`
  a.click()
  URL.revokeObjectURL(url)
}
