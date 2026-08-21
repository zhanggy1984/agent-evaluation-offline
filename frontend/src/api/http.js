import axios from 'axios'
import { ElMessage } from 'element-plus'

// 走同源 /api（生产 nginx 反代 backend，dev 走 vite proxy），baseURL 空
const http = axios.create({ baseURL: '', timeout: 15000 })

// 请求：注入 Bearer token
http.interceptors.request.use((config) => {
  const token = localStorage.getItem('access_token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

// 响应：解包后端统一响应 {code:0, message, data}，只把 data 透出给调用方
http.interceptors.response.use(
  (resp) => resp.data.data,
  (error) => {
    const status = error.response?.status
    const message = error.response?.data?.message || error.message || '请求失败'
    const url = error.config?.url || ''

    // 401 且非登录接口 → 会话失效，清态跳登录
    if (status === 401 && !url.includes('/auth/login')) {
      localStorage.clear()
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
