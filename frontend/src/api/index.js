import axios from 'axios'
import { ElMessage } from 'element-plus'

const http = axios.create({
  baseURL: '/api',
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
  list: (params) => http.get('/kols', { params }),
  detail: (id) => http.get(`/kols/${id}`),
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
  get:  ()       => http.get('/config'),
  save: (data)   => http.post('/config', data),
}
