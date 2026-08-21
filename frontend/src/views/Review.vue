<template>
  <div class="page">
    <el-card shadow="never" class="block">
      <div class="toolbar">
        <span class="label">待人工复核</span>
        <el-tag type="warning" effect="light">低置信 judge 判定需复核后 run 才收敛</el-tag>
        <div style="flex: 1" />
        <el-button :loading="loading" @click="load">刷新</el-button>
      </div>
    </el-card>

    <el-card shadow="never" class="block">
      <el-alert
        v-if="!loading && !rows.length"
        type="info"
        :closable="false"
        show-icon
        title="当前无待复核任务"
      />
      <el-table :data="rows" v-loading="loading" size="small" stripe>
        <el-table-column prop="agent_name" label="Agent" width="110" />
        <el-table-column prop="case_name" label="用例" min-width="160" show-overflow-tooltip />
        <el-table-column label="维度" width="90">
          <template #default="{ row }">{{ DIM_LABEL[row.dimension_code] || row.dimension_code }}</template>
        </el-table-column>
        <el-table-column label="置信度" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="confidenceType(row.confidence)">
              {{ row.confidence != null ? (row.confidence * 100).toFixed(0) + '%' : '—' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="Judge 判定" width="150">
          <template #default="{ row }">
            <span v-if="row.result">{{ fmtScore(row.result.score) }}（{{ levelLabel(row.result.level) }}）</span>
            <span v-else>—</span>
          </template>
        </el-table-column>
        <el-table-column label="Judge 理由" min-width="220" show-overflow-tooltip>
          <template #default="{ row }">{{ (row.result || {}).reason }}</template>
        </el-table-column>
        <el-table-column label="更新时间" width="160">
          <template #default="{ row }">{{ fmtTime(row.updated_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button
              link type="success" size="small" :loading="busyKey === rowKey(row)"
              @click="doApprove(row)"
            >采纳</el-button>
            <el-button
              link type="warning" size="small" :loading="busyKey === rowKey(row)"
              @click="openReject(row)"
            >驳回</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 驳回 dialog：带分=改判；不带分=放弃该维度（评分 N/A） -->
    <el-dialog v-model="rejectVisible" title="驳回（人工改判）" width="520px">
      <el-form label-width="90px">
        <el-form-item label="判定分">
          <el-input-number v-model="rejectForm.score" :min="0" :max="100" :step="1" style="width: 100%" />
          <div class="dim-note">留空 = 放弃该维度（评分为 N/A）</div>
        </el-form-item>
        <el-form-item label="理由" required>
          <el-input
            v-model="rejectForm.reason" type="textarea" :rows="3"
            maxlength="2000" show-word-limit placeholder="改判必须填写理由"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="rejectVisible = false">取消</el-button>
        <el-button type="warning" :loading="rejecting" @click="doReject">确认驳回</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { listPendingReviews, reviewTask } from '../api/reviews'

const loading = ref(false)
const rows = ref([])
const busyKey = ref('')
const rejectVisible = ref(false)
const rejecting = ref(false)
const rejectForm = reactive({ run_id: null, case_id: null, dimension_code: '', score: null, reason: '' })

// 与 Dashboard/Agents 同口径维度中文名
const DIM_LABEL = {
  completeness: '完成度',
  factuality: '事实性',
  reasoning_quality: '思考链',
  tool_usage: '工具使用',
  ttft: '首字延迟',
  e2e: '端到端延迟',
  token_cost: 'Token 成本',
}
const LEVEL_LABEL = { pass: '达标', partial: '部分', fail: '不达标' }
const levelLabel = (l) => LEVEL_LABEL[l] || l || '—'
const rowKey = (row) => `${row.run_id}-${row.case_id}-${row.dimension_code}`
const fmtScore = (s) => (s == null ? '—' : Number(s).toFixed(1))
const fmtTime = (t) => (t ? t.replace('T', ' ').slice(0, 19) : '—')
const confidenceType = (c) => (c == null ? 'info' : c >= 0.6 ? 'danger' : 'warning')

const load = async () => {
  loading.value = true
  try {
    rows.value = await listPendingReviews()
  } catch (e) {
    // 拦截器已弹
  } finally {
    loading.value = false
  }
}

const doApprove = async (row) => {
  busyKey.value = rowKey(row)
  try {
    await reviewTask({
      run_id: row.run_id, case_id: row.case_id,
      dimension_code: row.dimension_code, action: 'approve',
    })
    ElMessage.success('已采纳原判定，run 继续评分')
    await load()
  } catch (e) {
    // 拦截器已弹
  } finally {
    busyKey.value = ''
  }
}

const openReject = (row) => {
  rejectForm.run_id = row.run_id
  rejectForm.case_id = row.case_id
  rejectForm.dimension_code = row.dimension_code
  rejectForm.score = (row.result || {}).score ?? null
  rejectForm.reason = ''
  rejectVisible.value = true
}

const doReject = async () => {
  if (!rejectForm.reason.trim()) {
    ElMessage.warning('驳回必须填写理由')
    return
  }
  rejecting.value = true
  try {
    await reviewTask({
      run_id: rejectForm.run_id,
      case_id: rejectForm.case_id,
      dimension_code: rejectForm.dimension_code,
      action: 'reject',
      score: rejectForm.score ?? undefined,
      reason: rejectForm.reason.trim(),
    })
    ElMessage.success(rejectForm.score != null ? '已改判' : '已放弃该维度（N/A）')
    rejectVisible.value = false
    await load()
  } catch (e) {
    // 拦截器已弹
  } finally {
    rejecting.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
}
.label {
  font-weight: 600;
}
.dim-note {
  font-size: 12px;
  color: #909399;
  line-height: 1.6;
}
</style>
