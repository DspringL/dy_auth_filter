import axios from 'axios'
import { ElMessage } from 'element-plus'

/**
 * baseURL 在构建时确定：
 * - tauri build（生产打包）：TAURI_ENV_PLATFORM 有值，__TAURI_PROD__ = true
 *   → 用绝对地址直接请求后端，不走 vite proxy
 * - tauri dev / 浏览器：__TAURI_PROD__ = false
 *   → 用相对路径 /api，走 vite proxy 转发到后端
 */
// eslint-disable-next-line no-undef
const BASE_URL = __TAURI_PROD__ ? 'http://127.0.0.1:8000/api' : '/api'

const http = axios.create({
  baseURL: BASE_URL,
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
