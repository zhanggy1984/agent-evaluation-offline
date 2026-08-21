<template>
  <div>
    <el-page-header class="page-header" :content="`共 ${agents.length} 个 agent`" @back="router.back">
      <template #title>Agent 管理</template>
    </el-page-header>

    <!-- 列表 -->
    <div class="toolbar">
      <el-button v-if="isAdmin" type="primary" @click="openCreate">新建 Agent</el-button>
      <el-button :loading="loading" @click="load">刷新</el-button>
    </div>
    <el-table :data="agents" border size="small" v-loading="loading">
      <el-table-column prop="name" label="名称" min-width="150" />
      <el-table-column prop="base_url" label="Base URL" min-width="220" show-overflow-tooltip />
      <el-table-column prop="adapter_type" label="适配器" width="90" />
      <el-table-column prop="contract_version" label="契约版本" width="100" />
      <el-table-column label="启用" width="80">
        <template #default="{ row }">
          <el-switch v-model="row.enabled" :disabled="!isAdmin" @change="(v) => toggleEnabled(row, v)" />
        </template>
      </el-table-column>
      <el-table-column label="凭证" width="80">
        <template #default="{ row }">
          <el-tag :type="row.auth_configured ? 'success' : 'info'" size="small">
            {{ row.auth_configured ? '已配置' : '未配置' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="Owner" width="110">
        <template #default="{ row }">{{ userName(row.owner_id) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="220" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="openDetail(row)">详情</el-button>
          <el-button v-if="isAdmin" link type="primary" @click="openEdit(row)">编辑</el-button>
          <el-button v-if="isAdmin" link type="danger" @click="handleDisable(row)">
            {{ row.enabled ? '停用' : '已停用' }}
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 新建/编辑 dialog -->
    <el-dialog v-model="formVisible" :title="editingId ? '编辑 Agent' : '新建 Agent'" width="560px">
      <el-form :model="form" label-width="100px" size="small">
        <el-form-item label="名称" required>
          <el-input v-model="form.name" />
        </el-form-item>
        <el-form-item label="Base URL" required>
          <el-input v-model="form.base_url" placeholder="http://host:port，须在白名单/CIDR 内" />
        </el-form-item>
        <el-form-item label="适配器">
          <el-select v-model="form.adapter_type" style="width: 100%">
            <el-option value="config" label="config（模板配置驱动）" />
            <el-option value="code" label="code（代码适配器）" />
          </el-select>
        </el-form-item>
        <el-form-item label="契约版本">
          <el-input v-model="form.contract_version" placeholder="如 1.0（缺省 seed 模板）" />
        </el-form-item>
        <el-form-item label="adapter_config">
          <el-input v-model="form.adapterConfigText" type="textarea" :rows="5"
            placeholder='{...} JSON，如 {"timeout_s": 60}' />
        </el-form-item>
        <el-form-item label="Owner">
          <el-select v-model="form.owner_id" clearable placeholder="未指派" style="width: 100%">
            <el-option v-for="u in users" :key="u.id" :value="u.id" :label="`${u.username}（${u.role}）`" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" @click="formVisible = false">取消</el-button>
        <el-button size="small" type="primary" :loading="saving" @click="saveAgent">保存</el-button>
      </template>
    </el-dialog>

    <!-- 详情 drawer -->
    <el-drawer v-model="detailVisible" size="72%" :title="detail?.name">
      <div v-if="detail" class="detail-head">
        <el-tag size="small" :type="detail.enabled ? 'success' : 'danger'">{{ detail.enabled ? '启用' : '停用' }}</el-tag>
        <el-tag size="small" :type="detail.auth_configured ? 'success' : 'info'">凭证{{ detail.auth_configured ? '已配置' : '未配置' }}</el-tag>
        <el-tag size="small" type="warning">{{ detail.adapter_type }}</el-tag>
        <span class="detail-url">{{ detail.base_url }}</span>
        <span class="detail-owner">Owner: {{ userName(detail.owner_id) }}</span>
      </div>

      <el-tabs v-model="detailTab">
        <!-- 接口 -->
        <el-tab-pane label="接口" name="ifaces">
          <div class="toolbar">
            <el-button v-if="isAdmin" type="primary" size="small" @click="openIfaceDialog()">新增接口</el-button>
            <el-button size="small" :loading="discovering" @click="handleDiscover">契约发现</el-button>
            <el-button v-if="discoverResult" size="small" type="warning" :loading="syncing" @click="handleSync">
              同步清单（{{ discoverResult.interfaces?.length || 0 }} 项）
            </el-button>
          </div>
          <el-table :data="interfaces" border size="small">
            <el-table-column prop="name" label="名称" min-width="120" />
            <el-table-column prop="path" label="Path" min-width="200" show-overflow-tooltip />
            <el-table-column prop="method" label="Method" width="80" />
            <el-table-column prop="contract_type" label="类型" width="80" />
            <el-table-column prop="contract_version" label="契约" width="90" />
            <el-table-column label="可重试" width="80">
              <template #default="{ row }">
                <el-tag :type="row.retryable ? 'success' : 'info'" size="small">{{ row.retryable ? '是' : '否' }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="启用" width="80">
              <template #default="{ row }">
                <el-tag :type="row.enabled ? 'success' : 'info'" size="small">{{ row.enabled ? '是' : '否' }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="140" fixed="right">
              <template #default="{ row }">
                <el-button v-if="isAdmin" link type="primary" @click="openIfaceDialog(row)">编辑</el-button>
                <el-button v-if="isAdmin" link type="danger" @click="handleDisableIface(row)">停用</el-button>
              </template>
            </el-table-column>
          </el-table>

          <!-- 发现结果 -->
          <div v-if="discoverResult" class="discover-box">
            <el-alert :type="discoverResult.ok ? 'success' : 'error'" :closable="false"
              :title="discoverResult.ok
                ? `发现成功：契约 ${discoverResult.contract_version}，场景 ${discoverResult.scenes?.length || 0}，接口 ${discoverResult.interfaces?.length || 0}`
                : '发现失败（诊断信息）'" />
            <template v-if="discoverResult.ok">
              <div v-if="discoverResult.added?.length" class="discover-sec">
                <b>待补录（added）：</b>
                <el-tag v-for="i in discoverResult.added" :key="i.path" size="small" class="tag-gap">
                  {{ i.method }} {{ i.path }}
                </el-tag>
              </div>
              <div v-if="discoverResult.existing?.length" class="discover-sec">
                <b>已存在（existing）：</b>
                <el-tag v-for="i in discoverResult.existing" :key="i.path" size="small" type="info" class="tag-gap">
                  {{ i.method }} {{ i.path }}{{ i.changed?.length ? `（差异: ${i.changed.join(',')}）` : '' }}
                </el-tag>
              </div>
              <div v-if="discoverResult.missing?.length" class="discover-sec">
                <b>库中未被声明（missing，疑似下线）：</b>
                <el-tag v-for="i in discoverResult.missing" :key="i.path" size="small" type="warning" class="tag-gap">
                  {{ i.method }} {{ i.path }}
                </el-tag>
              </div>
              <div v-if="discoverResult.auxiliary?.length" class="discover-sec">
                <b>辅助接口（llm=false，不建 agent_interface）：</b>
                <el-tag v-for="i in discoverResult.auxiliary" :key="i.path" size="small" type="info" class="tag-gap">
                  {{ i.method }} {{ i.path }}
                </el-tag>
              </div>
            </template>
            <template v-else>
              <p v-for="(e, i) in discoverResult.errors" :key="i" class="discover-err">• {{ e }}</p>
              <pre v-if="discoverResult.raw_sample" class="discover-raw">{{ discoverResult.raw_sample }}</pre>
            </template>
          </div>

          <!-- 场景清单 -->
          <div class="scene-box">
            <h4>场景清单（scene_catalog）</h4>
            <div v-if="isAdmin" class="toolbar">
              <el-input v-model="sceneForm.tag" placeholder="场景标签" size="small" style="width: 200px" />
              <el-input v-model="sceneForm.description" placeholder="描述（可选）" size="small" style="width: 240px" />
              <el-button size="small" type="primary" @click="addScene">添加场景</el-button>
            </div>
            <el-table :data="scenes" border size="small">
              <el-table-column prop="scene_tag" label="标签" min-width="140" />
              <el-table-column prop="description" label="描述" min-width="200" />
            </el-table>
          </div>
        </el-tab-pane>

        <!-- 权重 -->
        <el-tab-pane label="权重" name="weights">
          <el-table :data="weightRows" border size="small">
            <el-table-column prop="name" label="维度" min-width="110" />
            <el-table-column prop="code" label="Code" min-width="150" />
            <el-table-column prop="cat" label="类别" width="90" />
            <el-table-column label="权重" width="200">
              <template #default="{ row }">
                <el-input-number v-model="row.weight" :disabled="!editableWeight(row)" :controls="false"
                  :min="0" :max="1" :step="0.01" style="width: 100%" />
              </template>
            </el-table-column>
            <el-table-column label="说明" min-width="180">
              <template #default="{ row }">
                <span v-if="row.cat !== 'accuracy'" class="dim-note">性能/成本维度不参与加权与<TermTip term="gate" label="门禁" /></span>
                <span v-else class="dim-note">参与 accuracy 加权</span>
              </template>
            </el-table-column>
          </el-table>
          <div class="footer-bar">
            <el-button type="primary" size="small" :loading="savingWeights" @click="saveWeights">保存权重</el-button>
          </div>
        </el-tab-pane>

        <!-- 阈值 -->
        <el-tab-pane label="阈值" name="targets">
          <div class="toolbar">
            <el-select v-model="targetIface" placeholder="选择接口" size="small" style="width: 260px" @change="loadTargets">
              <el-option v-for="i in interfaces" :key="i.id" :value="i.id" :label="`${i.name}（${i.path}）`" />
            </el-select>
            <el-button size="small" :loading="loadingTargets" @click="loadTargets">刷新</el-button>
          </div>
          <el-table :data="targetRows" border size="small">
            <el-table-column prop="dimension_code" label="维度" min-width="150" />
            <el-table-column width="200">
              <template #header><TermTip term="target" /></template>
              <template #default="{ row }">
                <el-input-number v-model="row.target_score" :disabled="!isStaff" :controls="false"
                  :min="0" :max="100" :step="0.1" style="width: 100%" />
              </template>
            </el-table-column>
            <el-table-column label="双签状态" width="130">
              <template #default="{ row }">
                <el-tag size="small"
                  :type="row.approval_status === 'approved' ? 'success' : row.approval_status === 'auto' ? 'info' : 'warning'">
                  {{ approvalLabel(row.approval_status) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="calibration_source" label="标定来源" width="100" />
            <el-table-column label="签名人" width="120">
              <template #default="{ row }">{{ signLabel(row) }}</template>
            </el-table-column>
          </el-table>
          <div v-if="targetRows.length" class="footer-bar">
            <el-button v-if="isStaff" type="primary" size="small" :loading="savingTargets" @click="saveTargets">
              保存阈值
            </el-button>
            <span class="dim-note">手动修改进入双签流程：第一人 pending_approval，第二人 approved</span>
          </div>
        </el-tab-pane>

        <!-- 凭证 -->
        <el-tab-pane label="凭证" name="auth">
          <el-form :model="authForm" label-width="100px" size="small" style="max-width: 420px">
            <el-form-item label="Username">
              <el-input v-model="authForm.username" autocomplete="off" />
            </el-form-item>
            <el-form-item label="Password">
              <el-input v-model="authForm.password" type="password" autocomplete="off" />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" size="small" :loading="savingAuth" @click="saveAuth">保存凭证</el-button>
            </el-form-item>
          </el-form>
          <div class="dim-note">凭证 Fernet 加密落库，接口读取时注入模板域 {auth.username}/{auth.password}，前端不回显明文。</div>
        </el-tab-pane>

        <!-- 探测 -->
        <el-tab-pane label="探测" name="probe">
          <div class="toolbar">
            <el-select v-model="probeIface" placeholder="全部接口" clearable size="small" style="width: 260px">
              <el-option v-for="i in enabledIfaces" :key="i.id" :value="i.id" :label="`${i.name}（${i.path}）`" />
            </el-select>
            <el-button type="warning" size="small" :loading="probing" @click="runProbe">开始探测</el-button>
            <span class="dim-note">探测会真实调用 agent 接口（消耗 LLM 费用与时间）</span>
          </div>
          <el-table v-if="probeResults.length" :data="probeResults" border size="small">
            <el-table-column prop="interface_id" label="接口" width="80" />
            <el-table-column prop="contract_type" label="类型" width="80" />
            <el-table-column label="结果" width="90">
              <template #default="{ row }">
                <el-tag :type="row.ok ? 'success' : 'danger'" size="small">{{ row.ok ? '通过' : '失败' }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="http_status" label="HTTP" width="80" />
            <el-table-column prop="elapsed_ms" label="耗时(ms)" width="100" />
            <el-table-column label="字段/错误" min-width="280">
              <template #default="{ row }">
                <span v-if="row.errors?.length" class="probe-err">{{ row.errors.join('；') }}</span>
                <span v-else-if="row.fields">
                  <el-tag v-for="(v, k) in fieldsSummary(row.fields)" :key="k" size="small" class="tag-gap"
                    :type="v ? 'success' : 'info'">{{ k }}</el-tag>
                </span>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>
      </el-tabs>
    </el-drawer>

    <!-- 接口新增/编辑 dialog -->
    <el-dialog v-model="ifaceFormVisible" :title="ifaceEditingId ? '编辑接口' : '新增接口'" width="520px">
      <el-form :model="ifaceForm" label-width="110px" size="small">
        <el-form-item label="名称" required><el-input v-model="ifaceForm.name" /></el-form-item>
        <el-form-item label="Path" required><el-input v-model="ifaceForm.path" placeholder="/api/v1/chat" /></el-form-item>
        <el-form-item label="Method">
          <el-select v-model="ifaceForm.method" style="width: 100%">
            <el-option v-for="m in ['POST', 'GET', 'PUT', 'DELETE', 'PATCH']" :key="m" :value="m" :label="m" />
          </el-select>
        </el-form-item>
        <el-form-item label="契约类型">
          <el-select v-model="ifaceForm.contract_type" style="width: 100%">
            <el-option value="sse" label="sse（流式）" />
            <el-option value="sync" label="sync（同步）" />
          </el-select>
        </el-form-item>
        <el-form-item label="契约版本"><el-input v-model="ifaceForm.contract_version" /></el-form-item>
        <el-form-item label="可重试">
          <el-switch v-model="ifaceForm.retryable" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" @click="ifaceFormVisible = false">取消</el-button>
        <el-button size="small" type="primary" :loading="savingIface" @click="saveIface">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  addScenes, createAgent, createInterface, deleteInterface, disableAgent, discoverAgent,
  getAgentWeights, getTargets, listAgents, listInterfaces, listScenes, probeAgent,
  setAgentAuth, setAgentWeights, setTargets, syncInterfaces, updateAgent, updateInterface,
} from '../api/agents'
import { listUsers } from '../api/users'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import TermTip from '../components/TermTip.vue'

const router = useRouter()
const auth = useAuthStore()
const { isAdmin } = storeToRefs(auth)
const isStaff = computed(() => ['admin', 'evaluator'].includes(auth.role))

const loading = ref(false)
const agents = ref([])
const users = ref([])

// 固定 7 评测维度（与 core/constants 一致；性能/成本不参与加权与门禁）
const DIMS = [
  { code: 'completeness', name: '完成度', cat: 'accuracy' },
  { code: 'factuality', name: '事实性', cat: 'accuracy' },
  { code: 'reasoning_quality', name: '思考链', cat: 'accuracy' },
  { code: 'tool_usage', name: '工具使用', cat: 'accuracy' },
  { code: 'ttft', name: '首字延迟', cat: 'performance' },
  { code: 'e2e', name: '端到端延迟', cat: 'performance' },
  { code: 'token_cost', name: 'Token 成本', cat: 'cost' },
]

const userName = (id) => users.value.find((u) => u.id === id)?.username || (id ? `#${id}` : '未指派')

const load = async () => {
  loading.value = true
  try {
    agents.value = await listAgents()
  } catch (e) {
    // 拦截器已弹
  } finally {
    loading.value = false
  }
}

const loadUsers = async () => {
  try {
    const r = await listUsers({ page_size: 100 })
    users.value = r.items
  } catch (e) {
    // 拦截器已弹
  }
}

// ---- 新建/编辑 ----
const formVisible = ref(false)
const editingId = ref(null)
const saving = ref(false)
const form = reactive({ name: '', base_url: '', adapter_type: 'config', contract_version: '', adapterConfigText: '{}', owner_id: null })

const resetForm = (agent) => {
  editingId.value = agent?.id || null
  form.name = agent?.name || ''
  form.base_url = agent?.base_url || ''
  form.adapter_type = agent?.adapter_type || 'config'
  form.contract_version = agent?.contract_version || ''
  form.adapterConfigText = agent ? JSON.stringify(agent.adapter_config || {}, null, 2) : '{}'
  form.owner_id = agent?.owner_id ?? null
}

const openCreate = () => {
  resetForm()
  formVisible.value = true
}

const openEdit = (agent) => {
  resetForm(agent)
  formVisible.value = true
}

const saveAgent = async () => {
  let adapterConfig
  try {
    adapterConfig = JSON.parse(form.adapterConfigText || '{}')
  } catch (e) {
    ElMessage.error('adapter_config 不是合法 JSON')
    return
  }
  const payload = {
    name: form.name, base_url: form.base_url, adapter_type: form.adapter_type,
    adapter_config: adapterConfig, contract_version: form.contract_version || null,
    owner_id: form.owner_id ?? null,
  }
  saving.value = true
  try {
    if (editingId.value) await updateAgent(editingId.value, payload)
    else await createAgent(payload)
    ElMessage.success('已保存')
    formVisible.value = false
    await load()
  } catch (e) {
    // 拦截器已弹
  } finally {
    saving.value = false
  }
}

const toggleEnabled = async (row, v) => {
  try {
    if (!v) {
      await ElMessageBox.confirm(`确认停用 agent「${row.name}」？`, '提示', { type: 'warning' })
      await disableAgent(row.id)
    } else {
      await updateAgent(row.id, { enabled: true })
    }
    ElMessage.success('已更新')
  } catch (e) {
    row.enabled = !v
  }
}

const handleDisable = async (row) => {
  try {
    await ElMessageBox.confirm(`确认停用 agent「${row.name}」？停用后无法探测/执行。`, '提示', { type: 'warning' })
    await disableAgent(row.id)
    await load()
  } catch (e) {
    // 取消或拦截器已弹
  }
}

// ---- 详情 drawer ----
const detailVisible = ref(false)
const detail = ref(null)
const detailTab = ref('ifaces')
const interfaces = ref([])
const discovering = ref(false)
const discoverResult = ref(null)
const syncing = ref(false)

const openDetail = async (row) => {
  detail.value = row
  detailVisible.value = true
  detailTab.value = 'ifaces'
  await Promise.all([loadInterfaces(), loadScenes(), loadWeights()])
}

const loadInterfaces = async () => {
  if (!detail.value) return
  try {
    interfaces.value = await listInterfaces(detail.value.id)
  } catch (e) {
    // 拦截器已弹
  }
}

const handleDiscover = async () => {
  discovering.value = true
  discoverResult.value = null
  try {
    discoverResult.value = await discoverAgent(detail.value.id)
  } catch (e) {
    // 拦截器已弹
  } finally {
    discovering.value = false
  }
}

const handleSync = async () => {
  const list = discoverResult.value?.interfaces || []
  if (!list.length) return
  try {
    await ElMessageBox.confirm(`确认按标准契约补录 ${list.length} 个接口？已存在接口将跳过（幂等）。`, '同步确认', { type: 'warning' })
  } catch (e) {
    return
  }
  syncing.value = true
  try {
    const r = await syncInterfaces(detail.value.id, list.map((i) => ({
      name: i.name, path: i.path, method: i.method, contract_type: i.contract_type,
    })))
    ElMessage.success(`补录 ${r.created.length}，跳过 ${r.skipped.length}${r.renamed.length ? `，改名 ${r.renamed.length}` : ''}`)
    await loadInterfaces()
  } catch (e) {
    // 拦截器已弹
  } finally {
    syncing.value = false
  }
}

// ---- 接口 CRUD ----
const ifaceFormVisible = ref(false)
const ifaceEditingId = ref(null)
const savingIface = ref(false)
const ifaceForm = reactive({ name: '', path: '', method: 'POST', contract_type: 'sse', contract_version: '1.0', retryable: true })

const openIfaceDialog = (row) => {
  ifaceEditingId.value = row?.id || null
  ifaceForm.name = row?.name || ''
  ifaceForm.path = row?.path || ''
  ifaceForm.method = row?.method || 'POST'
  ifaceForm.contract_type = row?.contract_type || 'sse'
  ifaceForm.contract_version = row?.contract_version || '1.0'
  ifaceForm.retryable = row?.retryable ?? true
  ifaceFormVisible.value = true
}

const saveIface = async () => {
  savingIface.value = true
  try {
    if (ifaceEditingId.value) {
      await updateInterface(detail.value.id, ifaceEditingId.value, { ...ifaceForm })
    } else {
      await createInterface(detail.value.id, { ...ifaceForm })
    }
    ElMessage.success('已保存')
    ifaceFormVisible.value = false
    await loadInterfaces()
  } catch (e) {
    // 拦截器已弹
  } finally {
    savingIface.value = false
  }
}

const handleDisableIface = async (row) => {
  try {
    await ElMessageBox.confirm(`确认停用接口「${row.name}」？`, '提示', { type: 'warning' })
    await deleteInterface(detail.value.id, row.id)
    await loadInterfaces()
  } catch (e) {
    // 取消
  }
}

// ---- 场景清单 ----
const scenes = ref([])
const sceneForm = reactive({ tag: '', description: '' })

const loadScenes = async () => {
  if (!detail.value) return
  try {
    scenes.value = await listScenes(detail.value.id)
  } catch (e) {
    // 拦截器已弹
  }
}

const addScene = async () => {
  if (!sceneForm.tag) {
    ElMessage.warning('请输入场景标签')
    return
  }
  try {
    const r = await addScenes(detail.value.id, [{ tag: sceneForm.tag, description: sceneForm.description }])
    ElMessage.success(`新增 ${r.created.length}，跳过 ${r.skipped.length}`)
    sceneForm.tag = ''
    sceneForm.description = ''
    await loadScenes()
  } catch (e) {
    // 拦截器已弹
  }
}

// ---- 权重 ----
const weightRows = ref([])
const savingWeights = ref(false)
const editableWeight = (row) => isStaff.value && row.cat === 'accuracy'

const loadWeights = async () => {
  if (!detail.value) return
  let cur = {}
  try {
    cur = await getAgentWeights(detail.value.id)
  } catch (e) {
    // 拦截器已弹
  }
  weightRows.value = DIMS.map((d) => ({ ...d, weight: d.cat === 'accuracy' ? (cur[d.code] ?? 0.25) : null }))
}

const saveWeights = async () => {
  const weights = {}
  for (const r of weightRows.value) {
    if (r.cat === 'accuracy') weights[r.code] = r.weight
  }
  savingWeights.value = true
  try {
    await setAgentWeights(detail.value.id, weights)
    ElMessage.success('权重已保存')
  } catch (e) {
    // 拦截器已弹
  } finally {
    savingWeights.value = false
  }
}

// ---- 阈值（双签） ----
const targetIface = ref(null)
const targetRows = ref([])
const loadingTargets = ref(false)
const savingTargets = ref(false)

const APPROVAL_LABEL = { auto: '自动标定', pending_approval: '待双签', approved: '已双签' }
const approvalLabel = (s) => APPROVAL_LABEL[s] || s
const signLabel = (row) => {
  if (row.approval_status === 'approved') return `${row.approved_by_1 || '?'}/${row.approved_by_2 || '?'}`
  return row.approved_by_1 ? `#${row.approved_by_1}` : '—'
}

const loadTargets = async () => {
  if (!detail.value || !targetIface.value) return
  loadingTargets.value = true
  try {
    targetRows.value = await getTargets(detail.value.id, targetIface.value)
  } catch (e) {
    // 拦截器已弹
  } finally {
    loadingTargets.value = false
  }
}

const saveTargets = async () => {
  const targetScores = {}
  for (const r of targetRows.value) targetScores[r.dimension_code] = r.target_score
  savingTargets.value = true
  try {
    await setTargets(detail.value.id, targetIface.value, targetScores)
    ElMessage.success('阈值已保存，进入双签流程')
    await loadTargets()
  } catch (e) {
    // 拦截器已弹
  } finally {
    savingTargets.value = false
  }
}

// ---- 凭证 ----
const authForm = reactive({ username: '', password: '' })
const savingAuth = ref(false)

const saveAuth = async () => {
  if (!authForm.username || !authForm.password) {
    ElMessage.warning('请输入用户名与密码')
    return
  }
  savingAuth.value = true
  try {
    await setAgentAuth(detail.value.id, { username: authForm.username, password: authForm.password })
    ElMessage.success('凭证已保存')
    detail.value.auth_configured = true
    authForm.username = ''
    authForm.password = ''
  } catch (e) {
    // 拦截器已弹
  } finally {
    savingAuth.value = false
  }
}

// ---- 探测 ----
const probeIface = ref(null)
const probing = ref(false)
const probeResults = ref([])

const enabledIfaces = computed(() => interfaces.value.filter((i) => i.enabled))

const fieldsSummary = (fields) => {
  const keys = ['usage', 'done', 'answer', 'reasoning', 'tool_calls', 'timing_start', 'timing_end']
  const out = {}
  for (const k of keys) if (k in fields) out[k] = !!fields[k]
  return out
}

const runProbe = async () => {
  try {
    await ElMessageBox.confirm(
      '探测会真实调用 agent 接口并消耗 LLM 费用与时间，确认开始？', '探测确认', { type: 'warning' })
  } catch (e) {
    return
  }
  probing.value = true
  probeResults.value = []
  try {
    const params = probeIface.value ? { interface_id: probeIface.value } : {}
    const r = await probeAgent(detail.value.id, params)
    probeResults.value = r.interfaces
    ElMessage.success(`探测完成：${r.interfaces.filter((i) => i.ok).length}/${r.interfaces.length} 通过`)
  } catch (e) {
    // 拦截器已弹
  } finally {
    probing.value = false
  }
}

onMounted(() => {
  load()
  loadUsers()
})
</script>

<style scoped>
.page-header { margin-bottom: 12px; }
.toolbar { display: flex; gap: 8px; margin-bottom: 10px; align-items: center; flex-wrap: wrap; }
.footer-bar { margin-top: 10px; display: flex; gap: 8px; align-items: center; }
.detail-head { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }
.detail-url { color: #606266; font-size: 13px; }
.detail-owner { color: #909399; font-size: 13px; }
.discover-box { margin-top: 10px; padding: 10px; border: 1px solid #e4e7ed; border-radius: 4px; }
.discover-sec { margin-top: 8px; }
.discover-err { margin: 4px 0; color: #f56c6c; }
.discover-raw { margin: 8px 0 0; background: #f5f7fa; padding: 8px; font-size: 12px; overflow-x: auto; max-height: 160px; }
.tag-gap { margin-right: 4px; }
.scene-box { margin-top: 16px; }
.scene-box h4 { margin: 0 0 8px; }
.dim-note { color: #909399; font-size: 12px; }
.probe-err { color: #f56c6c; font-size: 12px; }
</style>
