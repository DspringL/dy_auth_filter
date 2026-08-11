import axios from 'axios'
import { ElMessage } from 'element-plus'

/**
 * 判断是否在 Tauri 打包环境中运行
 * dev 模式（npm run tauri:dev）也是 Tauri 环境，但有 vite proxy
 * 打包后（tauri build）没有 vite proxy，需要用绝对地址
 */
function getBaseURL() {
  // __TAURI_INTERNALS__ 存在说明在 Tauri WebView 中运行
  const isTauri = typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window
  // TAURI_ENV_DEBUG 为 true 说明是 tauri dev（有 vite proxy）
  // import.meta.env.DEV 在 vite dev server 下为 true
  const isDevMode = import.meta.env.DEV

  if (isTauri && !isDevMode) {
    // 打包后的 Tauri 应用：直接请求后端绝对地址
    return 'http://127.0.0.1:8000/api'
  }
  // dev 模式或浏览器：走 vite proxy 的相对路径
  return '/api'
}

const http = axios.create({
  baseURL: getBaseURL(),
  timeout: 30000,
})

http.interceptors.response.use(
  (res) => res.data,
  (err) => {
    const msg = err.response?.data?.detail || err.message || '请求失败'
    ElMessage.error(msg)
    return Promise.reject(err)
  }
)

// ── 登录 ──────────────────────────────────────────────────────────────────────
export const loginApi = {
  status: () => http.get('/login/status'),
  triggerQrcode: () => http.post('/login/qrcode'),
}

// ── 达人 ──────────────────────────────────────────────────────────────────────
export const kolApi = {
  list:        (params)  => http.get('/kols', { params }),
  detail:      (id)      => http.get(`/kols/${id}`),
  delete:      (id)      => http.delete(`/kols/${id}`),
  deleteBatch: (ids)     => http.delete('/kols', { data: ids }),
}

// ── 任务 ──────────────────────────────────────────────────────────────────────
export const taskApi = {
  run: (data) => http.post('/tasks/run', data),
  status: (id) => http.get(`/tasks/${id}`),
}

// ── 私信 & 评论联系 ───────────────────────────────────────────────────────────
export const dmApi = {
  send:    (sec_user_ids) => http.post('/dm/send', { sec_user_ids }),
  contact: (sec_user_ids, contact_method = 'dm') =>
    http.post('/contact/send', { sec_user_ids, contact_method }),
}

// ── 用户 ID 解析 ───────────────────────────────────────────────────────────────
export const userApi = {
  resolve: (identifiers) => http.post('/users/resolve', { identifiers }),
}

// ── 统计 ──────────────────────────────────────────────────────────────────────
export const statsApi = {
  get: () => http.get('/stats'),
}

// ── 系统配置 ──────────────────────────────────────────────────────────────────
export const configApi = {
  get:  ()     => http.get('/config'),
  save: (data) => http.post('/config', data),
}
