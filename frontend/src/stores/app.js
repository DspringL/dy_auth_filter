import { defineStore } from 'pinia'
import { loginApi, statsApi } from '@/api/index.js'

export const useAppStore = defineStore('app', {
  state: () => ({
    loginStatus: { logged_in: false, message: '' },
    stats: { total_kols: 0, dm_sent: 0, dm_pending: 0, has_shop: 0 },
    loginLoading: false,
  }),
  actions: {
    async fetchLoginStatus() {
      try {
        this.loginStatus = await loginApi.status()
      } catch {}
    },
    async fetchStats() {
      try {
        this.stats = await statsApi.get()
      } catch {}
    },
    async triggerLogin() {
      this.loginLoading = true
      try {
        const res = await loginApi.triggerQrcode()
        return res
      } finally {
        this.loginLoading = false
      }
    },
  },
})
