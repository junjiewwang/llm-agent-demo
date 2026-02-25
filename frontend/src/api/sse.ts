/**
 * SSE 聊天客户端
 *
 * 基于 fetch + ReadableStream 解析 SSE 事件流。
 * 使用 AbortController 实现取消。
 */
import type { AgentEvent, DoneEvent, SSEEventType } from '../types'

export interface ChatSSECallbacks {
  onEvent: (event: AgentEvent) => void
  onDone: (data: DoneEvent) => void
  onError: (error: string) => void
}

/**
 * 发起 SSE 聊天请求
 * @returns AbortController，调用 .abort() 可取消请求
 */
export function chatSSE(
  tenantId: string,
  message: string,
  callbacks: ChatSSECallbacks,
): AbortController {
  const controller = new AbortController()

  ;(async () => {
    try {
      const response = await fetch(
        `/api/chat?tenant_id=${encodeURIComponent(tenantId)}`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message }),
          signal: controller.signal,
        },
      )

      if (!response.ok) {
        callbacks.onError(`HTTP ${response.status}: ${response.statusText}`)
        return
      }

      const reader = response.body?.getReader()
      if (!reader) {
        callbacks.onError('响应体不可读')
        return
      }

      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })

        // 解析 SSE：按双换行分割事件（兼容 \r\n\r\n 和 \n\n）
        const parts = buffer.split(/\r?\n\r?\n/)
        buffer = parts.pop() || ''

        for (const part of parts) {
          if (!part.trim()) continue

          let eventType: SSEEventType | null = null
          let eventData = ''

          for (const line of part.split(/\r?\n/)) {
            if (line.startsWith('event:')) {
              eventType = line.slice(6).trim() as SSEEventType
            } else if (line.startsWith('data:')) {
              eventData = line.slice(5).trim()
            }
          }

          if (!eventType || !eventData) continue

          try {
            const parsed = JSON.parse(eventData)

            if (eventType === 'done') {
              callbacks.onDone({ type: 'done', ...parsed } as DoneEvent)
            } else if (eventType === 'error') {
              callbacks.onError(parsed.message || '未知错误')
            } else {
              callbacks.onEvent({ type: eventType, ...parsed } as AgentEvent)
            }
          } catch {
            console.warn('SSE JSON 解析失败:', eventData)
          }
        }
      }
    } catch (err) {
      if ((err as Error).name === 'AbortError') return
      callbacks.onError((err as Error).message || '网络错误')
    }
  })()

  return controller
}
