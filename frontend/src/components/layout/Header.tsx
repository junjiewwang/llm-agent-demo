/**
 * 顶部导航栏
 */
import { useState } from 'react'
import { useUIStore } from '../../stores/uiStore'
import { useSessionStore } from '../../stores/sessionStore'
import LoginModal from '../auth/LoginModal'

export default function Header() {
  const { toggleSidebarCollapse, toggleStatusPanel } = useUIStore()
  const { user, logout } = useSessionStore()
  const [isLoginOpen, setIsLoginOpen] = useState(false)

  return (
    <header className="h-14 flex-shrink-0 bg-white dark:bg-gray-900 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between px-4">
      <div className="flex items-center gap-3">
        <button
          onClick={toggleSidebarCollapse}
          className="p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-600 dark:text-gray-400 transition-colors"
          title="折叠侧边栏"
        >
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M4 6h16M4 12h16M4 18h16" />
          </svg>
        </button>
        <h1 className="text-base font-semibold text-gray-800 dark:text-gray-200 flex items-center gap-2" style={{ fontFamily: 'var(--font-heading)' }}>
          <span className="text-xl">🤖</span>
          <span className="tracking-tight">LLM ReAct Agent</span>
        </h1>
      </div>

      <div className="flex items-center gap-4">
        {/* 用户信息与登录按钮 */}
        <div className="flex items-center gap-2">
          {user ? (
            <div className="flex items-center gap-3">
              <span className="text-sm font-medium text-gray-700 dark:text-gray-300">
                {user.username}
              </span>
              <button
                onClick={() => {
                  if (confirm('确定要退出登录吗？')) {
                    logout()
                    window.location.reload()
                  }
                }}
                className="text-xs px-2 py-1 bg-gray-100 hover:bg-gray-200 dark:bg-gray-800 dark:hover:bg-gray-700 text-gray-600 dark:text-gray-400 rounded transition-colors"
              >
                退出
              </button>
            </div>
          ) : (
            <button
              onClick={() => setIsLoginOpen(true)}
              className="text-sm px-3 py-1.5 text-white rounded-lg transition-colors font-medium shadow-sm"
              style={{ backgroundColor: 'var(--brand-primary)' }}
              onMouseEnter={(e) => e.currentTarget.style.backgroundColor = 'var(--brand-primary-hover)'}
              onMouseLeave={(e) => e.currentTarget.style.backgroundColor = 'var(--brand-primary)'}
            >
              登录 / 注册
            </button>
          )}
        </div>

        <div className="h-4 w-px bg-gray-300 dark:bg-gray-700 mx-1" />

        <button
          onClick={toggleStatusPanel}
          className="p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-600 dark:text-gray-400 transition-colors"
          title="系统状态"
        >
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
          </svg>
        </button>
      </div>

      <LoginModal isOpen={isLoginOpen} onClose={() => setIsLoginOpen(false)} />
    </header>
  )
}
