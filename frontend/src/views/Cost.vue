<template>
  <div class="page">
    <el-card shadow="never" class="block">
      <div class="toolbar">
        <span class="label">成本面板</span>
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
        <el-alert
          v-if="activeAgentId && rows.length && rows.every((r) => r.total_cost == null)"
          type="warning"
          :closable="false"
          show-icon
          title="该模型未配置单价，成本为 N/A（见下方模型单价表）"
          style="flex: 1"
        />
      </div>
    </el-card>

    <el-card v-if="activeAgentId" shadow="never" class="block">
      <template #header>
        <span>成本趋势</span>
        <el-tag size="small" type="info" style="margin-left: 8px">终态 run</el-tag>
      </template>
      <EChart v-if="rows.length" :option="costOption" height="320px" />
      <el-empty v-else description="该 agent 暂无终态评测记录" />
    </el-card>

    <el-card v-if="activeAgentId" shadow="never" class="block">
      <template #header>成本明细</template>
      <el-table :data="rows" size="small" stripe>
        <el-table-column prop="run_id" label="Run" width="70" />
        <el-table-column prop="version" label="版本" width="100" />
        <el-table-column prop="model" label="模型" min-width="140" />
        <el-table-column label="Token 总量" width="120">
          <template #default="{ row }">{{ row.total_tokens ?? 'N/A' }}</template>
        </el-table-column>
        <el-table-column label="成本(元)" width="110">
          <template #default="{ row }">{{ fmtCost(row.total_cost) }}</template>
        </el-table-column>
        <el-table-column label="开始时间" min-width="160">
          <template #default="{ row }">{{ fmtTime(row.started_at) }}</template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card v-if="activeAgentId" shadow="never" class="block">
      <template #header>
        <span>模型单价</span>
        <el-tag size="small" type="info" style="margin-left: 8px">成本(元) = (输入token×输入价 + 输出token×输出价) ÷ 1e6，单价单位：元/百万 token</el-tag>
      </template>
      <el-table :data="prices" size="small" stripe>
        <el-table-column prop="model" label="模型" min-width="180" />
        <el-table-column label="输入单价(元/百万)" width="150">
          <template #default="{ row }">{{ row.input_price ?? 'N/A' }}</template>
        </el-table-column>
        <el-table-column label="输出单价(元/百万)" width="150">
          <template #default="{ row }">{{ row.output_price ?? 'N/A' }}</template>
        </el-table-column>
        <el-table-column label="生效时间" min-width="160">
          <template #default="{ row }">{{ fmtTime(row.effective_from) }}</template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!prices.length" description="暂无单价配置" />
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { listAgents } from '../api/agents'
import { listModelPrices } from '../api/config'
import { getCost } from '../api/dashboard'
import EChart from '../components/EChart.vue'

const agentOptions = ref([])
const activeAgentId = ref(null)
const loading = ref(false)
const rows = ref([])
const prices = ref([])

const fmtTime = (t) => (t ? new Date(t).toLocaleString('zh-CN') : '-')
// 成本金额兜底：最多 6 位小数去尾零（cost=tokens×单价/1e6，长尾小数是正常精度，走查 #1）
const fmtCost = (v) => (v == null ? 'N/A' : Number(v).toFixed(6).replace(/\.?0+$/, ''))

const costOption = computed(() => ({
  tooltip: { trigger: 'axis' },
  legend: { data: ['Token 总量', '成本(元)'] },
  grid: { left: 56, right: 56, top: 36, bottom: 32 },
  xAxis: {
    type: 'category',
    data: rows.value.map((r) => `#${r.run_id}\n${r.version}`),
  },
  yAxis: [
    { type: 'value', name: 'tokens' },
    { type: 'value', name: '元' },
  ],
  series: [
    {
      name: 'Token 总量',
      type: 'bar',
      data: rows.value.map((r) => r.total_tokens),
      itemStyle: { color: '#5470c6' },
    },
    {
      name: '成本(元)',
      type: 'line',
      yAxisIndex: 1,
      smooth: true,
      data: rows.value.map((r) => r.total_cost), // 无单价时 null → 折线断开
      itemStyle: { color: '#91cc75' },
    },
  ],
}))

async function load(agentId) {
  loading.value = true
  try {
    rows.value = await getCost(agentId)
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
    await Promise.all([load(data[0].id), loadPrices()])
  }
}

async function loadPrices() {
  prices.value = await listModelPrices()
}

function onAgentChange() {
  if (activeAgentId.value) {
    load(activeAgentId.value)
    loadPrices()
  } else {
    rows.value = []
  }
}

function refresh() {
  if (activeAgentId.value) {
    load(activeAgentId.value)
    loadPrices()
  }
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
