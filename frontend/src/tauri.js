/**
 * Tauri 环境检测与后端就绪等待
 *
 * 三重保险：
 * 1. 监听 Rust emit 的 'backend-ready' 事件（Rust 轮询 HTTP 200 后触发）
 * 2. 前端 fetch 轮询 http://127.0.0.1:8000/（CSP=null 无限制）
 * 3. invoke('check_backend') 确认进程存在
 *
 * 只要任意一种方式确认后端就绪，立即进入主界面。
 */

export const isTauri = () =>
  typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window

/**
 * 等待后端就绪
 * @param {number} maxWait 最大等待秒数（sidecar 首次启动约 8-15s）
 * @returns {Promise<boolean>}
 */
export async function waitForBackend(maxWait = 90) {
  if (!isTauri()) return true

  return new Promise(async (resolve) => {
    let resolved = false
    let unlisten = null

    const done = (val, from) => {
      if (resolved) return
      resolved = true
      console.log(`[tauri] 后端就绪，来源: ${from}`)
      try { if (unlisten) unlisten() } catch {}
      resolve(val)
    }

    // 超时兜底
    const timeoutId = setTimeout(() => done(false, 'timeout'), maxWait * 1000)

    // ── 方案1：监听 Rust emit 的 backend-ready 事件 ────────────────────────
    try {
      const { listen } = await import('@tauri-apps/api/event')
      unlisten = await listen('backend-ready', () => {
        clearTimeout(timeoutId)
        done(true, 'backend-ready event')
      })
    } catch (e) {
      console.warn('[tauri] 无法监听 backend-ready:', e)
    }

    // ── 方案2：fetch 轮询 http://127.0.0.1:8000/ ──────────────────────────
    // CSP=null，无限制，最直接可靠
    ;(async () => {
      let attempt = 0
      const logLines = [`[frontend] waitForBackend started at ${new Date().toISOString()}`]

      const flushLog = () => {
        try {
          const existing = localStorage.getItem('startup_log') || ''
          localStorage.setItem('startup_log', existing + logLines.join('\n') + '\n')
        } catch {}
        // 同时尝试写 /tmp（在 Tauri 环境下通过 invoke）
        try {
          import('@tauri-apps/api/core').then(({ invoke }) => {
            invoke('write_startup_log', { content: logLines.join('\n') + '\n' }).catch(() => {})
          })
        } catch {}
      }

      while (!resolved) {
        attempt++
        const t0 = Date.now()
        try {
          const resp = await fetch('http://127.0.0.1:8000/', {
            signal: AbortSignal.timeout(2000),
            cache: 'no-store',
          })
          const elapsed = Date.now() - t0
          logLines.push(`[${new Date().toISOString()}] fetch #${attempt}: HTTP ${resp.status} (${elapsed}ms)`)
          if (resp.ok) {
            logLines.push(`[${new Date().toISOString()}] ✅ 后端就绪 via fetch`)
            flushLog()
            clearTimeout(timeoutId)
            done(true, 'fetch poll')
            return
          }
        } catch (e) {
          const elapsed = Date.now() - t0
          logLines.push(`[${new Date().toISOString()}] fetch #${attempt}: 失败 ${e.message} (${elapsed}ms)`)
          if (attempt % 5 === 0) flushLog()
        }
        await new Promise(r => setTimeout(r, 1500))
      }
      flushLog()
    })()

    // ── 方案3：invoke check_backend + 延迟确认 ────────────────────────────
    // 作为补充：进程已启动后再等 8 秒（sidecar 启动时间）
    ;(async () => {
      let processDetected = false
      while (!resolved) {
        try {
          const { invoke } = await import('@tauri-apps/api/core')
          const ok = await invoke('check_backend')
          if (ok && !processDetected) {
            processDetected = true
            console.log('[tauri] 后端进程已启动，等待 HTTP 就绪...')
            // 不立即 resolve，继续等 fetch 轮询确认
          }
        } catch {
          // invoke 还没准备好
        }
        await new Promise(r => setTimeout(r, 1000))
      }
    })()
  })
}
