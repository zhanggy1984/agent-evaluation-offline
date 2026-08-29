import axios from 'axios'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '../stores/auth'

// 走同源 /api（生产 nginx 反代 backend，dev 走 vite proxy），baseURL 空
const http = axios.create({ baseURL: '', timeout: 15000 })

// P2-B1 refresh 单飞：并发 401 共享同一刷新 Promise。
// 后端 refresh 是轮换制，并发用同一 refresh_token 会触发复用检测→整族撤销，必须只发一次。
let refreshPromise = null

function refreshAccessToken() {
  const refresh_token = localStorage.getItem('refresh_token')
  if (!refresh_token) return Promise.resolve(null)
  if (!refreshPromise) {
    // 裸 axios 而非 http 实例：refresh 自己 401 不会重新进拦截器（防死循环）
    refreshPromise = axios
      .post('/api/auth/refresh', { refresh_token })
      .then((resp) => {
        const auth = useAuthStore()
        auth.applyRefreshedTokens(resp.data.data)
        return auth.access_token
      })
      .catch(() => {
        useAuthStore().clear()
        return null
      })
      .finally(() => {
        refreshPromise = null
      })
  }
  return refreshPromise
}

// 请求：注入 Bearer token
http.interceptors.request.use((config) => {
  const token = localStorage.getItem('access_token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

// 响应：解包后端统一响应 {code:0, message, data}，只把 data 透出给调用方
http.interceptors.response.use(
  (resp) => resp.data.data,
  async (error) => {
    const status = error.response?.status
    const message = error.response?.data?.message || error.message || '请求失败'
    const url = error.config?.url || ''

    // 401 且非登录接口 → 尝试 refresh 换新 token 重放；失败则会话失效清态跳登录
    if (status === 401 && !url.includes('/auth/login')) {
      const config = error.config
      if (config && !config._retried) {
        config._retried = true
        const newToken = await refreshAccessToken()
        if (newToken) {
          config.headers.Authorization = `Bearer ${newToken}`
          return http(config) // 重放原请求（新 token）
        }
      }
      // 已重试仍 401 / refresh 失败 / 无 refresh_token → 会话失效
      useAuthStore().clear()
      if (!window.location.pathname.startsWith('/login')) {
        window.location.href = '/login'
      }
      return Promise.reject(error)
    }
    // 登录失败（401/403/429）等业务错误：提示 message，不跳转
    ElMessage.error(message)
    return Promise.reject(error)
  }
)

export default http
