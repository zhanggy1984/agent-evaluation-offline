// #230 批次 3c：看板评测记录过滤 + 分页（纯函数，供 Dashboard.vue 调用 + vitest 单测）
// 关键词命中：run id 包含 / agent 名包含 / 状态 label 包含（大小写不敏感）
export function filterRuns(list, { statusFilter, search, agentName, runStatus }) {
  let result = list
  if (statusFilter) result = result.filter((r) => r.status === statusFilter)
  const kw = (search || '').trim().toLowerCase()
  if (kw) {
    result = result.filter((r) =>
      String(r.id).includes(kw) ||
      agentName(r.agent_id).toLowerCase().includes(kw) ||
      runStatus(r.status).label.toLowerCase().includes(kw)
    )
  }
  return result
}

export function paginate(list, page, pageSize) {
  return list.slice((page - 1) * pageSize, page * pageSize)
}
