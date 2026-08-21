<template>
  <div class="page">
    <el-card shadow="never" class="block">
      <div class="toolbar">
        <span class="label">问题列表</span>
        <el-tag v-if="regressionCount" type="danger" effect="dark">
          回归风险 {{ regressionCount }} 项（曾修复又复现）
        </el-tag>
        <el-select
          v-model="filters.agent_id"
          placeholder="Agent"
          clearable
          filterable
          style="width: 180px"
          @change="load"
        >
          <el-option v-for="a in agentOptions" :key="a.id" :label="a.name" :value="a.id" />
        </el-select>
        <el-select v-model="filters.status" placeholder="状态" clearable style="width: 140px" @change="load">
          <el-option v-for="(label, v) in STATUS_LABEL" :key="v" :label="label" :value="v" />
        </el-select>
        <el-select v-model="filters.severity" placeholder="严重级别" clearable style="width: 140px" @change="load">
          <el-option v-for="(label, v) in SEVERITY_LABEL" :key="v" :label="label" :value="v" />
        </el-select>
        <el-button :loading="loading" @click="load">刷新</el-button>
        <div style="flex: 1" />
        <el-button v-if="isStaff" type="primary" @click="openCreate">登记问题</el-button>
      </div>
    </el-card>

    <el-card shadow="never" class="block">
      <el-table :data="rows" v-loading="loading" size="small" stripe :row-class-name="rowClassName">
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column prop="agent_name" label="Agent" width="110" />
        <el-table-column prop="title" label="标题" min-width="180" show-overflow-tooltip />
        <el-table-column label="严重级别" width="90">
          <template #default="{ row }">
            <el-tag :type="SEVERITY_COLOR[row.severity] || 'info'" size="small">
              {{ SEVERITY_LABEL[row.severity] || row.severity }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="STATUS_COLOR[row.status] || 'info'" size="small">
              {{ STATUS_LABEL[row.status] || row.status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="复现状态" width="100">
          <template #default="{ row }">
            <el-tag :type="VERIFY_COLOR[row.last_verify_result] || 'info'" size="small">
              {{ VERIFY_LABEL[row.last_verify_result] || '—' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="related_dimension" label="关联维度" width="120" />
        <el-table-column label="更新时间" width="160">
          <template #default="{ row }">{{ fmtTime(row.updated_at) }}</template>
        </el-table-column>
        <el-table-column v-if="isStaff" label="操作" width="160" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
            <el-dropdown v-if="nextStatus(row.status).length" @command="(to) => doTransition(row, to)">
              <el-button link type="warning" size="small">流转▾</el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item v-for="n in nextStatus(row.status)" :key="n.to" :command="n.to">
                    {{ n.label }}
                  </el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 登记 / 编辑 -->
    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑问题' : '登记问题'" width="520px">
      <el-form label-width="100px">
        <el-form-item label="Agent" required>
          <el-select v-model="form.agent_id" style="width: 100%" :disabled="!!editingId">
            <el-option v-for="a in agentOptions" :key="a.id" :label="a.name" :value="a.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="标题" required>
          <el-input v-model="form.title" maxlength="256" show-word-limit />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="3" />
        </el-form-item>
        <el-form-item label="严重级别">
          <el-select v-model="form.severity" style="width: 100%">
            <el-option v-for="(label, v) in SEVERITY_LABEL" :key="v" :label="label" :value="v" />
          </el-select>
        </el-form-item>
        <el-form-item label="关联维度">
          <el-input v-model="form.related_dimension" placeholder="dimension code，如 completeness" />
        </el-form-item>
        <el-form-item label="关联用例">
          <el-input v-model.number="form.related_case_id" placeholder="case id（可选）" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useAuthStore } from '../stores/auth'
import { listAgents } from '../api/agents'
import { listIssues, createIssue, updateIssue, transitionIssue } from '../api/issues'

// ---- 常量（与后端 issue_rules 枚举一致）----
const STATUS_LABEL = {
  open: '待处理',
  fixing: '修复中',
  fixed: '已修复',
  verified: '已验证',
  closed: '已关闭',
}
const STATUS_COLOR = {
  open: 'warning',
  fixing: 'primary',
  fixed: 'info',
  verified: 'success',
  closed: 'info',
}
const SEVERITY_LABEL = { low: '低', medium: '中', high: '高', critical: '严重' }
const SEVERITY_COLOR = { low: 'info', medium: 'warning', high: 'danger', critical: 'danger' }
// 复现验证结果（6.2）：reproduced=回归复现 / fixed=复现已修复 / verified=闭环确认
const VERIFY_LABEL = { reproduced: '复现', fixed: '已修复', verified: '已验证' }
const VERIFY_COLOR = { reproduced: 'danger', fixed: 'success', verified: 'primary' }
// 合法流转：open→fixing(开始修复)/closed(登记作废)；fixing→fixed；fixed→verified；verified→closed；closed 终态
const NEXT_STATUS = {
  open: [
    { to: 'fixing', label: '开始修复' },
    { to: 'closed', label: '登记作废' },
  ],
  fixing: [{ to: 'fixed', label: '标记已修复' }],
  fixed: [{ to: 'verified', label: '标记已验证' }],
  verified: [{ to: 'closed', label: '关闭' }],
  closed: [],
}

const auth = useAuthStore()
const isStaff = computed(() => ['admin', 'evaluator'].includes(auth.role))

const agentOptions = ref([])
const loading = ref(false)
const rows = ref([])
const filters = reactive({ agent_id: null, status: '', severity: '' })

const dialogVisible = ref(false)
const editingId = ref(null)
const saving = ref(false)
const form = reactive({
  agent_id: null,
  title: '',
  description: '',
  severity: 'medium',
  related_dimension: '',
  related_case_id: null,
})

const fmtTime = (t) => (t ? new Date(t).toLocaleString('zh-CN') : '-')
const nextStatus = (status) => NEXT_STATUS[status] || []
// 回归风险项：最近一次复现验证判为 reproduced 且未关闭（含自动重开后的 open）
const regressionCount = computed(() =>
  rows.value.filter((r) => r.last_verify_result === 'reproduced' && r.status !== 'closed').length
)
function rowClassName({ row }) {
  return row.last_verify_result === 'reproduced' && row.status !== 'closed' ? 'regression-row' : ''
}

async function load() {
  loading.value = true
  try {
    const params = {}
    if (filters.agent_id) params.agent_id = filters.agent_id
    if (filters.status) params.status = filters.status
    if (filters.severity) params.severity = filters.severity
    rows.value = await listIssues(params)
  } finally {
    loading.value = false
  }
}

async function loadAgents() {
  agentOptions.value = await listAgents()
}

// ---- 登记 / 编辑 ----
function openCreate() {
  editingId.value = null
  Object.assign(form, {
    agent_id: filters.agent_id,
    title: '',
    description: '',
    severity: 'medium',
    related_dimension: '',
    related_case_id: null,
  })
  dialogVisible.value = true
}

function openEdit(row) {
  editingId.value = row.id
  Object.assign(form, {
    agent_id: row.agent_id,
    title: row.title,
    description: row.description || '',
    severity: row.severity,
    related_dimension: row.related_dimension || '',
    related_case_id: row.related_case_id ?? null,
  })
  dialogVisible.value = true
}

async function save() {
  if (!form.agent_id || !form.title.trim()) {
    ElMessage.warning('Agent 与标题必填')
    return
  }
  saving.value = true
  try {
    if (editingId.value) {
      const { agent_id, ...rest } = form // 编辑不允许改 Agent
      await updateIssue(editingId.value, rest)
    } else {
      await createIssue({ ...form })
    }
    ElMessage.success('已保存')
    dialogVisible.value = false
    await load()
  } finally {
    saving.value = false
  }
}

// ---- 状态流转 ----
async function doTransition(row, to) {
  try {
    await ElMessageBox.confirm(
      `确认将 #${row.id}「${row.title}」流转为「${STATUS_LABEL[to]}」？`,
      '状态流转',
      { type: 'warning' }
    )
  } catch {
    return
  }
  await transitionIssue(row.id, to)
  ElMessage.success('已流转')
  await load()
}

onMounted(() => {
  loadAgents()
  load()
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
.label {
  font-size: 15px;
  font-weight: 600;
}
/* 回归风险行（6.2）：最近复现验证判 reproduced → 浅红底高亮 */
.regression-row {
  background: #fef0f0 !important;
}
</style>
