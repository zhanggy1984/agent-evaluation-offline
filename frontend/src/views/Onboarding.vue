<template>
  <div class="onboarding">
    <el-page-header @back="router.push('/agents')" content="快捷接入（URL 即接入）" />

    <el-alert type="info" :closable="false" class="mb"
      title="填 URL → 自动发现 → 确认 adapter → 凭证/接口 → 冒烟探测 → 用例骨架落库。平台定标准，agent 只适配；adapter 确认、凭证、落库需 Admin。" />

    <el-steps :active="step" finish-status="success" align-center class="mb">
      <el-step title="填 URL" />
      <el-step title="自动发现" />
      <el-step title="确认 adapter" />
      <el-step title="凭证与接口" />
      <el-step title="冒烟探测" />
      <el-step title="用例骨架" />
    </el-steps>

    <!-- Step 1：填 URL -->
    <el-card v-show="step === 0">
      <template #header>① 填 URL（agent 已部署，提供平台可访问地址）</template>
      <el-form :model="form" label-width="100px" @submit.prevent>
        <el-form-item label="Agent 名称" required>
          <el-input v-model="form.name" placeholder="如 customer-service" maxlength="128" />
        </el-form-item>
        <el-form-item label="服务 URL" required>
          <el-input v-model="form.base_url" placeholder="http://host.docker.internal:8000" />
          <div class="hint">平台出站访问地址；manifest 端点 = {URL}/api/contracts</div>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- Step 2：自动发现 -->
    <el-card v-show="step === 1">
      <template #header>② 自动发现（拉取 manifest → 解析 + adapter 草案 + 漂移提示）</template>
      <div v-if="!discoverData" class="muted">点击「下一步」触发发现…</div>
      <template v-else-if="!discoverData.ok">
        <el-alert type="error" :closable="false" title="发现失败"
          :description="(discoverData.errors || []).join('；')" />
        <pre v-if="discoverData.raw_sample" class="raw">{{ discoverData.raw_sample }}</pre>
        <el-button @click="step = 0" class="mt">修改 URL 重试</el-button>
      </template>
      <template v-else>
        <el-alert v-if="!adapter.valid" type="error" :closable="false" title="adapter 校验不通过（硬错误，需修正 manifest）"
          :description="(adapter.errors || []).join('；')" class="mb" />
        <el-alert v-else type="success" :closable="false" title="adapter 草案有效" class="mb" />
        <el-descriptions :column="2" border size="small" class="mb">
          <el-descriptions-item label="agent">{{ discoverData.agent }}</el-descriptions-item>
          <el-descriptions-item label="契约版本">{{ discoverData.contract_version }}</el-descriptions-item>
          <el-descriptions-item label="评测接口（llm）">
            {{ (discoverData.interfaces || []).filter((i) => i.llm).map((i) => i.name).join(', ') || '无' }}
          </el-descriptions-item>
          <el-descriptions-item label="场景">
            {{ (discoverData.scenes || []).map((s) => s.tag).join(', ') || '无' }}
          </el-descriptions-item>
          <el-descriptions-item label="input_fields" :span="2">
            {{ (adapter.input_fields || []).join(', ') || '无' }}
          </el-descriptions-item>
          <el-descriptions-item v-if="adapter.requires_auth" label="需要凭证" :span="2">是</el-descriptions-item>
        </el-descriptions>
        <div v-if="(discoverData.adapter_drift || []).length" class="mb">
          <el-alert type="warning" :closable="false" title="与已落库快照有漂移"
            :description="discoverData.adapter_drift.join('；')" />
        </div>
        <div v-if="adapter.warnings && adapter.warnings.length" class="mb">
          <div v-for="w in adapter.warnings" :key="w" class="muted warn">⚠ {{ w }}</div>
        </div>
        <el-button @click="doDiscover" :loading="busy" class="mt">重新发现</el-button>
      </template>
    </el-card>

    <!-- Step 3：确认 adapter -->
    <el-card v-show="step === 2">
      <template #header>③ 确认 adapter（可编辑 manifest，服务端权威派生落库）</template>
      <div class="mb">
        <el-button size="small" @click="resetManifest">重置为发现结果</el-button>
        <span class="muted"> 编辑后服务端重新校验派生；硬错误会被拒绝</span>
      </div>
      <el-input v-model="manifestText" type="textarea" :rows="16" class="mono"
        placeholder="粘贴/编辑 v2 manifest JSON" />
      <div v-if="!isAdmin" class="hint">⚠ 需 Admin 角色确认 adapter 落库</div>
    </el-card>

    <!-- Step 4：凭证与接口 -->
    <el-card v-show="step === 3">
      <template #header>④ 凭证与接口{{ syncResult ? '（已同步）' : '' }}</template>
      <template v-if="requireAuth">
        <el-alert type="info" :closable="false" class="mb"
          title="manifest 声明 {{auth.*}} 域，需配置出站凭证（Fernet 加密落库）" />
        <el-form :model="secrets" label-width="140px">
          <el-form-item v-for="k in authKeys" :key="k" :label="k" required>
            <el-input v-model="secrets[k]" type="password" show-password />
          </el-form-item>
        </el-form>
        <div v-if="!isAdmin" class="hint">⚠ 需 Admin 角色配置凭证</div>
      </template>
      <el-alert v-else type="success" :closable="false" title="无需凭证" class="mb" />
      <template v-if="syncResult">
        <el-descriptions :column="2" border size="small" class="mt">
          <el-descriptions-item label="接口 created">{{ syncResult.created.length }}</el-descriptions-item>
          <el-descriptions-item label="接口 skipped">{{ syncResult.skipped.length }}</el-descriptions-item>
          <el-descriptions-item label="场景 created" :span="2">{{ syncResult.scene_created }}</el-descriptions-item>
        </el-descriptions>
      </template>
    </el-card>

    <!-- Step 5：冒烟探测 -->
    <el-card v-show="step === 4">
      <template #header>⑤ 冒烟探测（真实调 agent，逐字段验证契约达标）</template>
      <el-form label-width="140px" class="mb">
        <el-form-item v-for="p in inputPaths" :key="p.path" :label="p.path">
          <el-input v-model="probeValues[p.path]" :placeholder="fileType(p) ? '平台 uploads 内样例文件路径' : '输入值'" />
        </el-form-item>
      </el-form>
      <el-alert v-if="hasFileField" type="info" :closable="false"
        title="文件型输入：样例文件需先放置到平台 uploads 目录" class="mb" />
      <template v-if="probeData">
        <el-alert :type="probeData.ok ? 'success' : 'error'" :closable="false" class="mb"
          :title="probeData.ok ? '契约探测达标' : (probeData.input_error ? '输入错误：先检查样例文件/输入，非 agent 契约问题' : '契约探测不达标')" />
        <el-table :data="probeData.interfaces" size="small" border>
          <el-table-column prop="interface_id" label="接口" width="70" />
          <el-table-column label="契约" width="70">
            <template #default="{ row }">{{ row.contract_type }}</template>
          </el-table-column>
          <el-table-column label="HTTP" width="70">
            <template #default="{ row }">{{ row.http_status }}</template>
          </el-table-column>
          <el-table-column label="字段到达">
            <template #default="{ row }">
              <el-tag v-for="k in ['meta', 'reasoning', 'tool_call', 'answer', 'usage', 'done']"
                :key="k" :type="row.fields?.[k] ? 'success' : 'info'" size="small" class="tag">
                {{ k }}{{ row.fields?.[k] ? '✓' : '✗' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="errors" label="错误">
            <template #default="{ row }">
              <span v-for="e in row.errors || []" :key="e" class="err">• {{ e }}<br /></span>
            </template>
          </el-table-column>
        </el-table>
      </template>
    </el-card>

    <!-- Step 6：用例骨架 -->
    <el-card v-show="step === 5">
      <template #header>⑥ 用例骨架（每 scene × 每 llm 接口一个 case；expected 永远人工补全）</template>
      <div v-if="!skeletonData" class="muted">
        点击「生成骨架」用冒烟输入生成用例模板…
      </div>
      <template v-else>
        <el-alert type="info" :closable="false" class="mb"
          :title="`suite：${skeletonData.suite.name}（${skeletonData.cases.length} 个 case，冒烟输入已预填）`" />
        <el-table :data="skeletonData.cases" size="small" border>
          <el-table-column prop="name" label="case 名" min-width="150" />
          <el-table-column prop="interface_name" label="接口" width="100" />
          <el-table-column prop="scene_tag" label="场景" width="100" />
          <el-table-column prop="input_type" label="类型" width="70" />
          <el-table-column label="input">
            <template #default="{ row }">
              <span class="mono small">{{ JSON.stringify(row.input) }}</span>
            </template>
          </el-table-column>
        </el-table>
        <div v-if="!isAdmin" class="hint mt">⚠ 需 Admin 角色确认落库（createSuite + createCase）</div>
      </template>
    </el-card>

    <!-- 底部导航 -->
    <div class="footer">
      <el-button v-if="step > 0 && !done" @click="step--">上一步</el-button>
      <el-button v-if="step < 5 && !done" type="primary" :loading="busy" :disabled="!canNext" @click="next">
        {{ nextLabel }}
      </el-button>
      <el-button v-if="step === 5 && !skeletonData && !done" type="primary" :loading="busy" @click="genSkeleton">
        生成骨架
      </el-button>
      <el-button v-if="step === 5 && skeletonData && !done" type="success" :loading="busy" :disabled="!isAdmin" @click="persist">
        确认落库（Admin）
      </el-button>
      <el-result v-if="done" icon="success" title="接入完成"
        :sub-title="`suite #${persisted.suiteId} + ${persisted.created.length} 用例已落库`">
        <template #extra>
          <el-button type="primary" @click="router.push('/agents')">返回 Agent 管理</el-button>
          <el-button v-if="persisted.skipped.length" @click="router.push(`/cases?suite=${persisted.suiteId}`)">
            查看 suite（{{ persisted.skipped.length }} 个跳过）
          </el-button>
        </template>
      </el-result>
    </div>
  </div>
</template>

<script setup>
defineOptions({ name: 'Onboarding' })

import { computed, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '../stores/auth'
import {
  addScenes, confirmAdapter, createAgent, discoverAgent, generateSkeleton,
  listInterfaces, probeAgentInput, setAgentAuth, syncInterfaces,
} from '../api/agents'
import { createSuite } from '../api/suites'
import { createCase } from '../api/cases'
import { unpackFields, collectInput } from '../utils/unpackFields'

const router = useRouter()
const auth = useAuthStore()
const { isAdmin } = storeToRefs(auth)

const step = ref(0)
const busy = ref(false)
const done = ref(false)

// Step 1
const form = reactive({ name: '', base_url: '' })
const agentId = ref(null)

// Step 2
const discoverData = ref(null)

// Step 3
const manifestText = ref('')
const confirmedManifest = ref(null) // 确认后 manifest（编辑后真相源，Step4 接口/场景同步据此派生）
const requireAuth = ref(false)

// Step 4
const secrets = reactive({})
const authKeys = ref([])
const syncResult = ref(null)

// Step 5
const inputFields = ref([])
const inputPaths = ref([])
const probeValues = reactive({})
const probeData = ref(null)
const probeInputUsed = ref(null) // 冒烟成功那次的输入，喂骨架（Q5 半自动核心）

// Step 6
const skeletonData = ref(null)
const persisted = ref({ suiteId: null, created: [], skipped: [] })

const adapter = computed(() => discoverData.value?.adapter || {})
const hasFileField = computed(() => inputFields.value.includes('file_path'))
const fileType = (p) => p.path === 'file_path'

const nextLabel = computed(() => ['创建并发现', '下一步', '确认 adapter', '配置并同步', '开始冒烟', ''][step.value])

const canNext = computed(() => {
  if (busy.value || done.value) return false
  switch (step.value) {
    case 0: return !!(form.name.trim() && form.base_url.trim())
    case 1: return discoverData.value?.ok && adapter.value.valid
    case 2: {
      if (!isAdmin.value) return false
      try { JSON.parse(manifestText.value); return true } catch { return false }
    }
    case 3: return isAdmin.value && (!requireAuth.value || authKeys.value.every((k) => secrets[k]))
    case 4: return true // 冒烟可反复重试
    default: return false
  }
})

const doDiscover = async () => {
  busy.value = true
  try {
    if (!agentId.value) {
      const created = await createAgent({
        name: form.name.trim(), base_url: form.base_url.trim(),
        adapter_type: 'config', adapter_config: {}, contract_version: '2.0',
      })
      agentId.value = created.id
    }
    discoverData.value = await discoverAgent(agentId.value)
    if (discoverData.value.ok) {
      manifestText.value = JSON.stringify(discoverData.value.manifest, null, 2)
      requireAuth.value = adapter.value.requires_auth
      authKeys.value = requireAuth.value ? collectAuthKeys(discoverData.value.manifest) : []
      if (requireAuth.value && !authKeys.value.length) authKeys.value = ['token'] // 兜底
      inputFields.value = adapter.value.input_fields || []
      const { paths, template } = unpackFields(inputFields.value)
      inputPaths.value = paths
      Object.assign(probeValues, flattenValues(template))
    }
  } catch (e) {
    // 拦截器已弹
  } finally {
    busy.value = false
  }
}

const resetManifest = () => {
  manifestText.value = JSON.stringify(discoverData.value.manifest, null, 2)
}

const next = async () => {
  busy.value = true
  try {
    if (step.value === 0) {
      await doDiscover()
      // 无论成败前移展示诊断卡片（失败走「修改 URL 重试」/「重新发现」）；
      // createAgent 抛错时 discoverData 仍 null → 停留 Step0（toast 已示因）
      if (discoverData.value) step.value = 1
    } else if (step.value === 1) {
      step.value = 2
    } else if (step.value === 2) {
      const manifest = JSON.parse(manifestText.value)
      // 以服务端权威派生为准（用户可能编辑过 manifest）：requires_auth/input_fields
      // 来自确认响应，凭证键从「编辑后」manifest 重扫，Step4/Step5 表单随之对齐
      const resp = await confirmAdapter(agentId.value, manifest)
      confirmedManifest.value = manifest // 接口/场景同步的真相源
      requireAuth.value = resp.requires_auth
      inputFields.value = resp.input_fields || []
      authKeys.value = requireAuth.value ? collectAuthKeys(manifest) : []
      if (requireAuth.value && !authKeys.value.length) authKeys.value = ['token']
      const { paths, template } = unpackFields(inputFields.value)
      inputPaths.value = paths
      probeData.value = null
      for (const k of Object.keys(probeValues)) delete probeValues[k]
      Object.assign(probeValues, flattenValues(template))
      ElMessage.success('adapter 已落库')
      step.value = 3
    } else if (step.value === 3) {
      if (requireAuth.value) {
        // 凭证点号键 → 嵌套对象（engine._get_path 按点号遍历 secrets）
        await setAgentAuth(agentId.value, collectInput(authKeys.value, { ...secrets }))
      }
      // 以确认后 manifest 为准（用户可能在 Step3 编辑过接口/场景）
      const m = confirmedManifest.value || discoverData.value
      const sync = await syncInterfaces(agentId.value,
        (m.interfaces || []).map((i) => ({
          name: i.name, path: i.path, method: i.method || 'POST', contract_type: i.contract_type,
        })))
      const sceneRes = await addScenes(agentId.value,
        (m.scenes || []).map((s) => ({ tag: s.tag, description: s.description || '' })))
      syncResult.value = { created: sync.created, skipped: sync.skipped, scene_created: sceneRes.created.length }
      step.value = 4
    } else if (step.value === 4) {
      const input = collectInput(inputFields.value, { ...probeValues })
      probeInputUsed.value = input
      probeData.value = await probeAgentInput(agentId.value, input)
      if (probeData.value.ok) step.value = 5
    }
  } catch (e) {
    // 拦截器已弹
  } finally {
    busy.value = false
  }
}

const genSkeleton = async () => {
  busy.value = true
  try {
    skeletonData.value = await generateSkeleton(agentId.value, probeInputUsed.value)
  } catch (e) {
    // 拦截器已弹
  } finally {
    busy.value = false
  }
}

const persist = async () => {
  if (!skeletonData.value) return
  busy.value = true
  try {
    const suite = await createSuite({
      agent_id: agentId.value, name: skeletonData.value.suite.name,
      description: skeletonData.value.suite.description,
    })
    const ifaces = await listInterfaces(agentId.value)
    const idByName = Object.fromEntries(ifaces.map((i) => [i.name, i.id]))
    const created = []
    const skipped = []
    for (const c of skeletonData.value.cases) {
      const iid = idByName[c.interface_name]
      if (!iid) {
        skipped.push(`${c.name}（接口 ${c.interface_name} 未 sync）`)
        continue
      }
      await createCase(suite.id, {
        interface_id: iid, name: c.name, input_type: c.input_type,
        input: c.input, expected: c.expected || {}, assertions: c.assertions || [],
        metrics: c.metrics || {},
        scenes: c.scene_tag ? [c.scene_tag] : [],
      })
      created.push(c.name)
    }
    persisted.value = { suiteId: suite.id, created, skipped }
    done.value = true
    ElMessage.success(`suite + ${created.length} 用例已落库`)
  } catch (e) {
    // 拦截器已弹
  } finally {
    busy.value = false
  }
}

// ---- 小工具 ----
function collectAuthKeys(manifest) {
  // 从 manifest 各渲染域收集 {{auth.*}} 完整键路径（如 username / credentials.token），去重保序
  const keys = new Set()
  const walk = (node) => {
    if (typeof node === 'string') {
      for (const m of node.matchAll(/\{\{auth\.([^}]+)\}\}/g)) keys.add(m[1].trim())
    } else if (Array.isArray(node)) {
      node.forEach(walk)
    } else if (node && typeof node === 'object') {
      Object.values(node).forEach(walk)
    }
  }
  walk(manifest)
  return [...keys]
}

function flattenValues(template, prefix = '', out = {}) {
  for (const [k, v] of Object.entries(template)) {
    const path = prefix ? `${prefix}.${k}` : k
    if (typeof v === 'object' && v !== null) flattenValues(v, path, out)
    else out[path] = v
  }
  return out
}
</script>

<style scoped>
.onboarding { max-width: 1080px; margin: 0 auto; padding: 16px; }
.mb { margin-bottom: 16px; }
.mt { margin-top: 16px; }
.hint { font-size: 12px; color: #909399; margin-top: 4px; }
.muted { color: #909399; font-size: 13px; }
.warn { color: #e6a23c; }
.err { color: #f56c6c; font-size: 12px; }
.raw { background: #f5f7fa; padding: 8px; font-size: 12px; overflow: auto; max-height: 160px; }
.mono { font-family: monospace; }
.small { font-size: 12px; }
.tag { margin-right: 4px; }
.footer { margin-top: 20px; display: flex; gap: 8px; align-items: center; }
</style>
