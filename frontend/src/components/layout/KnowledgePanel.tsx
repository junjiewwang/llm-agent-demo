/**
 * 知识库管理面板
 *
 * 嵌入在 StatusPanel 底部，支持文件上传和清空知识库。
 */
import { useRef, useState } from 'react'
import { uploadFiles, clearKnowledgeBase, getStatus } from '../../api/client'
import { useChatStore } from '../../stores/chatStore'
import { useSessionStore } from '../../stores/sessionStore'

export default function KnowledgePanel() {
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [uploading, setUploading] = useState(false)
  const [result, setResult] = useState<string | null>(null)
  const tenantId = useSessionStore((s) => s.tenantId)
  const setStatus = useChatStore((s) => s.setStatus)

  const refreshStatus = async () => {
    try {
      const res = await getStatus(tenantId)
      if (res.success && res.data) setStatus(res.data)
    } catch { /* ignore */ }
  }

  const handleUpload = async () => {
    const files = fileInputRef.current?.files
    if (!files || files.length === 0) return

    setUploading(true)
    setResult(null)

    try {
      const res = await uploadFiles(Array.from(files))
      if (res.success && res.data) {
        const lines = res.data.results.map((r) =>
          r.error ? `❌ ${r.file}: ${r.error}` : `✅ ${r.file}: ${r.chunks} 个文本块`,
        )
        lines.push(`\n📚 知识库总量: ${res.data.total_chunks} 个文本块`)
        setResult(lines.join('\n'))
      } else {
        setResult(`❌ ${res.error || '上传失败'}`)
      }
      await refreshStatus()
    } catch (err) {
      setResult(`❌ ${(err as Error).message}`)
    } finally {
      setUploading(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  const handleClear = async () => {
    try {
      const res = await clearKnowledgeBase()
      setResult(res.success ? '✅ 知识库已清空' : `❌ ${res.error}`)
      await refreshStatus()
    } catch (err) {
      setResult(`❌ ${(err as Error).message}`)
    }
  }

  return (
    <div className="mt-6 pt-4 border-t border-gray-200 dark:border-gray-700">
      <h4 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-3 flex items-center gap-2">
        📚 知识库管理
      </h4>

      <div className="space-y-3">
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept=".txt,.md,.pdf"
          className="block w-full text-xs text-gray-500 file:mr-2 file:py-1.5 file:px-3 file:rounded-lg file:border-0 file:text-xs file:font-medium file:bg-blue-50 file:text-blue-700 hover:file:bg-blue-100 dark:file:bg-blue-900/30 dark:file:text-blue-300"
        />

        <div className="flex gap-2">
          <button
            onClick={handleUpload}
            disabled={uploading}
            className="flex-1 h-8 rounded-lg bg-blue-600 hover:bg-blue-700 disabled:bg-gray-400 text-white text-xs font-medium transition-colors"
          >
            {uploading ? '导入中...' : '📥 导入'}
          </button>
          <button
            onClick={handleClear}
            className="h-8 px-3 rounded-lg bg-red-50 hover:bg-red-100 dark:bg-red-900/20 dark:hover:bg-red-900/40 text-red-600 dark:text-red-400 text-xs font-medium transition-colors"
          >
            🗑️ 清空
          </button>
        </div>

        {result && (
          <pre className="text-xs text-gray-600 dark:text-gray-400 bg-gray-100 dark:bg-gray-800 rounded-lg p-2 whitespace-pre-wrap max-h-32 overflow-y-auto">
            {result}
          </pre>
        )}
      </div>
    </div>
  )
}
