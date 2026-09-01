<template>
  <div>
    <el-page-header class="page-header" :content="activeTab === 'manage' ? '用例集 / 用例管理' : '待双人标注的用例'">
      <template #title>用例管理</template>
    </el-page-header>

    <el-tabs v-model="activeTab">
      <!-- ============ 用例管理 ============ -->
      <el-tab-pane label="用例管理" name="manage">
        <div class="manage-layout">
          <div class="suite-panel">
            <div class="toolbar">
              <el-select v-model="curAgentId" placeholder="选择 agent" size="small" style="width: 100%" @change="onAgentChange">
                <el-option v-for="a in agents" :key="a.id" :value="a.id" :label="a.name" />
              </el-select>
              <el-button v-if="isStaff && curAgentId" size="small" type="primary" style="width: 100%" @click="openSuiteDialog">
                新建用例集
              </el-button>
            </div>
            <div class="suite-list">
              <div v-for="s in suites" :key="s.id" class="suite-item" :class="{ active: curSuiteId === s.id }"
                @click="selectSuite(s.id)">
                <div class="suite-name">{{ s.name }}</div>
                <div class="suite-meta">{{ s.case_count }} 用例</div>
                <div class="suite-ops">
                  <el-button v-if="isAdmin" link type="danger" size="small" @click.stop="handleDeleteSuite(s)">删</el-button>
                  <el-button v-if="isStaff" link type="primary" size="small" @click.stop="openSuiteDialog(s)">编</el-button>
                </div>
              </div>
              <div v-if="!suites.length" class="suite-empty">暂无用例集，请选择 agent 或新建</div>
            </div>
          </div>

          <div class="case-panel">
            <div class="toolbar">
              <el-button v-if="isStaff && curSuiteId" type="primary" size="small" @click="openCaseDialog()">新建用例</el-button>
              <el-button size="small" :loading="loadingCases" @click="loadCases">刷新</el-button>
            </div>
            <el-table :data="cases" border size="small" v-loading="loadingCases">
              <el-table-column prop="id" label="ID" width="60" />
              <el-table-column prop="name" label="名称" min-width="140" show-overflow-tooltip />
              <el-table-column prop="interface_name" label="接口" width="90" />
              <el-table-column prop="input_type" label="输入" width="80" />
              <el-table-column label="状态" width="90">
                <template #default="{ row }">
                  <el-tag size="small" :type="statusTag(row.status)">{{ statusLabel(row.status) }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="标注" width="100">
                <template #default="{ row }">
                  <el-tag size="small" :type="annTag(row.annotation_status)">{{ annLabel(row.annotation_status) }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="标记" width="90">
                <template #default="{ row }">
                  <span v-if="row.is_gold"><el-tag size="small" type="warning">gold</el-tag></span>
                  <span v-if="row.is_held_out"><el-tag size="small" type="danger" style="margin-left:2px">留出</el-tag></span>
                </template>
              </el-table-column>
              <el-table-column label="场景" min-width="140">
                <template #default="{ row }">
                  <el-tag v-for="t in row.scene_tags" :key="t" size="small" type="info" class="tag-gap">{{ t }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="操作" width="250" fixed="right">
                <template #default="{ row }">
                  <el-button v-if="isStaff" link type="primary" @click="openCaseDialog(row)">编辑</el-button>
                  <el-button v-if="isStaff" link type="primary" @click="openTagDialog(row)">打标</el-button>
                  <el-button v-if="isStaff" link type="primary" @click="openAnnotationDialog(row)">标注</el-button>
                  <el-button v-if="isStaff && row.status !== 'invalidated'" link type="danger" @click="handleInvalidate(row)">作废</el-button>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </div>
      </el-tab-pane>

      <!-- ============ 标注待办 ============ -->
      <el-tab-pane label="标注待办" name="todo">
        <div class="toolbar">
          <el-select v-model="todoAgentId" placeholder="全部 agent" clearable size="small" style="width: 260px" @change="loadTodo">
            <el-option v-for="a in agents" :key="a.id" :value="a.id" :label="a.name" />
          </el-select>
          <el-button size="small" :loading="loadingTodo" @click="loadTodo">刷新</el-button>
        </div>
        <el-table :data="todoList" border size="small" v-loading="loadingTodo">
          <el-table-column prop="case_id" label="ID" width="60" />
          <el-table-column prop="case_name" label="用例" min-width="160" show-overflow-tooltip />
          <el-table-column prop="suite_id" label="用例集" width="90" />
          <el-table-column label="Agent" width="120">
            <template #default="{ row }">{{ agentName(row.agent_id) }}</template>
          </el-table-column>
          <el-table-column label="标注状态" width="100">
            <template #default="{ row }">
              <el-tag size="small" :type="annTag(row.annotation_status)">{{ annLabel(row.annotation_status) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="已标维度" min-width="160">
            <template #default="{ row }">
              <el-tag v-for="d in row.dimension_codes" :key="d" size="small" class="tag-gap">{{ d }}</el-tag>
              <span v-if="!row.dimension_codes?.length" class="dim-note">未标注</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="100">
            <template #default="{ row }">
              <el-button v-if="isStaff" link type="primary" @click="annotateFromTodo(row)">去标注</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>
    </el-tabs>

    <!-- suite 新建/编辑 dialog -->
    <el-dialog v-model="suiteVisible" :title="suiteEditingId ? '编辑用例集' : '新建用例集'" width="440px">
      <el-form :model="suiteForm" label-width="80px" size="small">
        <el-form-item label="名称" required><el-input v-model="suiteForm.name" /></el-form-item>
        <el-form-item label="描述"><el-input v-model="suiteForm.description" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" @click="suiteVisible = false">取消</el-button>
        <el-button size="small" type="primary" :loading="savingSuite" @click="saveSuite">保存</el-button>
      </template>
    </el-dialog>

    <!-- case 新建/编辑 dialog -->
    <el-dialog v-model="caseVisible" :title="caseEditingId ? '编辑用例' : '新建用例'" width="680px" top="4vh">
      <el-form :model="caseForm" label-width="90px" size="small">
        <el-form-item label="名称" required><el-input v-model="caseForm.name" /></el-form-item>
        <el-form-item label="接口" required>
          <el-select v-model="caseForm.interface_id" style="width: 100%">
            <el-option v-for="i in agentInterfaces" :key="i.id" :value="i.id"
              :label="`${i.name}（${i.path}）`" />
          </el-select>
        </el-form-item>
        <el-form-item label="输入类型">
          <el-radio-group v-model="caseForm.input_type">
            <el-radio value="text">文本</el-radio>
            <el-radio value="file">文件</el-radio>
            <el-radio value="conversation">多轮</el-radio>
          </el-radio-group>
          <el-button link type="primary" size="small" style="margin-left: 12px" @click="fillExample">
            填入示例
          </el-button>
          <span class="dim-note" style="margin-left: 4px">按输入类型填充可编辑模板，标注【改】的键请改写</span>
        </el-form-item>
        <el-form-item label="input" :error="caseErrors.input">
          <el-input v-model="caseForm.inputText" type="textarea" :rows="3"
            placeholder='统一格式 {"content": "...", "params": {}, "file_ref": "...", "seed": 1}' />
        </el-form-item>
        <el-form-item v-if="caseForm.input_type === 'file'" label="附件">
          <el-upload :show-file-list="false" :auto-upload="false" accept=".pdf,.txt,.doc,.docx,.xls,.xlsx,.csv,.json" @change="handleFileChange">
            <el-button size="small">选择文件</el-button>
          </el-upload>
          <span v-if="caseForm.file_ref" class="dim-note" style="margin-left:8px">{{ caseForm.file_ref }}</span>
        </el-form-item>
        <el-form-item label="expected" :error="caseErrors.expected">
          <el-input v-model="caseForm.expectedText" type="textarea" :rows="2" placeholder='{"golden_answer": "..."}' />
        </el-form-item>
        <el-form-item label="assertions" :error="caseErrors.assertions">
          <el-input v-model="caseForm.assertionsText" type="textarea" :rows="2" placeholder='[{"op": "field_nonempty", "args": {"path": "answer"}, "dimension": "completeness"}]' />
        </el-form-item>
        <el-form-item label="metrics" :error="caseErrors.metrics">
          <el-input v-model="caseForm.metricsText" type="textarea" :rows="2" placeholder='{"factuality": {"enabled": true}}' />
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="caseForm.status" style="width: 160px">
            <el-option value="draft" label="草稿" />
            <el-option value="active" label="启用" />
            <el-option value="invalidated" label="已作废" />
          </el-select>
        </el-form-item>
        <el-form-item label="标记">
          <el-checkbox v-model="caseForm.is_gold" :disabled="!isStaff">gold 用例</el-checkbox>
          <el-checkbox v-model="caseForm.is_held_out" :disabled="!isStaff" style="margin-left: 16px">留出集</el-checkbox>
          <span class="dim-note" style="margin-left: 8px">留出集仅 admin/独立 QA 可见可标</span>
        </el-form-item>
        <el-form-item v-if="scenes.length" label="场景">
          <el-select v-model="caseForm.scene_tags" multiple filterable style="width: 100%">
            <el-option v-for="s in scenes" :key="s.scene_tag" :value="s.scene_tag" :label="s.scene_tag" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" @click="caseVisible = false">取消</el-button>
        <el-button size="small" type="primary" :loading="savingCase" @click="saveCase">保存</el-button>
      </template>
    </el-dialog>

    <!-- 打标 dialog -->
    <el-dialog v-model="tagVisible" :title="`打标：${tagCase?.name}`" width="440px">
      <el-select v-model="tagSelection" multiple filterable style="width: 100%">
        <el-option v-for="s in scenes" :key="s.scene_tag" :value="s.scene_tag" :label="s.scene_tag" />
      </el-select>
      <div class="dim-note" style="margin-top: 8px">当前标签：{{ tagCase?.scene_tags?.join(', ') || '无' }}</div>
      <template #footer>
        <el-button size="small" @click="tagVisible = false">取消</el-button>
        <el-button size="small" type="primary" :loading="savingTags" @click="saveTags">保存标签</el-button>
      </template>
    </el-dialog>

    <!-- 标注 dialog -->
    <el-dialog v-model="annVisible" :title="`标注：${annCase?.name}`" width="640px">
      <div class="ann-head">
        <el-tag size="small" :type="annTag(annCase?.annotation_status)">
          {{ annLabel(annCase?.annotation_status) }}
        </el-tag>
        <span class="dim-note">双人标注流转：draft → single → double → consensus/disputed</span>
      </div>
      <el-table :data="annList" border size="small">
        <el-table-column prop="annotator_name" label="标注人" width="110" />
        <el-table-column prop="dimension_code" label="维度" min-width="130" />
        <el-table-column label="分值" width="90">
          <template #default="{ row }">{{ row.level ?? '—' }}</template>
        </el-table-column>
        <el-table-column prop="note" label="备注" min-width="140" show-overflow-tooltip />
        <el-table-column prop="created_at" label="时间" min-width="150" />
      </el-table>
      <el-form :model="annForm" label-width="80px" size="small" style="margin-top: 10px">
        <el-form-item label="维度">
          <el-select v-model="annForm.dimension_code" style="width: 200px">
            <el-option v-for="d in DIMS" :key="d.code" :value="d.code" :label="d.name" />
          </el-select>
        </el-form-item>
        <el-form-item label="分值">
          <el-input-number v-model="annForm.level" :min="0" :max="10" :step="0.5" />
          <span class="dim-note" style="margin-left: 8px">留空仅备注</span>
        </el-form-item>
        <el-form-item label="备注"><el-input v-model="annForm.note" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" @click="annVisible = false">关闭</el-button>
        <el-button size="small" type="primary" :loading="savingAnn" @click="saveAnnotation">提交标注</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listAgents, listInterfaces, listScenes, addCaseScenes } from '../api/agents'
import { listSuites, createSuite, updateSuite, deleteSuite } from '../api/suites'
import { listCases, getCase, createCase, updateCase, invalidateCase } from '../api/cases'
import { uploadFile } from '../api/uploads'
import { listTodo, listCaseAnnotations, addAnnotation } from '../api/annotations'
import { useAuthStore } from '../stores/auth'
import { parseCaseField, buildCaseExample } from '../utils/caseParse'

const auth = useAuthStore()
const { isAdmin } = storeToRefs(auth)
const isStaff = computed(() => ['admin', 'evaluator'].includes(auth.role))

const activeTab = ref('manage')

// 固定 7 评测维度（与 core/constants 一致）
const DIMS = [
  { code: 'completeness', name: '完成度' }, { code: 'factuality', name: '事实性' },
  { code: 'reasoning_quality', name: '思考链' }, { code: 'tool_usage', name: '工具使用' },
  { code: 'ttft', name: '首字延迟' }, { code: 'e2e', name: '端到端延迟' },
  { code: 'token_cost', name: 'Token 成本' },
]

const STATUS_LABEL = { draft: '草稿', active: '启用', invalidated: '已作废' }
const statusLabel = (s) => STATUS_LABEL[s] || s
const statusTag = (s) => (s === 'active' ? 'success' : s === 'invalidated' ? 'info' : 'warning')
const ANN_LABEL = { draft: '未标注', single: '单人', double: '双人', consensus: '一致', disputed: '冲突' }
const annLabel = (s) => ANN_LABEL[s] || s
const annTag = (s) => (s === 'consensus' ? 'success' : s === 'disputed' ? 'danger' : s === 'double' ? 'primary' : s === 'single' ? 'warning' : 'info')

const agents = ref([])
const agentName = (id) => agents.value.find((a) => a.id === id)?.name || `#${id}`

// ---- suite 树 ----
const curAgentId = ref(null)
const curSuiteId = ref(null)
const suites = ref([])
const loadingSuites = ref(false)

const onAgentChange = async () => {
  curSuiteId.value = null
  cases.value = []
  await loadSuites()
}

const loadSuites = async () => {
  if (!curAgentId.value) return
  loadingSuites.value = true
  try {
    suites.value = await listSuites(curAgentId.value)
    if (suites.value.length) {
      // P2-D16：优先选中已指定的 suite（标注待办【去标注】定位），否则默认第一个
      const target = curSuiteId.value && suites.value.some((s) => s.id === curSuiteId.value)
        ? curSuiteId.value
        : suites.value[0].id
      selectSuite(target)
    }
  } catch (e) {
    // 拦截器已弹
  } finally {
    loadingSuites.value = false
  }
}

const selectSuite = async (sid) => {
  curSuiteId.value = sid
  await loadCases()
  await loadScenes()
}

// ---- suite CRUD ----
const suiteVisible = ref(false)
const suiteEditingId = ref(null)
const savingSuite = ref(false)
const suiteForm = reactive({ name: '', description: '' })

const openSuiteDialog = (s) => {
  suiteEditingId.value = s?.id || null
  suiteForm.name = s?.name || ''
  suiteForm.description = s?.description || ''
  suiteVisible.value = true
}

const saveSuite = async () => {
  if (!suiteForm.name) {
    ElMessage.warning('请输入名称')
    return
  }
  savingSuite.value = true
  try {
    if (suiteEditingId.value) {
      await updateSuite(suiteEditingId.value, { ...suiteForm })
    } else {
      await createSuite({ agent_id: curAgentId.value, ...suiteForm })
    }
    ElMessage.success('已保存')
    suiteVisible.value = false
    await loadSuites()
  } catch (e) {
    // 拦截器已弹
  } finally {
    savingSuite.value = false
  }
}

const handleDeleteSuite = async (s) => {
  try {
    await ElMessageBox.confirm(`确认删除用例集「${s.name}」？其下有用例则后端拒绝（409）。`, '提示', { type: 'warning' })
    await deleteSuite(s.id)
    await loadSuites()
  } catch (e) {
    // 取消或 409 提示
  }
}

// ---- case ----
const cases = ref([])
const loadingCases = ref(false)
const agentInterfaces = ref([])
const scenes = ref([])

const loadCases = async () => {
  if (!curSuiteId.value) return
  loadingCases.value = true
  try {
    cases.value = await listCases(curSuiteId.value)
  } catch (e) {
    // 拦截器已弹
  } finally {
    loadingCases.value = false
  }
}

const loadScenes = async () => {
  if (!curAgentId.value) return
  try {
    scenes.value = await listScenes(curAgentId.value)
  } catch (e) {
    // 拦截器已弹
  }
}

const loadAgentInterfaces = async () => {
  if (!curAgentId.value) return
  try {
    agentInterfaces.value = await listInterfaces(curAgentId.value)
  } catch (e) {
    // 拦截器已弹
  }
}

// ---- case 新建/编辑 ----
const caseVisible = ref(false)
const caseEditingId = ref(null)
const savingCase = ref(false)
const caseForm = reactive({
  name: '', interface_id: null, input_type: 'text', inputText: '{}',
  expectedText: '{}', assertionsText: '[]', metricsText: '{}',
  file_ref: '', status: 'draft', is_gold: false, is_held_out: false, scene_tags: [],
})
// 示例模板已抽至 utils/caseParse.js（EXAMPLES + buildCaseExample）
// 字段级 JSON 校验：非法时写入对应错误，form-item 红框标错（解析逻辑在 utils/caseParse.js）
const caseErrors = reactive({ input: '', expected: '', assertions: '', metrics: '' })

const fillExample = () => {
  const ex = buildCaseExample(caseForm.input_type)
  caseForm.inputText = ex.input
  caseForm.expectedText = ex.expected
  caseForm.assertionsText = ex.assertions
  caseForm.metricsText = ex.metrics
  ElMessage.success(`已按「${caseForm.input_type}」填入示例，请改写标【改】的字段`)
}

const resetCaseForm = (c) => {
  caseEditingId.value = c?.id || null
  caseForm.name = c?.name || ''
  caseForm.interface_id = c?.interface_id ?? null
  caseForm.input_type = c?.input_type || 'text'
  caseForm.inputText = JSON.stringify(c?.input ?? {}, null, 2)
  caseForm.expectedText = JSON.stringify(c?.expected ?? {}, null, 2)
  caseForm.assertionsText = JSON.stringify(c?.assertions ?? [], null, 2)
  caseForm.metricsText = JSON.stringify(c?.metrics ?? {}, null, 2)
  caseForm.file_ref = c?.file_ref || ''
  caseForm.status = c?.status || 'draft'
  caseForm.is_gold = c?.is_gold ?? false
  caseForm.is_held_out = c?.is_held_out ?? false
  caseForm.scene_tags = c?.scene_tags ? [...c.scene_tags] : []
}

const openCaseDialog = async (c) => {
  await loadAgentInterfaces()
  if (c) {
    const full = await getCase(c.id) // 拉全量（含 input/expected）
    resetCaseForm(full)
  } else {
    resetCaseForm()
  }
  caseVisible.value = true
}

const handleFileChange = async (file) => {
  try {
    const r = await uploadFile(file.raw)
    caseForm.file_ref = r.file_ref
    ElMessage.success(`已上传：${r.filename}`)
  } catch (e) {
    // 拦截器已弹
  }
}

const saveCase = async () => {
  caseErrors.input = caseErrors.expected = caseErrors.assertions = caseErrors.metrics = ''
  const parsed = {}
  for (const [key, text] of [
    ['input', caseForm.inputText],
    ['expected', caseForm.expectedText],
    ['assertions', caseForm.assertionsText],
    ['metrics', caseForm.metricsText],
  ]) {
    const r = parseCaseField(key, text)
    if (!r.ok) {
      caseErrors[key] = r.error
      ElMessage.error('存在非法 JSON，请修正标红的字段')
      return
    }
    parsed[key] = r.value
  }
  const payload = {
    name: caseForm.name, interface_id: caseForm.interface_id, input_type: caseForm.input_type,
    input: parsed.input, expected: parsed.expected, assertions: parsed.assertions, metrics: parsed.metrics,
    file_ref: caseForm.file_ref || null,
    is_gold: caseForm.is_gold, is_held_out: caseForm.is_held_out,
    scenes: caseForm.scene_tags.length ? caseForm.scene_tags : null,
  }
  savingCase.value = true
  try {
    let id = caseEditingId.value
    if (id) {
      await updateCase(id, { ...payload, status: caseForm.status })
    } else {
      const r = await createCase(curSuiteId.value, payload)
      id = r.id
      // 新建接口不接收 status（默认 draft），按选择激活
      if (caseForm.status && caseForm.status !== 'draft') {
        await updateCase(id, { status: caseForm.status })
      }
    }
    ElMessage.success('已保存')
    caseVisible.value = false
    await loadCases()
  } catch (e) {
    // 拦截器已弹
  } finally {
    savingCase.value = false
  }
}

const handleInvalidate = async (row) => {
  try {
    await ElMessageBox.confirm(`确认作废用例「${row.name}」？作废后保留历史，可再次编辑启用。`, '提示', { type: 'warning' })
    await invalidateCase(row.id)
    await loadCases()
  } catch (e) {
    // 取消
  }
}

// ---- 打标 ----
const tagVisible = ref(false)
const tagCase = ref(null)
const tagSelection = ref([])
const savingTags = ref(false)

const openTagDialog = async (row) => {
  tagCase.value = row
  tagSelection.value = row.scene_tags ? [...row.scene_tags] : []
  tagVisible.value = true
}

const saveTags = async () => {
  savingTags.value = true
  try {
    const r = await addCaseScenes(tagCase.value.id, tagSelection.value)
    ElMessage.success(`新增 ${r.added.length}，跳过 ${r.skipped.length}`)
    tagVisible.value = false
    await loadCases()
  } catch (e) {
    // 拦截器已弹
  } finally {
    savingTags.value = false
  }
}

// ---- 标注 ----
const annVisible = ref(false)
const annCase = ref(null)
const annList = ref([])
const savingAnn = ref(false)
const annForm = reactive({ dimension_code: '', level: null, note: '' })

const openAnnotationDialog = async (row) => {
  annCase.value = row
  annForm.dimension_code = ''
  annForm.level = null
  annForm.note = ''
  annVisible.value = true
  await loadAnnList(row.id)
}

const loadAnnList = async (caseId) => {
  try {
    annList.value = await listCaseAnnotations(caseId)
  } catch (e) {
    // 拦截器已弹
  }
}

const saveAnnotation = async () => {
  if (!annForm.dimension_code) {
    ElMessage.warning('请选择维度')
    return
  }
  savingAnn.value = true
  try {
    await addAnnotation(annCase.value.id, {
      dimension_code: annForm.dimension_code,
      level: annForm.level,
      note: annForm.note || null,
    })
    ElMessage.success('已提交')
    await loadAnnList(annCase.value.id)
    await loadCases()
  } catch (e) {
    // 拦截器已弹
  } finally {
    savingAnn.value = false
  }
}

// ---- 待办队列 ----
const todoAgentId = ref(null)
const todoList = ref([])
const loadingTodo = ref(false)

const loadTodo = async () => {
  loadingTodo.value = true
  try {
    todoList.value = await listTodo(todoAgentId.value || undefined)
  } catch (e) {
    // 拦截器已弹
  } finally {
    loadingTodo.value = false
  }
}

// 待办 → 切回用例管理定位该 case 打开标注
const annotateFromTodo = async (row) => {
  activeTab.value = 'manage'
  curAgentId.value = row.agent_id
  // P2-D16：待办 case 所在 suite 不一定是第一个——先占位 curSuiteId，loadSuites 会选中该 suite
  curSuiteId.value = row.suite_id
  await loadSuites()
  const match = cases.value.find((c) => c.id === row.case_id)
  if (match) {
    openAnnotationDialog(match)
  } else {
    // 若该 suite 下不存在（如被作废），回退列表
    ElMessage.info('该用例当前不在列表中')
  }
}

onMounted(async () => {
  agents.value = await listAgents()
  if (agents.value.length) {
    curAgentId.value = agents.value[0].id
    await loadSuites()
  }
  // P2-D10：待办队列是 staff 标注工作流功能（后端 /annotations/todo 仅 admin/evaluator，
  // viewer 无标注权限）——viewer 打开页面不再无条件触发 403 toast
  if (isStaff.value) {
    loadTodo()
  }
})
</script>

<style scoped>
.page-header { margin-bottom: 12px; }
.manage-layout { display: flex; gap: 12px; }
.suite-panel { width: 260px; flex-shrink: 0; }
.case-panel { flex: 1; min-width: 0; }
.toolbar { display: flex; gap: 8px; margin-bottom: 10px; align-items: center; flex-wrap: wrap; }
.suite-list { border: 1px solid #e4e7ed; border-radius: 4px; margin-top: 8px; max-height: 60vh; overflow-y: auto; }
.suite-item { padding: 8px 10px; cursor: pointer; border-bottom: 1px solid #f0f2f5; display: flex; align-items: center; gap: 8px; }
.suite-item:hover { background: #f5f7fa; }
.suite-item.active { background: #ecf5ff; }
.suite-name { flex: 1; font-size: 13px; }
.suite-meta { color: #909399; font-size: 12px; }
.suite-ops { display: flex; gap: 2px; }
.suite-empty { padding: 16px; color: #909399; text-align: center; font-size: 12px; }
.tag-gap { margin-right: 4px; }
.dim-note { color: #909399; font-size: 12px; }
.ann-head { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
</style>
