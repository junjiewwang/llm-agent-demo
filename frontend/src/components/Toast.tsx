/**
 * 全局 Toast 通知容器
 *
 * 固定在屏幕右上角，自动消失，支持手动关闭。
 * 在 App.tsx 中挂载一次即可。
 */
import { useToastStore } from '../stores/toastStore'
import type { ToastType } from '../stores/toastStore'

const ICON: Record<ToastType, string> = {
  success: '✓',
  error: '✕',
  info: 'ℹ',
}

const COLOR: Record<ToastType, string> = {
  success:
    'bg-emerald-50 dark:bg-emerald-900/30 border-emerald-200 dark:border-emerald-700 text-emerald-800 dark:text-emerald-200',
  error:
    'bg-red-50 dark:bg-red-900/30 border-red-200 dark:border-red-700 text-red-800 dark:text-red-200',
  info:
    'bg-blue-50 dark:bg-blue-900/30 border-blue-200 dark:border-blue-700 text-blue-800 dark:text-blue-200',
}

const ICON_COLOR: Record<ToastType, string> = {
  success: 'bg-emerald-500 text-white',
  error: 'bg-red-500 text-white',
  info: 'bg-blue-500 text-white',
}

export default function ToastContainer() {
  const { toasts, remove } = useToastStore()

  if (toasts.length === 0) return null

  return (
    <div className="fixed top-4 right-4 z-[9999] flex flex-col gap-2 pointer-events-none">
      {toasts.map((t) => (
        <div
          key={t.id}
          className={`pointer-events-auto flex items-center gap-2.5 px-3 py-2.5 rounded-lg border shadow-lg backdrop-blur-sm
            animate-[slideIn_0.25s_ease-out] max-w-xs ${COLOR[t.type]}`}
        >
          <span
            className={`flex-shrink-0 w-5 h-5 rounded-full flex items-center justify-center text-xs font-bold ${ICON_COLOR[t.type]}`}
          >
            {ICON[t.type]}
          </span>
          <span className="text-sm leading-snug flex-1">{t.message}</span>
          <button
            onClick={() => remove(t.id)}
            className="flex-shrink-0 opacity-40 hover:opacity-80 transition-opacity text-sm leading-none"
          >
            ×
          </button>
        </div>
      ))}
    </div>
  )
}
