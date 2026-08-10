/**
 * Tauri 环境检测与后端就绪等待
 * 桌面端：等待后端进程启动完成再展示界面
 * 浏览器端：直接跳过
 */

/** 判断是否在 Tauri 桌面环境中运行 */
export const isTauri = () => {
  return typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window
}

/**
 * 等待后端 API 就绪（轮询 /api/stats）
 * @param {number} maxWait 最大等待秒数
 * @returns {Promise<boolean>} 是否就绪
 */
export async function waitForBackend(maxWait = 30) {
  const BASE = 'http://127.0.0.1:8000'
  const start = Date.now()

  while (Date.now() - start < maxWait * 1000) {
    try {
      const resp = await fetch(`${BASE}/`, { signal: AbortSignal.timeout(2000) })
      if (resp.ok) return true
    } catch {
      // 后端还没起来，继续等
    }
    await new Promise(r => setTimeout(r, 800))
  }
  return false
}

/**
 * 监听 Tauri 后端就绪事件
 */
export async function onBackendReady(callback) {
  if (!isTauri()) {
    callback()
    return
  }
  try {
    const { listen } = await import('@tauri-apps/api/event')
    const unlisten = await listen('backend-ready', () => {
      unlisten()
      callback()
    })
  } catch {
    // 降级：直接轮询
    const ready = await waitForBackend(30)
    if (ready) callback()
  }
}
