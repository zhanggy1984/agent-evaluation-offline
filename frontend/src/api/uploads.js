import http from './http'

// POST /uploads（multipart）→ {file_ref, filename, size}
// 扩展名白名单 pdf/txt/doc/docx/xls/xlsx/csv/json；大小读 file_max_size；UUID 命名
export const uploadFile = (file) => {
  const fd = new FormData()
  fd.append('file', file)
  return http.post('/api/uploads', fd)
}
