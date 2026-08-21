<template>
  <div class="page">
    <el-card shadow="never" class="block">
      <div class="toolbar">
        <span class="label">覆盖率</span>
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

    <el-row v-if="activeAgentId" :gutter="12">
      <el-col :xs="24" :md="12">
        <el-card shadow="never" class="block">
          <template #header>
            <span>接口覆盖</span>
            <el-tag size="small" type="info" style="margin-left: 8px">enabled 接口 vs 用例标注</el-tag>
          </template>
          <div v-if="coverage" class="coverage-body">
            <div class="coverage-rate">
              <el-progress type="dashboard" :percentage="pct(coverage.interface_rate)" :width="150">
                <template #default>
                  <div class="pct-num">{{ pct(coverage.interface_rate) }}%</div>
                  <div class="pct-sub">{{ coverage.interface_covered }}/{{ coverage.interface_total }}</div>
                </template>
              </el-progress>
            </div>
            <div class="coverage-desc">
              <el-alert
                v-if="coverage.interface_blank.length"
                type="warning"
                :closable="false"
                show-icon
                title="以下接口未被任何用例覆盖（盲区）"
              />
              <el-alert
                v-else
                type="success"
                :closable="false"
                show-icon
                title="全部启用接口均已覆盖"
              />
            </div>
            <el-table v-if="coverage.interface_blank.length" :data="coverage.interface_blank" size="small" stripe style="margin-top: 12px">
              <el-table-column prop="id" label="ID" width="60" />
              <el-table-column prop="name" label="接口名" min-width="140" />
              <el-table-column prop="method" label="方法" width="80" />
              <el-table-column prop="path" label="路径" min-width="160" show-overflow-tooltip />
            </el-table>
          </div>
          <el-empty v-else description="暂无数据" />
        </el-card>
      </el-col>

      <el-col :xs="24" :md="12">
        <el-card shadow="never" class="block">
          <template #header>
            <span>场景覆盖</span>
            <el-tag size="small" type="info" style="margin-left: 8px">场景清单 vs 用例打标</el-tag>
          </template>
          <div v-if="coverage" class="coverage-body">
            <div class="coverage-rate">
              <el-progress type="dashboard" :percentage="pct(coverage.scene_rate)" :width="150">
                <template #default>
                  <div class="pct-num">{{ pct(coverage.scene_rate) }}%</div>
                  <div class="pct-sub">{{ coverage.scene_covered }}/{{ coverage.scene_total }}</div>
                </template>
              </el-progress>
            </div>
            <div class="coverage-desc">
              <el-alert
                v-if="coverage.scene_blank.length"
                type="warning"
                :closable="false"
                show-icon
                title="以下场景未被任何用例覆盖（盲区）"
              />
              <el-alert
                v-else
                type="success"
                :closable="false"
                show-icon
                title="全部场景均已覆盖"
              />
            </div>
            <el-table v-if="coverage.scene_blank.length" :data="coverage.scene_blank" size="small" stripe style="margin-top: 12px">
              <el-table-column prop="tag" label="场景" min-width="160" />
              <el-table-column prop="description" label="描述" min-width="160" show-overflow-tooltip />
            </el-table>
          </div>
          <el-empty v-else description="暂无数据" />
        </el-card>
      </el-col>
    </el-row>

    <!-- 6.3 基线对比：最近 run 各接口维度得分 vs 达标分 -->
    <el-card v-if="activeAgentId" shadow="never" class="block">
      <template #header>
        <TermTip term="baseline" />
        <el-tag v-if="baseline.run" size="small" type="info" style="margin-left: 8px">
          run #{{ baseline.run.run_id }} · v{{ baseline.run.version }}
        </el-tag>
        <el-tag v-if="baseline.is_gold_count" size="small" type="warning" style="margin-left: 8px">
          <TermTip term="gold" /> {{ baseline.is_gold_count }} 例
        </el-tag>
      </template>
      <template v-if="baseline.run">
        <div class="baseline-summary">
          总分 <b>{{ baseline.run.agent_score ?? '—' }}</b>
          · 通过 {{ baseline.run.pass_case }}/{{ baseline.run.total_case }}
          <span v-if="baseline.run.finished_at">
            · 完成 {{ fmtTime(baseline.run.finished_at) }}
          </span>
          <el-tag size="small" type="info" style="margin-left: 8px">
            {{ RUN_STATUS[baseline.run.status] || baseline.run.status }}
          </el-tag>
        </div>
        <el-table :data="baselineRows" size="small" stripe style="margin-top: 12px">
          <el-table-column prop="iface" label="接口" min-width="170" show-overflow-tooltip />
          <el-table-column label="维度" width="100">
            <template #default="{ row }">{{ DIM_LABEL[row.code] || row.code }}</template>
          </el-table-column>
          <el-table-column label="run 得分" width="200">
            <template #default="{ row }">
              <el-progress
                v-if="row.score != null"
                :percentage="row.score"
                :stroke-width="12"
                :color="row.met ? '#67c23a' : '#f56c6c'"
              />
              <span v-else class="dim-none">—</span>
            </template>
          </el-table-column>
          <el-table-column width="90">
            <template #header><TermTip term="target" /></template>
            <template #default="{ row }">{{ row.target ?? '未配置' }}</template>
          </el-table-column>
          <el-table-column label="差距" width="100">
            <template #default="{ row }">
              <span v-if="row.gap != null" :class="row.gap >= 0 ? 'gap-plus' : 'gap-minus'">
                {{ row.gap >= 0 ? '+' : '' }}{{ row.gap }}
              </span>
              <span v-else class="dim-none">—</span>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="90">
            <template #default="{ row }">
              <el-tag v-if="row.met != null" :type="row.met ? 'success' : 'danger'" size="small">
                {{ row.met ? '达标' : '未达标' }}
              </el-tag>
              <el-tag v-else type="info" size="small">未配置</el-tag>
            </template>
          </el-table-column>
        </el-table>
      </template>
      <el-empty v-else description="最近无已评分 run，暂无比对" />
    </el-card>

    <el-empty v-if="!activeAgentId" description="请选择 Agent" />
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { listAgents } from '../api/agents'
import TermTip from '../components/TermTip.vue'
import { getBaseline, getCoverage } from '../api/dashboard'

