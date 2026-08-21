<template>
  <div class="dashboard">
    <!-- 顶部操作区 -->
    <el-card shadow="never" class="block">
      <div class="toolbar">
        <el-select
          v-model="activeAgentId"
          placeholder="选择 Agent 下钻"
          clearable
          filterable
          style="width: 240px"
          @change="onAgentChange"
        >
          <el-option v-for="a in agentOptions" :key="a.id" :label="a.name" :value="a.id" />
        </el-select>
        <el-button v-if="isStaff" type="primary" @click="openTrigger">触发评测</el-button>
        <el-button :loading="loading" @click="refreshAll">刷新</el-button>
      </div>
    </el-card>

    <!-- L0 门禁墙 -->
    <el-card shadow="never" class="block">
      <template #header>
        <TermTip term="gate" />
        <el-tag size="small" type="info" style="margin-left: 8px"><TermTip term="L0" /></el-tag>
      </template>
      <el-row :gutter="12">
        <el-col v-for="g in gateList" :key="g.agent_id" :xs="24" :sm="12" :md="8" :lg="6">
          <div
            class="gate-card"
            :class="{ active: g.agent_id === activeAgentId }"
            @click="selectAgent(g)"
          >
            <div class="gate-head">
              <span class="gate-name">{{ g.agent_name }}</span>
              <el-tag v-if="g.version" size="small">{{ g.version }}</el-tag>
              <el-tag v-else size="small" type="info">未评测</el-tag>
            </div>
            <div class="gate-score">
              <template v-if="g.agent_score != null">
                <!-- agent_score 为 0-100 百分制（scorer score_case 合成），进度条直接取分值 -->
                <div class="score-num">{{ fmtScore(g.agent_score) }}<span class="score-unit"> / 100</span></div>
                <el-progress :percentage="Math.min(100, Math.round(g.agent_score || 0))" :stroke-width="10" />
              </template>
              <el-empty v-else description="暂无评分" :image-size="42" />
            </div>
            <div class="gate-meta">
              <span>通过率 {{ fmtRate(g.pass_rate) }}</span>
              <span>{{ g.pass_case }}/{{ g.total_case }} 用例</span>
            </div>
            <!-- 6.4b 过拟合降级：manual run 分 vs 最近 held_out run 分差超阈值（gate.overfit 由后端 build_gate_cards 注入） -->
            <div v-if="g.overfit && g.overfit_gap != null" class="gate-stale">
              <el-tag size="small" type="danger">过拟合 Δ{{ g.overfit_gap }}</el-tag>
            </div>
            <div v-if="g.stale_suites && g.stale_suites.length" class="gate-stale">
              <el-tag v-for="s in g.stale_suites" :key="s.id" size="small" type="warning">
                陈旧:{{ s.name }}
              </el-tag>
            </div>
          </div>
        </el-col>
      </el-row>
    </el-card>

    <!-- run 列表 -->
    <el-card shadow="never" class="block">
      <template #header>
        <div class="run-toolbar">
          <span>评测记录</span>
          <el-input
            v-model="runSearch"
            placeholder="搜索 ID / Agent / 状态"
            clearable
            size="small"
            style="width: 220px; margin-left: 12px"
          />
          <el-select v-model="runStatusFilter" placeholder="状态筛选" clearable size="small" style="width: 130px; margin-left: 8px">
            <el-option v-for="(v, k) in RUN_STATUS" :key="k" :label="v.label" :value="k" />
          </el-select>
          <span class="dim-note" style="margin-left: 8px">共 {{ filteredRuns.length }} 条</span>
          <el-text v-if="hasMoreRuns" type="warning" size="small" style="margin-left: 8px">
            run 记录超 200 条，仅展示最新 200 条，更早记录未覆盖
          </el-text>
        </div>
      </template>
      <el-table :data="pagedRuns" v-loading="runsLoading" size="small" stripe>
        <el-table-column prop="id" label="ID" width="64" />
        <el-table-column label="Agent" width="160">
          <template #default="{ row }">{{ agentName(row.agent_id) }}</template>
        </el-table-column>
        <el-table-column prop="version" label="版本" width="100" />
        <el-table-column label="状态" width="160">
          <template #default="{ row }">
            <el-tag :type="runStatus(row.status).type" size="small">{{ runStatus(row.status).label }}</el-tag>
            <el-tag v-if="row.fail_case > 0" size="small" type="warning" style="margin-left: 4px">
              {{ row.fail_case }} 未达标
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="评分" width="80">
          <template #default="{ row }">{{ fmtScore(row.agent_score) }}</template>
        </el-table-column>
        <el-table-column label="通过率" width="90">
          <template #default="{ row }">
            {{ fmtRate(row.total_case ? row.pass_case / row.total_case : null) }}
          </template>
        </el-table-column>
        <el-table-column label="开始时间" min-width="160">
          <template #default="{ row }">{{ fmtTime(row.started_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="170" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openRunDetail(row)">明细</el-button>
            <el-button v-if="isStaff && isActive(row.status)" link type="warning" size="small" @click="doCancel(row)">
              取消
            </el-button>
            <el-button v-if="isStaff && !isActive(row.status)" link type="primary" size="small" @click="doRerun(row)">
              重跑
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination
        v-if="filteredRuns.length > PAGE_SIZE"
        layout="prev, pager, next, total"
        :total="filteredRuns.length"
        :page-size="PAGE_SIZE"
        :current-page="runPage"
        @current-change="(p) => (runPage = p)"
        style="margin-top: 8px; justify-content: flex-end"
      />
    </el-card>

    <!-- L1 趋势 -->
    <el-card v-if="activeAgentId" shadow="never" class="block">
      <template #header>
        <span>评测趋势</span>
        <el-tag size="small" type="info" style="margin-left: 8px"><TermTip term="L1" /></el-tag>
      </template>
      <EChart v-if="trendData.length" :option="trendOption" height="280px" />
      <el-empty v-else description="该 agent 暂无终态评测记录" />
    </el-card>

    <!-- L2 版本对比 -->
    <el-card v-if="activeAgentId" shadow="never" class="block">
      <template #header>
        <span>版本对比</span>
        <el-tag size="small" type="info" style="margin-left: 8px"><TermTip term="L2" /></el-tag>
      </template>
      <div class="compare-bar">
        <el-select v-model="compareA" placeholder="Run A" filterable style="width: 240px">
          <el-option
            v-for="r in trendData"
            :key="r.run_id"
            :value="r.run_id"
            :label="`#${r.run_id} ${r.version} ${fmtTime(r.started_at)}`"
          />
        </el-select>
        <span class="vs">vs</span>
        <el-select v-model="compareB" placeholder="Run B" filterable style="width: 240px">
          <el-option
            v-for="r in trendData"
            :key="r.run_id"
            :value="r.run_id"
            :label="`#${r.run_id} ${r.version} ${fmtTime(r.started_at)}`"
          />
        </el-select>
        <el-button type="primary" :disabled="!compareA || !compareB || compareA === compareB" @click="doCompare">
          对比
        </el-button>
      </div>
      <template v-if="compareData">
        <EChart :option="compareOption" height="300px" />
        <el-table :data="compareData.dims" size="small" stripe style="margin-top: 8px">
          <el-table-column label="维度">
            <template #default="{ row }">{{ DIM_LABEL[row.code] || row.code }}</template>
          </el-table-column>
          <el-table-column label="版本 A 均值">
            <template #default="{ row }">{{ fmtScore(row.mean_a) }}</template>
          </el-table-column>
          <el-table-column label="版本 B 均值">
            <template #default="{ row }">{{ fmtScore(row.mean_b) }}</template>
          </el-table-column>
          <el-table-column label="Δ">
            <template #default="{ row }">{{ fmtScore(row.delta) }}</template>
          </el-table-column>
          <el-table-column label="显著性（2σ）" width="150">
            <template #default="{ row }">
              <span v-if="row.significant === 'up'" class="sig up">↑ 显著提升</span>
              <span v-else-if="row.significant === 'down'" class="sig down">↓ 显著下降</span>
              <span v-else-if="row.significant === 'flat'" class="sig flat">持平</span>
              <span v-else class="sig na">数据不足</span>
            </template>
          </el-table-column>
        </el-table>
      </template>
    </el-card>

    <!-- L3 用例明细 + L3.5 门禁失败摘要（drawer 弹出） -->
    <el-drawer v-model="detailVisible" size="720px" direction="rtl">
      <template #header>
        <span>用例明细</span>
        <el-tag size="small" type="info" style="margin-left: 8px"><TermTip term="L3" /></el-tag>
        <el-tag v-if="curRunId" size="small" style="margin-left: 8px">run #{{ curRunId }}</el-tag>
        <span v-if="isStaff" class="export-bar">
          <el-button size="small" type="primary" :loading="exporting" @click="doExport('pdf')">导出 PDF</el-button>
          <el-button size="small" :loading="exporting" @click="doExport('xlsx')">导出 Excel</el-button>
        </span>
      </template>
      <el-table :data="runResults" v-loading="resultsLoading" size="small" stripe>
        <el-table-column prop="case_name" label="用例" min-width="160" show-overflow-tooltip />
        <el-table-column prop="interface_name" label="接口" width="120" />
        <el-table-column label="结果" width="80">
          <template #default="{ row }">
            <el-tag :type="pfTag(row.pass_fail)" size="small">{{ pfLabel(row.pass_fail) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="总分" width="80">
          <template #default="{ row }">{{ fmtScore(row.score_total) }}</template>
        </el-table-column>
        <el-table-column label="维度分" min-width="220">
          <template #default="{ row }">
            <div v-for="d in row.score_per_dimension" :key="d.code" class="dim-line">
              <span class="dim-label">{{ DIM_LABEL[d.code] || d.code }}</span>
              <span v-if="d.na" class="dim-na">N/A{{ d.na_reason ? '（' + d.na_reason + '）' : '' }}</span>
              <span v-else>{{ fmtScore(d.value) }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="错误" min-width="140" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.error_type" class="err">{{ row.error_type }}: {{ row.error_detail }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="80" fixed="right">
          <template #default="{ row }">
            <el-button v-if="isStaff" link type="primary" size="small" @click="openEvidence(row)">证据</el-button>
          </template>
        </el-table-column>
      </el-table>
      <!-- L3.5 门禁失败摘要 -->
      <div v-if="runFailures.length" class="drawer-failures">
        <div class="drawer-failures-title">
          <span>门禁失败摘要</span>
          <el-tag size="small" type="warning" style="margin-left: 8px"><TermTip term="L3.5" /></el-tag>
        </div>
        <el-collapse>
          <el-collapse-item v-for="f in runFailures" :key="f.case_id" :name="f.case_id">
            <template #title>
              <span class="fail-title">{{ f.case_name }}</span>
              <el-tag size="small" type="danger" style="margin-left: 8px">fail</el-tag>
              <el-tag v-if="f.interface_name" size="small" type="info" style="margin-left: 8px">
                {{ f.interface_name }}
              </el-tag>
              <span class="fail-score">score: {{ fmtScore(f.score_total) }}</span>
            </template>
            <div v-if="f.fail_dims.length" class="fail-sec">
              <div class="fail-sec-title">不达标维度</div>
              <div v-for="d in f.fail_dims" :key="d.code" class="fail-dim">
                {{ DIM_LABEL[d.code] || d.code }}: {{ fmtScore(d.value) }} &lt; <TermTip term="target" label="target" /> {{ fmtScore(d.target) }}
              </div>
            </div>
            <div v-if="f.assertion_failures.length" class="fail-sec">
              <div class="fail-sec-title">断言失败</div>
              <div v-for="(a, i) in f.assertion_failures" :key="i" class="fail-dim">
                [{{ a.dimension || '-' }}] {{ a.op }} args={{ JSON.stringify(a.args) }} → actual={{ a.actual }}
              </div>
            </div>
            <div v-if="f.judge_failures.length" class="fail-sec">
              <div class="fail-sec-title">judge 判定不达标</div>
              <div v-for="(j, i) in f.judge_failures" :key="i" class="fail-dim">
                {{ DIM_LABEL[j.dimension] || j.dimension }}: {{ fmtScore(j.score) }} — {{ j.reason }}
              </div>
            </div>
          </el-collapse-item>
        </el-collapse>
      </div>
    </el-drawer>

    <!-- 触发评测 dialog -->
    <el-dialog v-model="triggerVisible" title="触发评测" width="480px">
      <el-form label-width="90px">
        <el-form-item label="Agent">
          <el-select v-model="triggerForm.agent_id" style="width: 100%" @change="onTriggerAgentChange">
            <el-option v-for="a in agentOptions" :key="a.id" :label="a.name" :value="a.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="测试套件">
          <el-select
            v-model="triggerForm.suite_id"
            style="width: 100%"
            placeholder="选择套件"
            :disabled="!triggerForm.agent_id"
          >
            <el-option
              v-for="s in suiteOptions"
              :key="s.id"
              :label="`${s.name}（${s.case_count ?? '-'} 用例）`"
              :value="s.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="版本">
          <el-input v-model="triggerForm.version" placeholder="semver，如 1.2.3" />
        </el-form-item>
        <el-form-item label="触发类型">
          <el-radio-group v-model="triggerForm.trigger_type">
            <el-radio value="manual">常规评测</el-radio>
            <el-radio value="held_out">留出集复测</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-alert
          type="warning"
          :closable="false"
          show-icon
          title="将真实调用 agent（LLM 耗时/费用），请确认版本无误"
        />
      </el-form>
      <template #footer>
        <el-button @click="triggerVisible = false">取消</el-button>
        <el-button type="primary" :loading="triggering" @click="doTrigger">确认触发</el-button>
      </template>
    </el-dialog>

    <!-- L4 证据 dialog -->
    <el-dialog v-model="evidenceVisible" width="860px" top="4vh">
      <template #header>评测证据（<TermTip term="L4" />）</template>
      <div v-if="evidence" v-loading="evidenceLoading" class="evidence">
        <el-collapse>
          <el-collapse-item title="Answer（agent 最终回答）" name="answer">
            <pre class="pre">{{ evidence.answer || '(空)' }}</pre>
          </el-collapse-item>
          <el-collapse-item title="Reasoning（思考链全文）" name="reasoning">
            <pre class="pre">{{ evidence.reasoning || '(空)' }}</pre>
          </el-collapse-item>
          <el-collapse-item v-if="evidence.judge_results && evidence.judge_results.length" title="Judge 判定" name="judge">
            <div v-for="(j, i) in evidence.judge_results" :key="i" class="judge-row">
              <el-tag size="small">{{ DIM_LABEL[j.dimension] || j.dimension }}</el-tag>
              <span class="judge-score">score={{ fmtScore(j.score) }}</span>
              <div class="judge-reason">{{ j.reason }}</div>
            </div>
          </el-collapse-item>
          <el-collapse-item
            v-if="evidence.assertion_results && evidence.assertion_results.length"
            title="断言详情"
            name="assert"
          >
            <div v-for="(a, i) in evidence.assertion_results" :key="i" class="assert-row">
              <div>
                <span class="assert-op">[{{ a.dimension || '-' }}] {{ a.op }}</span>
                <span v-if="a.pass" class="sig up">PASS</span>
                <span v-else class="sig down">FAIL</span>
              </div>
              <pre class="pre">{{ JSON.stringify(a, null, 2) }}</pre>
            </div>
          </el-collapse-item>
          <el-collapse-item v-if="evidence.tool_calls && evidence.tool_calls.length" title="工具调用" name="tools">
            <pre class="pre">{{ JSON.stringify(evidence.tool_calls, null, 2) }}</pre>
          </el-collapse-item>
          <el-collapse-item title="Usage / Timing" name="meta">
            <pre class="pre">{{ JSON.stringify({ usage: evidence.usage, timing: evidence.timing }, null, 2) }}</pre>
          </el-collapse-item>
          <el-collapse-item v-if="evidence.error_type" title="错误信息" name="err">
            <pre class="pre">{{ evidence.error_type }}: {{ evidence.error_detail }}</pre>
          </el-collapse-item>
        </el-collapse>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useAuthStore } from '../stores/auth'
import TermTip from '../components/TermTip.vue'
import { listAgents } from '../api/agents'
import { listSuites } from '../api/suites'
import {
  listRuns, createRun, cancelRun, rerunRun,
  listRunResults, listRunFailures, getResultEvidence,
} from '../api/runs'
import { getGate, getTrend, getCompare } from '../api/dashboard'
import { createExport, downloadExport } from '../api/exports'
import EChart from '../components/EChart.vue'
import { filterRuns, paginate } from '../utils/runFilter'

// ---- 常量 ----
const DIM_LABEL = {
  completeness: '完成度',
  factuality: '事实性',
  reasoning_quality: '思考链',
  tool_usage: '工具使用',
  ttft: '首字延迟',
  e2e: '端到端延迟',
  token_cost: 'Token 成本',
}
const RUN_STATUS = {
  pending: { label: '等待中', type: 'info' },
  running: { label: '执行中', type: 'primary' },
  scoring: { label: '评分中', type: 'warning' },
  scoring_failed: { label: '评分失败', type: 'danger' },
  completed: { label: '完成', type: 'success' },
  partial_failed: { label: '部分失败', type: 'danger' },
  timeout: { label: '超时', type: 'danger' },
  cancelled: { label: '已取消', type: 'info' },
}
const PF = {
  pass: { label: '通过', type: 'success' },
  fail: { label: '失败', type: 'danger' },
  error: { label: '错误', type: 'warning' },
  na: { label: 'N/A', type: 'info' },
}
const ACTIVE = ['pending', 'running', 'scoring']
const SEMVER = /^\d+\.\d+\.\d+/

const auth = useAuthStore()
const { isAdmin } = storeToRefs(auth)
const isStaff = computed(() => ['admin', 'evaluator'].includes(auth.role))

// ---- 数据 ----
const loading = ref(false)
const gateList = ref([])
const agentOptions = ref([])
const activeAgentId = ref(null)
const runsLoading = ref(false)
const runList = ref([])
const hasMoreRuns = ref(false) // 后端 limit 上限 200，run 量超出时提示仅展示最新
const trendData = ref([])
const compareA = ref(null)
const compareB = ref(null)
const compareData = ref(null)
const curRunId = ref(null)
const resultsLoading = ref(false)
const runResults = ref([])
const exporting = ref(false)
const runFailures = ref([])
const triggerVisible = ref(false)
const triggering = ref(false)
const triggerForm = reactive({ agent_id: null, suite_id: null, version: '', trigger_type: 'manual' })
const suiteOptions = ref([])
const evidenceVisible = ref(false)
const evidenceLoading = ref(false)
const evidence = ref(null)
const detailVisible = ref(false) // L3 用例明细抽屉

let pollTimer = null
let pollCount = 0

// ---- 格式化 ----
const agentName = (id) => (agentOptions.value.find((a) => a.id === id) || {}).name || `#${id}`
const runStatus = (s) => RUN_STATUS[s] || { label: s, type: 'info' }
const pfTag = (v) => (PF[v] || {}).type || 'info'
const pfLabel = (v) => (PF[v] || {}).label || v
const isActive = (s) => ACTIVE.includes(s)
const fmtTime = (t) => (t ? new Date(t).toLocaleString('zh-CN') : '-')
const fmtRate = (r) => (r == null ? 'N/A' : `${(r * 100).toFixed(1)}%`)
// 分数统一兜底：保留 2 位小数并去尾零（避免 100.0 / 2.092 这类长尾展示，走查 #3）
const fmtScore = (v) => (v == null ? 'N/A' : Number(Number(v).toFixed(2)))

// ---- 图表 option ----
const trendOption = computed(() => ({
  tooltip: {
    trigger: 'axis',
    formatter: (params) => {
      const r = trendData.value[params[0].dataIndex]
      return [
        `#${r.run_id} ${r.version}（${runStatus(r.status).label}）`,
        `总分：${fmtScore(r.agent_score)}`,
        `通过率：${fmtRate(r.pass_rate)}（${r.pass_case}/${r.total_case}）`,
        `TTFT p50：${fmtScore(r.ttft_p50)}`,
        `E2E p50：${fmtScore(r.e2e_p50)}`,
        `${fmtTime(r.started_at)}`,
      ].join('<br/>')
    },
  },
  grid: { left: 48, right: 24, top: 24, bottom: 32 },
  xAxis: {
    type: 'category',
    data: trendData.value.map((r) => `#${r.run_id}\n${r.version}`),
  },
  yAxis: { type: 'value', min: 0, max: 100, name: '总分' },
  series: [
    {
      type: 'line',
      smooth: true,
      symbolSize: 8,
      data: trendData.value.map((r) => r.agent_score),
      lineStyle: { width: 2 },
    },
  ],
}))

const headLabel = (h) => `#${h.run_id} ${h.version}`

const compareOption = computed(() => {
  if (!compareData.value) return {}
  const dims = compareData.value.dims
  return {
    tooltip: { trigger: 'axis' },
    legend: { data: [headLabel(compareData.value.a), headLabel(compareData.value.b)] },
    grid: { left: 48, right: 24, top: 36, bottom: 28 },
    xAxis: {
      type: 'category',
      data: dims.map((d) => DIM_LABEL[d.code] || d.code),
    },
    yAxis: { type: 'value', min: 0, max: 100, name: '维度均分' },
    series: [
      {
        name: headLabel(compareData.value.a),
        type: 'bar',
        data: dims.map((d) => d.mean_a),
        itemStyle: { color: '#5470c6' },
      },
      {
        name: headLabel(compareData.value.b),
        type: 'bar',
        data: dims.map((d) => d.mean_b),
        itemStyle: { color: '#91cc75' },
      },
    ],
  }
})

// ---- 加载 ----
async function refreshAll() {
  loading.value = true
  try {
    await Promise.all([loadAgents(), loadGate(), loadRuns()])
    if (activeAgentId.value) await loadTrend(activeAgentId.value)
  } finally {
    loading.value = false
  }
}

async function loadAgents() {
  const data = await listAgents()
  agentOptions.value = data
}

async function loadGate() {
  gateList.value = await getGate()
}

async function loadRuns() {
  runsLoading.value = true
  try {
    // 全量拉取（后端 limit 上限 200）→ 前端本地过滤 + 本地分页，避免 offset 分页与本地过滤冲突
    runList.value = await listRuns({ limit: 200 })
    runPage.value = 1
    // 截断探测：取第 201 条判断是否还有更早记录（复用现有 offset 参数，零后端改动）；失败静默不阻断
    try {
      const extra = await listRuns({ limit: 1, offset: 200 })
      hasMoreRuns.value = extra.length > 0
    } catch (e) {
      hasMoreRuns.value = false
    }
  } finally {
    runsLoading.value = false
  }
}

// ---- 评测记录分页/搜索（run 量 <200，本地过滤足够；超限再评估后端加 search 参数） ----
const runSearch = ref('')
const runStatusFilter = ref('')
const runPage = ref(1)
const PAGE_SIZE = 20
const filteredRuns = computed(() =>
  filterRuns(runList.value, {
    statusFilter: runStatusFilter.value,
    search: runSearch.value,
    agentName,
    runStatus,
  })
)
const pagedRuns = computed(() => paginate(filteredRuns.value, runPage.value, PAGE_SIZE))
watch([runSearch, runStatusFilter], () => { runPage.value = 1 })

async function loadTrend(agentId) {
  trendData.value = await getTrend(agentId)
  // 有 ≥2 次终态 run 时自动预选最近两次对比
  const len = trendData.value.length
  if (len >= 2) {
    compareA.value = trendData.value[len - 1].run_id
    compareB.value = trendData.value[len - 2].run_id
    await doCompare()
  } else {
    compareA.value = len ? trendData.value[0].run_id : null
    compareB.value = null
    compareData.value = null
  }
}

function selectAgent(g) {
  activeAgentId.value = g.agent_id
  resetDrill()
  loadTrend(g.agent_id)
}

function onAgentChange() {
  resetDrill()
  if (activeAgentId.value) loadTrend(activeAgentId.value)
}

// 切换 agent 时清空 L2/L3 下钻
function resetDrill() {
  compareA.value = null
  compareB.value = null
  compareData.value = null
  curRunId.value = null
  detailVisible.value = false
  runResults.value = []
  runFailures.value = []
  evidence.value = null
}

async function doCompare() {
  if (!compareA.value || !compareB.value || compareA.value === compareB.value) return
  compareData.value = await getCompare(compareA.value, compareB.value)
}

async function openRunDetail(row) {
  curRunId.value = row.id
  resultsLoading.value = true
  detailVisible.value = true
  try {
    const [results, failures] = await Promise.all([
      listRunResults(row.id),
      listRunFailures(row.id),
    ])
    runResults.value = results
    runFailures.value = failures
  } finally {
    resultsLoading.value = false
  }
}

// ---- 触发评测 ----
function openTrigger() {
  triggerForm.agent_id = activeAgentId.value
  triggerForm.suite_id = null
  triggerForm.version = ''
  triggerForm.trigger_type = 'manual'
  suiteOptions.value = []
  if (triggerForm.agent_id) onTriggerAgentChange()
  triggerVisible.value = true
}

async function onTriggerAgentChange() {
  triggerForm.suite_id = null
  if (!triggerForm.agent_id) {
    suiteOptions.value = []
    return
  }
  suiteOptions.value = await listSuites(triggerForm.agent_id)
}

async function doTrigger() {
  if (!triggerForm.agent_id || !triggerForm.suite_id) {
    ElMessage.warning('请选择 Agent 和测试套件')
    return
  }
  if (!SEMVER.test(triggerForm.version)) {
    ElMessage.warning('版本需符合 semver（如 1.2.3）')
    return
  }
  try {
    await ElMessageBox.confirm(
      '将真实调用 agent 执行评测（LLM 耗时/费用）。确认继续？',
      '触发评测',
      { type: 'warning', confirmButtonText: '确认触发', cancelButtonText: '再想想' }
    )
  } catch {
    return
  }
  triggering.value = true
  try {
    const res = await createRun({
      agent_id: triggerForm.agent_id,
      suite_id: triggerForm.suite_id,
      version: triggerForm.version,
      trigger_type: triggerForm.trigger_type,
    })
    ElMessage.success(`run #${res.id} 已创建，正在执行`)
    triggerVisible.value = false
    await refreshAll()
    startPoll()
  } finally {
    triggering.value = false
  }
}

// 触发后轮询刷新（run 全终态或超时自动停）
function startPoll() {
  stopPoll()
  pollCount = 0
  pollTimer = setInterval(async () => {
    pollCount += 1
    await refreshAll()
    if (pollCount >= 45) stopPoll() // 最多约 3 分钟
    else if (!runList.value.some((r) => isActive(r.status))) stopPoll()
  }, 4000)
}

function stopPoll() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

// ---- run 操作 ----
async function doCancel(row) {
  try {
    await ElMessageBox.confirm(`确认取消 run #${row.id}？`, '取消评测', { type: 'warning' })
  } catch {
    return
  }
  await cancelRun(row.id)
  ElMessage.success('已取消')
  refreshAll()
}

async function doRerun(row) {
  try {
    await ElMessageBox.confirm(
      `确认重跑 run #${row.id}（${row.version}）？将真实调用 agent，耗时/费用。`,
      '重跑评测',
      { type: 'warning' }
    )
  } catch {
    return
  }
  const res = await rerunRun(row.id)
  ElMessage.success(`已创建 run #${res.id}`)
  refreshAll()
  startPoll()
}

// ---- 报告导出（6.1）----
async function doExport(format) {
  if (!curRunId.value) return
  exporting.value = true
  try {
    const data = await createExport(curRunId.value, format)
    await downloadExport(data.token, data.filename)
    ElMessage.success(`${format === 'pdf' ? 'PDF' : 'Excel'} 报告已下载`)
  } catch (e) {
    ElMessage.error(e.message || '导出失败')
  } finally {
    exporting.value = false
  }
}

// ---- L4 证据 ----
async function openEvidence(row) {
  evidenceVisible.value = true
  evidenceLoading.value = true
  evidence.value = null
  try {
    evidence.value = await getResultEvidence(curRunId.value, row.id)
  } finally {
    evidenceLoading.value = false
  }
}

onMounted(() => {
  refreshAll()
})

onBeforeUnmount(() => {
  stopPoll()
})
</script>

<style scoped>
.block {
  margin-bottom: 16px;
}
.toolbar {
  display: flex;
  gap: 12px;
  align-items: center;
}
.run-toolbar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
}
.gate-card {
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  padding: 12px;
  margin-bottom: 12px;
  cursor: pointer;
  transition: all 0.2s;
}
.gate-card:hover {
  border-color: var(--el-color-primary);
  box-shadow: var(--el-box-shadow-light);
}
.gate-card.active {
  border-color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
}
.gate-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}
.gate-name {
  font-weight: 600;
  font-size: 15px;
}
.score-num {
  font-size: 26px;
  font-weight: 700;
  color: var(--el-color-primary);
}
.score-unit {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.gate-score {
  margin-bottom: 8px;
}
.gate-meta {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-bottom: 6px;
}
.gate-stale {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.compare-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}
.vs {
  color: var(--el-text-color-secondary);
}
.export-bar {
  float: right;
  margin-left: auto; /* drawer header 为 flex 容器时靠右 */
}
.export-bar .el-button + .el-button {
  margin-left: 8px;
}
.drawer-failures {
  margin-top: 16px;
}
.drawer-failures-title {
  display: flex;
  align-items: center;
  margin-bottom: 8px;
  font-weight: 600;
}
.sig.up {
  color: #67c23a;
  font-weight: 600;
}
.sig.down {
  color: #f56c6c;
  font-weight: 600;
}
.sig.flat {
  color: var(--el-text-color-secondary);
}
.sig.na {
  color: var(--el-text-color-placeholder);
}
.dim-line {
  display: flex;
  gap: 8px;
  font-size: 12px;
  line-height: 20px;
}
.dim-label {
  color: var(--el-text-color-secondary);
  min-width: 52px;
}
.dim-na {
  color: var(--el-text-color-placeholder);
}
.err {
  color: #f56c6c;
  font-size: 12px;
}
.fail-title {
  font-weight: 600;
}
.fail-score {
  margin-left: 12px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.fail-sec {
  margin-top: 8px;
}
.fail-sec-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
  margin-bottom: 4px;
}
.fail-dim {
  font-size: 13px;
  line-height: 22px;
  padding-left: 8px;
}
.pre {
  background: var(--el-fill-color-light);
  border-radius: 6px;
  padding: 10px;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 360px;
  overflow: auto;
}
.judge-row {
  margin-bottom: 10px;
}
.judge-score {
  margin-left: 8px;
  color: var(--el-color-primary);
  font-weight: 600;
}
.judge-reason {
  font-size: 13px;
  margin-top: 4px;
  padding-left: 8px;
}
.assert-row {
  margin-bottom: 10px;
}
.assert-op {
  font-weight: 600;
  margin-right: 8px;
}
</style>
