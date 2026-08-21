import http from './http'

export const login = (username, password) =>
  http.post('/api/auth/login', { username, password })

export const me = () => http.get('/api/auth/me')

export const logout = (refresh_token) =>
  http.post('/api/auth/logout', { refresh_token })

export const changePassword = (old_password, new_password) =>
  http.post('/api/auth/change-password', { old_password, new_password })

export const refresh = (refresh_token) =>
  http.post('/api/auth/refresh', { refresh_token })