const agentOptions = ref([])
const activeAgentId = ref(null)
const loading = ref(false)
const coverage = ref(null)
// 6.3 基线对比：最近 run {run, interfaces[], is_gold_count}
const baseline = ref({ run: null, interfaces: [], is_gold_count: 0 })

const pct = (r) => (r == null ? 0 : Math.round(r * 100))

// ---- 6.3 维度/状态展示常量 ----
const DIM_LABEL = {
  completeness: '完成度',
  factuality: '事实性',
  reasoning_quality: '思考链',
  tool_usage: '工具使用',
}
const RUN_STATUS = {
  completed: '已完成',
  partial_failed: '部分失败',
  scoring_failed: '评分超时',
  timeout: '超时',
  cancelled: '已取消',
}
const fmtTime = (t) => (t ? new Date(t).toLocaleString('zh-CN') : '-')
// 接口 × 维度扁平化（含接口名与用例数），供表格直接渲染
const baselineRows = computed(() => {
  const rows = []
  for (const it of baseline.value.interfaces || []) {
    for (const d of it.dims || []) {
      rows.push({
        iface: `${it.name}（${it.case_count} 用例）`,
        code: d.code, score: d.score, target: d.target, gap: d.gap, met: d.met,
      })
    }
  }
  return rows
})

async function load(agentId) {
  loading.value = true
  try {
    const [cov, base] = await Promise.all([getCoverage(agentId), getBaseline(agentId)])
    coverage.value = cov
    baseline.value = base
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
  else coverage.value = null
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
.coverage-body {
  display: flex;
  flex-direction: column;
}
.coverage-rate {
  display: flex;
  justify-content: center;
  margin-bottom: 8px;
}
.pct-num {
  font-size: 22px;
  font-weight: 700;
  color: var(--el-color-primary);
}
.pct-sub {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.coverage-desc {
  margin-bottom: 4px;
}
/* 6.3 基线对比 */
.baseline-summary {
  font-size: 13px;
  color: var(--el-text-color-regular);
}
.dim-none {
  color: var(--el-text-color-placeholder);
}
.gap-plus {
  color: var(--el-color-success);
  font-weight: 600;
}
.gap-minus {
  color: var(--el-color-danger);
  font-weight: 600;
}
</style>
