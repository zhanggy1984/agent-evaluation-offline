import http from './http'

// POST /runs/{id}/export → {token, format, expires_at, filename}（viewer 403）
export const createExport = (runId, format) => http.post(`/api/runs/${runId}/export`, { format })

// GET /exports/{token} 一次性下载（token.user_id 绑定当前用户，viewer 403）
// 用原生 fetch 而非 axios：绕开拦截器 resp.data.data 解包，拿到二进制 blob
export async function downloadExport(token, filename) {
  const resp = await fetch(`/api/exports/${token}`, {
    headers: { Authorization: `Bearer ${localStorage.getItem('access_token')}` },
  })
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}))
    throw new Error(body.message || `下载失败（${resp.status}）`)
  }
  const blob = await resp.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}
