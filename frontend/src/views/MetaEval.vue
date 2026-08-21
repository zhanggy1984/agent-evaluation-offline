<template>
  <div class="meta-eval">
    <!-- ① judge 漂移检测 -->
    <el-card shadow="never" class="block">
      <template #header>
        <TermTip term="drift" label="judge 漂移检测" />
        <el-tag size="small" type="warning" style="margin-left: 8px">重判<TermTip term="gold" label="金标准" />用例</el-tag>
      </template>
      <div class="toolbar">
        <el-button
          v-if="isStaff"
          type="primary"
          :loading="driftLoading"
          :disabled="!canDrift"
          @click="runDriftCheck"
        >
          触发漂移检测
        </el-button>
        <el-select
          v-if="isStaff"
          v-model="agentFilter"
          placeholder="全部 agent（触发范围）"
          clearable
          style="width: 200px"
        >
          <el-option v-for="a in agents" :key="a.id" :label="a.name" :value="a.id" />
        </el-select>
        <el-alert
          v-else
          type="info"
          :closable="false"
          title="评测员 / 管理员可触发检测，观察者只读"
          style="width: 100%"
        />
        <el-button :loading="loading" @click="loadDrift">刷新</el-button>
      </div>

      <el-alert
        v-if="lastDrift"
        type="success"
        :closable="false"
        show-icon
        style="margin-top: 12px"
        :title="`检测完成：${lastDrift.history.length} 个维度，一致率阈值 ${lastDrift.threshold}`"
        :description="lastDrift.history.filter((h) => h.drift_flag).length + ' 个维度低于阈值（已在问题列表生成告警）'"
      />

      <div class="toolbar" style="margin-top: 12px">
        <el-select
          v-model="dimFilter"
          placeholder="按维度过滤"
          clearable
          style="width: 200px"
          @change="loadDrift"
        >
          <el-option v-for="d in dimOptions" :key="d" :label="DIM_LABEL[d] || d" :value="d" />
        </el-select>
      </div>

      <el-table v-if="driftList.length" :data="driftList" size="small" stripe style="margin-top: 8px">
        <el-table-column prop="id" label="ID" width="64" />
        <el-table-column label="维度" width="120">
          <template #default="{ row }">{{ DIM_LABEL[row.dimension_code] || row.dimension_code }}</template>
        </el-table-column>
        <el-table-column label="一致率" width="130">
          <template #default="{ row }">
            <el-tag :type="row.drift_flag ? 'danger' : 'success'" size="small">
              {{ (row.consistency_rate * 100).toFixed(1) }}%
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="judged_case_cnt" label="判例数" width="90" />
        <el-table-column width="80">
          <template #header><TermTip term="drift" /></template>
          <template #default="{ row }">
            <el-tag :type="row.drift_flag ? 'danger' : 'success'" size="small">
              {{ row.drift_flag ? '是' : '否' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="检测时间" min-width="170">
          <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
        </el-table-column>
      </el-table>
      <el-empty v-else description="暂无漂移历史，触发一次检测后生成" style="margin-top: 12px" />
    </el-card>

    <!-- ② 数据清理 -->
    <el-card shadow="never" class="block">
      <template #header>
        <span>数据清理</span>
        <el-tag size="small" type="info" style="margin-left: 8px">按 agent+套件保留 N 次</el-tag>
      </template>
      <div class="toolbar">
        <el-button v-if="isStaff" type="danger" plain :loading="cleanupLoading" @click="runCleanup">
          触发数据清理
        </el-button>
        <el-alert
          v-else
          type="info"
          :closable="false"
          title="评测员 / 管理员可触发清理，观察者只读"
          style="width: 100%"
        />
      </div>
      <el-descriptions v-if="cleanupResult" :column="4" border size="small" style="margin-top: 12px">
        <el-descriptions-item label="删除 run">{{ cleanupResult.purged }}</el-descriptions-item>
        <el-descriptions-item label="保留次数">{{ cleanupResult.retain }}</el-descriptions-item>
        <el-descriptions-item label="跳过（issue 关联）">
          {{ cleanupResult.skipped_issue }}
        </el-descriptions-item>
        <el-descriptions-item label="跳过（pinned）">
          {{ cleanupResult.skipped_pinned }}
        </el-descriptions-item>
      </el-descriptions>
      <el-empty v-else description="尚未触发清理（保留次数默认 50，pinned / issue 关联 run 永不清理）" style="margin-top: 12px" />
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useAuthStore } from '../stores/auth'
import TermTip from '../components/TermTip.vue'
import { listAgents } from '../api/agents'
import { getDrift, triggerCleanup, triggerDrift } from '../api/meta'

const auth = useAuthStore()
const isStaff = computed(() => ['admin', 'evaluator'].includes(auth.role))

const DIM_LABEL = {
  completeness: '完成度',
  factuality: '事实性',
  reasoning_quality: '思考链',
  tool_usage: '工具使用',
  ttft: '首字延迟',
  e2e: '端到端延迟',
  token_cost: 'Token 成本',
}

const fmtTime = (t) => (t ? new Date(t).toLocaleString('zh-CN') : '-')

// ---- 漂移检测 ----
const driftList = ref([])
const dimFilter = ref('')
const lastDrift = ref(null)
const loading = ref(false)
const driftLoading = ref(false)
// 触发粒度：agentFilter（下拉选 agent）＋ dimFilter（历史维度过滤，兼作触发粒度）→ 省 judge token
const agents = ref([])
const agentFilter = ref('')
// drift/check 同步烧 judge token，长时间运行：允许触发但限制并发重复点击
const canDrift = computed(() => !driftLoading.value)
const dimOptions = computed(() => [...new Set(driftList.value.map((h) => h.dimension_code))])

async function loadAgents() {
  agents.value = (await listAgents()).filter((a) => a.enabled !== false)
}

async function loadDrift() {
  loading.value = true
  try {
    driftList.value = await getDrift(dimFilter.value || undefined)
  } finally {
    loading.value = false
  }
}

async function runDriftCheck() {
  try {
    await ElMessageBox.confirm(
      '漂移检测将重新调用 judge LLM 对金标准用例判分（消耗 token/费用）。确认继续？',
      '触发漂移检测',
      { type: 'warning', confirmButtonText: '确认触发', cancelButtonText: '再想想' }
    )
  } catch {
    return
  }
  driftLoading.value = true
  try {
    lastDrift.value = await triggerDrift({
      dimension_code: dimFilter.value || undefined,
      agent_id: agentFilter.value || undefined,
    })
    await loadDrift()
    const flags = lastDrift.value.history.filter((h) => h.drift_flag)
    ElMessage.success(
      flags.length ? `漂移检测完成，${flags.length} 个维度低于阈值` : '漂移检测完成，全部维度一致'
    )
  } finally {
    driftLoading.value = false
  }
}

// ---- 数据清理 ----
const cleanupResult = ref(null)
const cleanupLoading = ref(false)

async function runCleanup() {
  try {
    await ElMessageBox.confirm(
      '将删除超出保留次数的历史 run（issue 关联 / pinned 的 run 永不清理）。确认继续？',
      '触发数据清理',
      { type: 'warning', confirmButtonText: '确认清理', cancelButtonText: '再想想' }
    )
  } catch {
    return
  }
  cleanupLoading.value = true
  try {
    cleanupResult.value = await triggerCleanup()
    ElMessage.success(`清理完成：删除 ${cleanupResult.value.purged} 个 run`)
  } finally {
    cleanupLoading.value = false
  }
}

onMounted(() => {
  loadDrift()
  loadAgents()
})
</script>
