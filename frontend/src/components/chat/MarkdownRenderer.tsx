/**
 * Markdown 渲染组件
 *
 * 基于 react-markdown + remark-gfm，支持 GFM 表格、代码高亮等。
 */
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

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
