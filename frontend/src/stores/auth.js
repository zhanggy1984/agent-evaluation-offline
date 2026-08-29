import { defineStore } from 'pinia'
import * as authApi from '../api/auth'

// 会话状态：token/refresh/role/must_change_password 持久化到 localStorage
export const useAuthStore = defineStore('auth', {
  state: () => ({
    access_token: localStorage.getItem('access_token') || '',
    refresh_token: localStorage.getItem('refresh_token') || '',
    role: localStorage.getItem('role') || '',
    username: localStorage.getItem('username') || '',
    must_change_password: localStorage.getItem('must_change_password') === 'true',
  }),
  getters: {
    isLoggedIn: (s) => !!s.access_token,
    isAdmin: (s) => s.role === 'admin',
  },
  actions: {
    async login(username, password) {
      const data = await authApi.login(username, password)
      this.setSession(data)
      // login 响应不含 username，需 /me 补齐（失败不阻塞登录，state 已有 token）
      try {
        await this.fetchMe()
      } catch (e) {
        // 已由拦截器提示
      }
      return data
    },
    // 登录响应与 /me 双源写入（/me 刷新时必须传 id，见 fetchMe）
    setSession(data) {
      this.access_token = data.access_token
      this.refresh_token = data.refresh_token
      this.role = data.role || this.role
      this.username = data.username || this.username
      this.must_change_password = !!data.must_change_password
      localStorage.setItem('access_token', this.access_token)
      localStorage.setItem('refresh_token', this.refresh_token)
      localStorage.setItem('role', this.role)
      localStorage.setItem('username', this.username)
      localStorage.setItem('must_change_password', String(this.must_change_password))
    },
    // P2-B1 refresh 轮换成功后仅更新 token/role：refresh 响应不含 username/must_change_password，
    // 复用 setSession 会把强制改密标记误清（首登改密中 token 过期 refresh 后应仍强制改密）
    applyRefreshedTokens(data) {
      this.$patch({ access_token: data.access_token, refresh_token: data.refresh_token })
      if (data.role) this.role = data.role
      localStorage.setItem('access_token', this.access_token)
      localStorage.setItem('refresh_token', this.refresh_token)
      localStorage.setItem('role', this.role)
    },
    async fetchMe() {
      const data = await authApi.me()
      this.username = data.username
      this.role = data.role
      this.must_change_password = !!data.must_change_password
      localStorage.setItem('username', this.username)
      localStorage.setItem('role', this.role)
      localStorage.setItem('must_change_password', String(this.must_change_password))
      return data
    },
    // 改密成功后后端全族 refresh 撤销 → 必须清态重新登录
    async changePassword(old_password, new_password) {
      await authApi.changePassword(old_password, new_password)
      this.clear()
    },
    async logout() {
      try {
        if (this.refresh_token) await authApi.logout(this.refresh_token)
      } catch (e) {
        // 登出接口失败也继续本地清态
      } finally {
        this.clear()
      }
    },
    clear() {
      this.access_token = ''
      this.refresh_token = ''
      this.role = ''
      this.username = ''
      this.must_change_password = false
      localStorage.clear()
    },
  },
})
