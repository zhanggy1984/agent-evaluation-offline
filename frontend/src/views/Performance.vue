<template>
  <div class="page">
    <el-card shadow="never" class="block">
      <div class="toolbar">
        <span class="label">性能面板</span>
        <el-select
          v-model="activeAgentId"
          placeholder="选择 Agent"
          clearable
          filterable
          style="width: 240px"
          @change="onAgentChange"
        >
          <el-option v-for="a in agentOptions" :key="a.id" :label="a.name" :value="a.id" />
        </el-select>
        <el-button :loading="loading" @click="refresh">刷新</el-button>
      </div>
    </el-card>

    <el-card v-if="activeAgentId" shadow="never" class="block">
      <template #header>
        <span>延迟趋势</span>
        <el-tag size="small" type="info" style="margin-left: 8px">终态 run · ms</el-tag>
      </template>
      <EChart v-if="rows.length" :option="perfOption" height="320px" />
      <el-empty v-else description="该 agent 暂无终态评测记录" />
    </el-card>

    <el-card v-if="activeAgentId" shadow="never" class="block">
      <template #header>性能明细</template>
      <el-table :data="rows" size="small" stripe>
        <el-table-column prop="run_id" label="Run" width="70" />
        <el-table-column prop="version" label="版本" width="100" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="runStatus(row.status).type" size="small">{{ runStatus(row.status).label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="TTFT p50" width="110">
          <template #default="{ row }">{{ row.ttft_p50 ?? 'N/A' }}</template>
        </el-table-column>
        <el-table-column label="TTFT p95" width="110">
          <template #default="{ row }">{{ row.ttft_p95 ?? 'N/A' }}</template>
        </el-table-column>
        <el-table-column label="E2E p50" width="110">
          <template #default="{ row }">{{ row.e2e_p50 ?? 'N/A' }}</template>
        </el-table-column>
        <el-table-column label="E2E p95" width="110">
          <template #default="{ row }">{{ row.e2e_p95 ?? 'N/A' }}</template>
        </el-table-column>
        <el-table-column label="开始时间" min-width="160">
          <template #default="{ row }">{{ fmtTime(row.started_at) }}</template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { listAgents } from '../api/agents'
import { getPerf } from '../api/dashboard'
import EChart from '../components/EChart.vue'

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

const agentOptions = ref([])
const activeAgentId = ref(null)
const loading = ref(false)
const rows = ref([])

const runStatus = (s) => RUN_STATUS[s] || { label: s, type: 'info' }
const fmtTime = (t) => (t ? new Date(t).toLocaleString('zh-CN') : '-')

const perfOption = computed(() => {
  const names = ['TTFT p50', 'TTFT p95', 'E2E p50', 'E2E p95']
  const keys = ['ttft_p50', 'ttft_p95', 'e2e_p50', 'e2e_p95']
  return {
    tooltip: { trigger: 'axis' },
    legend: { data: names },
    grid: { left: 48, right: 24, top: 36, bottom: 32 },
    xAxis: {
      type: 'category',
      data: rows.value.map((r) => `#${r.run_id}\n${r.version}`),
    },
    yAxis: { type: 'value', name: 'ms' },
    series: names.map((name, i) => ({
      name,
      type: 'line',
      smooth: true,
      symbolSize: 8,
      data: rows.value.map((r) => r[keys[i]]),
      lineStyle: { width: 2 },
    })),
  }
})

async function load(agentId) {
  loading.value = true
  try {
    rows.value = await getPerf(agentId)
  } finally {
    loading.value = false
  }
}

async function loadAgents() {
  const data = await listAgents()
  agentOptions.value = data
  // 默认选中第一个 agent，页面打开即有内容
  if (data.length && !activeAgentId.value) {
    activeAgentId.value = data[0].id
    await load(data[0].id)
  }
}

function onAgentChange() {
  if (activeAgentId.value) load(activeAgentId.value)
  else rows.value = []
}

function refresh() {
  if (activeAgentId.value) load(activeAgentId.value)
}

onMounted(loadAgents)
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
.label {
  font-size: 15px;
  font-weight: 600;
}
</style>
