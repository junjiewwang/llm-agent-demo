/**
 * 全局错误边界
 *
 * 捕获子组件树中的 React 渲染错误，展示友好的降级 UI，
 * 防止整个页面白屏。支持重试操作。
 */
import { Component, type ReactNode, type ErrorInfo } from 'react'

interface Props {
  children: ReactNode
  /** 自定义降级 UI（可选） */
  fallback?: ReactNode
}

interface State {
  hasError: boolean
  error: Error | null
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('[ErrorBoundary] Uncaught error:', error, info.componentStack)
  }

  handleRetry = (): void => {
    this.setState({ hasError: false, error: null })
  }

  render(): ReactNode {
    if (this.state.hasError) {
      if (this.props.fallback) return this.props.fallback

      return (
        <div className="h-full flex items-center justify-center bg-gray-50 dark:bg-gray-950 p-8">
          <div className="text-center max-w-md">
            <div className="text-4xl mb-4">💥</div>
            <h2 className="text-lg font-medium text-gray-800 dark:text-gray-200 mb-2">
              页面出现了意外错误
            </h2>
            <p className="text-sm text-gray-500 dark:text-gray-400 mb-1">
              {this.state.error?.message || '未知错误'}
            </p>
            <p className="text-xs text-gray-400 dark:text-gray-500 mb-4">
              你可以尝试重试，或刷新页面
            </p>
            <div className="flex items-center justify-center gap-3">
              <button
                onClick={this.handleRetry}
                className="px-4 py-2 rounded-lg bg-blue-600 text-white text-sm hover:bg-blue-700 transition-colors"
              >
                重试
              </button>
              <button
                onClick={() => window.location.reload()}
                className="px-4 py-2 rounded-lg border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 text-sm hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
              >
                刷新页面
              </button>
            </div>
          </div>
        </div>
      )
    }

    return this.props.children
  }
}
