/**
 * Tauri 环境检测与后端就绪等待
 * 桌面端：优先监听 Rust emit 的 backend-ready 事件，降级为 invoke 轮询
 * 浏览器端：直接跳过
 */

/** 判断是否在 Tauri 桌面环境中运行 */
export const isTauri = () => {
  return typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window
}

/**
 * 等待后端就绪
 * @param {number} maxWait 最大等待秒数
 * @returns {Promise<boolean>}
 */
export async function waitForBackend(maxWait = 60) {
  if (!isTauri()) return true

  return new Promise(async (resolve) => {
    let resolved = false
    let unlisten = null

    const done = (val) => {
      if (resolved) return
      resolved = true
      if (unlisten) { try { unlisten() } catch {} }
      resolve(val)
    }

    // 超时兜底
    const timeout = setTimeout(() => done(false), maxWait * 1000)

    // 方案1：监听 Rust emit 的 backend-ready 事件（最快，不受 CSP 限制）
    try {
      const { listen } = await import('@tauri-apps/api/event')
      unlisten = await listen('backend-ready', () => {
        clearTimeout(timeout)
        done(true)
      })
    } catch (e) {
      console.warn('无法监听 backend-ready 事件，将使用轮询降级', e)
    }

    // 方案2：同时轮询 invoke check_backend（Rust IPC，无 CSP 限制）
    // 再配合 fetch 确认 HTTP 可达
    ;(async () => {
      while (!resolved) {
        try {
          const { invoke } = await import('@tauri-apps/api/core')
          const backendStarted = await invoke('check_backend')
          if (backendStarted) {
            // 进程已启动，确认 HTTP 可达
            try {
              const resp = await fetch('http://127.0.0.1:8000/', {
                signal: AbortSignal.timeout(2000)
              })
              if (resp.ok) {
                clearTimeout(timeout)
                done(true)
                return
              }
            } catch {
              // HTTP 还没通，继续等
            }
          }
        } catch {
          // invoke 暂不可用，继续等
        }
        await new Promise(r => setTimeout(r, 1000))
      }
    })()
  })
}
