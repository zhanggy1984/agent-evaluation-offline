import http from './http'

// GET /users?page&page_size&role → {items, total, page, page_size}
export const listUsers = (params) => http.get('/api/users', { params })

// POST /users {username, password, role}
export const createUser = (data) => http.post('/api/users', data)

// PUT /users/{id} {role?, enabled?, password?}
export const updateUser = (id, data) => http.put(`/api/users/${id}`, data)

// DELETE /users/{id} → 软禁
export const disableUser = (id) => http.delete(`/api/users/${id}`)
