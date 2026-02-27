/**
 * Mermaid 图表全屏查看 Modal
 *
 * 支持 ESC 关闭、点击遮罩关闭、阻止背景滚动。
 * SVG 自适应容器大小显示。
 */
import { useEffect } from 'react'
import { makeResponsiveSvg } from './mermaid-config'

interface DiagramModalProps {
  svg: string
  onClose: () => void
}

export default function DiagramModal({ svg, onClose }: DiagramModalProps) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onClose])

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
