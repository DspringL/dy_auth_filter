/**
 * Tauri 环境检测与后端就绪等待
 *
 * 策略：
 * 1. 监听 Rust emit 的 'backend-ready' 事件（最可靠，Rust 侧 reqwest 确认 HTTP 200 后触发）
 * 2. 同时轮询 invoke('check_backend')，一旦 Rust 侧进程存在即认为就绪
 *    （不在前端做 fetch，避免 WebView CSP/安全策略拦截 http://127.0.0.1）
 */

export const isTauri = () =>
  typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window

/**
 * 等待后端就绪
 * @param {number} maxWait 最大等待秒数（默认 90，sidecar 首次启动约 8-15s）
 * @returns {Promise<boolean>}
 */
export async function waitForBackend(maxWait = 90) {
  if (!isTauri()) return true

  return new Promise(async (resolve) => {
    let resolved = false
    let unlisten = null

    const done = (val) => {
      if (resolved) return
      resolved = true
      try { if (unlisten) unlisten() } catch {}
      resolve(val)
    }

    // 超时兜底
    const timeoutId = setTimeout(() => done(false), maxWait * 1000)

    // ── 方案1：监听 Rust emit 的 backend-ready 事件 ────────────────────────
    // Rust 侧 reqwest 轮询确认 HTTP 200 后才 emit，最可靠
    try {
      const { listen } = await import('@tauri-apps/api/event')
      unlisten = await listen('backend-ready', () => {
        clearTimeout(timeoutId)
        done(true)
      })
    } catch (e) {
      console.warn('[tauri] 无法监听 backend-ready，降级为轮询', e)
    }

    // ── 方案2：轮询 invoke check_backend（IPC，无 CSP 限制）────────────────
    // check_backend 返回 true 表示 Rust 侧进程已存在
    // 注意：进程存在 ≠ HTTP 已就绪，所以这里再等 3 秒再 resolve
    ;(async () => {
      let backendProcessStarted = false
      while (!resolved) {
        try {
          const { invoke } = await import('@tauri-apps/api/core')
          const ok = await invoke('check_backend')
          if (ok && !backendProcessStarted) {
            backendProcessStarted = true
            // 进程已启动，再等 5 秒让 HTTP 服务准备好
            await new Promise(r => setTimeout(r, 5000))
            if (!resolved) {
              clearTimeout(timeoutId)
              done(true)
            }
            return
          }
        } catch {
          // invoke 还没准备好，继续等
        }
        await new Promise(r => setTimeout(r, 1000))
      }
    })()
  })
}
