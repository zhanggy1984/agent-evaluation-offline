// 配置中心 key → 中文说明 + 单位 + 精度
// 来源：backend/app/seed.py DEFAULT_SYSTEM_CONFIG（新增配置项时在此同步补充，否则兜底显示原 key）
// precision：比例类（0-1 浮点）用 2，整数类用 0
export const CONFIG_META = {
  // ---- 运行期（run）----
  global_max_inflight: { label: '全局最大并发 run 数', unit: '个', precision: 0, desc: '平台同时进行的评测（run）总数上限，超过排队等待' },
  per_agent_concurrency: { label: '单 agent 并发', unit: '个', precision: 0, desc: '同一 agent 同时执行的用例数上限' },
  case_timeout: { label: '单用例超时', unit: '秒', precision: 0, desc: '每个用例平台调用 agent 的时限，超时该用例判失败' },
  scoring_timeout: { label: '评分超时', unit: '秒', precision: 0, desc: 'judge 判分整体时限，超时 run 转 scoring_failed 兜底' },
  contract_check_timeout: { label: '契约检查超时', unit: '秒', precision: 0, desc: 'run 前契约探测（probe）单个接口的时限' },
  sse_idle_timeout: { label: 'SSE 空闲超时', unit: '秒', precision: 0, desc: 'agent 流式响应空闲多久判定结束' },
  run_timeout: { label: '整 run 超时', unit: '秒', precision: 0, desc: '整个评测时限；留空则按用例数与单用例超时估算' },
  perf_repeat_count: { label: '性能重复次数', unit: '次', precision: 0, desc: '性能/成本维度对同一用例重复评测的次数' },
  judge_concurrency: { label: 'judge 并发', unit: '个', precision: 0, desc: '同时调用 judge LLM 判分的数量' },
  judge_call_timeout: { label: 'judge 调用超时', unit: '秒', precision: 0, desc: '单次 judge LLM 调用的超时上限' },
  judge_na_threshold: { label: 'NA 判定阈值', unit: '比例', precision: 2, desc: '判不出结论（NA）的占比上限，超过则该维度结果不可信' },
  error_rate_block: { label: '错误率熔断阈值', unit: '比例', precision: 2, desc: '用例错误率超过该值即熔断该 agent，暂停下发' },
  assertion_penalty: { label: '断言失败扣分', unit: '分', precision: 0, desc: '断言未通过时该维度的扣分数（百分制）' },
  judge_max_retries: { label: 'judge 最大重试', unit: '次', precision: 0, desc: 'judge 调用失败的最大重试次数' },
  judge_repeat: { label: 'judge 重复判分', unit: '次', precision: 0, desc: '同一用例重复判分次数，取一致结论' },
  breaker_failure_threshold: { label: '熔断失败阈值', unit: '次', precision: 0, desc: '连续失败多少次触发熔断' },
  breaker_open_duration: { label: '熔断打开时长', unit: '秒', precision: 0, desc: '熔断打开后多久尝试恢复' },
  breaker_half_open_probe: { label: '半开探测数', unit: '个', precision: 0, desc: '熔断半开状态放行的探测请求数' },
  retry_backoff_max: { label: '重试退避上限', unit: '秒', precision: 0, desc: '重试指数退避的最大等待时间' },
  max_retries: { label: '最大重试次数', unit: '次', precision: 0, desc: '平台调用 agent 失败的最大重试次数' },
  // ---- 进程级（global）----
  retain_runs: { label: 'run 保留次数', unit: '次', precision: 0, desc: '每个 agent+套件保留的历史 run 数，超出部分清理' },
  heartbeat_interval: { label: '心跳间隔', unit: '秒', precision: 0, desc: 'orchestrator 心跳间隔（非热生效，改需重启）' },
  'judge_llm.base_url': { label: 'judge LLM 地址', unit: 'URL', precision: 0, desc: '判分 LLM 的服务地址（密钥走 env）' },
  'judge_llm.model_name': { label: 'judge LLM 模型', unit: '', precision: 0, desc: '判分 LLM 的模型名' },
  // ---- 注册期（registration）----
  file_max_size: { label: '附件大小上限', unit: 'MB', precision: 0, desc: '用例附件上传的最大体积' },
  base_url_allowlist: { label: 'Agent 地址白名单', unit: '', precision: 0, desc: '注册 agent 时允许的 base_url 网段（SSRF 防护）' },
  llm_allowlist: { label: 'judge LLM 白名单', unit: '', precision: 0, desc: 'judge 出站 LLM 域名白名单（SSRF 防护）' },
}
