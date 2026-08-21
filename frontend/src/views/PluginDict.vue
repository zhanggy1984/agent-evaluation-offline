<template>
  <div>
    <el-page-header class="page-header" :content="isAdmin ? '可新增/编辑（admin）' : '只读'">
      <template #title>插件字典</template>
    </el-page-header>

    <el-tabs v-model="tab">
      <!-- 模型价格 -->
      <el-tab-pane label="模型价格" name="prices">
        <div class="toolbar">
          <el-button v-if="isAdmin" type="primary" size="small" @click="openPriceDialog()">新增价格</el-button>
          <el-button size="small" :loading="loadingPrices" @click="loadPrices">刷新</el-button>
        </div>
        <el-table :data="prices" border size="small" v-loading="loadingPrices">
          <el-table-column prop="model" label="模型" min-width="180" />
          <el-table-column label="输入(元/百万 token)" width="150">
            <template #default="{ row }">{{ row.input_price }}</template>
          </el-table-column>
          <el-table-column label="输出(元/百万 token)" width="150">
            <template #default="{ row }">{{ row.output_price }}</template>
          </el-table-column>
          <el-table-column label="缓存命中(元/百万 token)" width="170">
            <template #default="{ row }">{{ row.cache_hit_price ?? '—' }}</template>
          </el-table-column>
          <el-table-column prop="effective_from" label="生效时间" min-width="170" />
        </el-table>
      </el-tab-pane>

      <!-- 断言算子 -->
      <el-tab-pane label="断言算子" name="ops">
        <div class="toolbar">
          <el-button v-if="isAdmin" type="primary" size="small" @click="openOpDialog()">新增算子</el-button>
          <el-button size="small" :loading="loadingOps" @click="loadOps">刷新</el-button>
        </div>
        <el-table :data="ops" border size="small" v-loading="loadingOps">
          <el-table-column prop="op" label="算子名" min-width="180" />
          <el-table-column prop="class_path" label="class_path" min-width="360" />
        </el-table>
        <div class="dim-note">class_path 走代码内白名单校验（frozenset），数据库只存指向白名单的索引，不可运行时增删。</div>
      </el-tab-pane>

      <!-- rubric -->
      <el-tab-pane label="Rubric" name="rubrics">
        <div class="toolbar">
          <el-button v-if="isAdmin" type="primary" size="small" @click="openRubricDialog()">新增 rubric</el-button>
          <el-button size="small" :loading="loadingRubrics" @click="loadRubrics">刷新</el-button>
        </div>
        <el-table :data="rubrics" border size="small" v-loading="loadingRubrics">
          <el-table-column prop="dimension_code" label="维度" min-width="130" />
          <el-table-column prop="interface_id" label="接口 ID" width="90" />
          <el-table-column prop="version" label="版本" width="90" />
          <el-table-column label="模板" min-width="320">
            <template #default="{ row }">
              <pre class="tmpl">{{ JSON.stringify(row.template, null, 2) }}</pre>
            </template>
          </el-table-column>
          <el-table-column v-if="isAdmin" label="操作" width="120" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" @click="openRubricDialog(row)">编辑</el-button>
              <el-button link type="danger" @click="handleDeleteRubric(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>
    </el-tabs>

    <!-- 价格 dialog -->
    <el-dialog v-model="priceVisible" title="新增模型价格" width="460px">
      <el-form :model="priceForm" label-width="110px" size="small">
        <el-form-item label="模型名" required><el-input v-model="priceForm.model" placeholder="如 deepseek-chat" /></el-form-item>
        <el-form-item label="输入价(元/百万)" required>
          <el-input-number v-model="priceForm.input_price" :controls="false" :min="0" style="width: 100%" />
        </el-form-item>
        <el-form-item label="输出价(元/百万)" required>
          <el-input-number v-model="priceForm.output_price" :controls="false" :min="0" style="width: 100%" />
        </el-form-item>
        <el-form-item label="缓存命中(元/百万)">
          <el-input-number v-model="priceForm.cache_hit_price" :controls="false" :min="0" style="width: 100%" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" @click="priceVisible = false">取消</el-button>
        <el-button size="small" type="primary" :loading="savingPrice" @click="savePrice">保存</el-button>
      </template>
    </el-dialog>

    <!-- 算子 dialog -->
    <el-dialog v-model="opVisible" title="新增断言算子" width="520px">
      <el-form :model="opForm" label-width="100px" size="small">
        <el-form-item label="算子名" required><el-input v-model="opForm.op" placeholder="如 keyword_contains" /></el-form-item>
        <el-form-item label="class_path" required>
          <el-select v-model="opForm.class_path" filterable style="width: 100%">
            <el-option-group label="断言算子">
              <el-option v-for="p in assertionPaths" :key="p" :value="p" :label="p" />
            </el-option-group>
            <el-option-group label="评测指标">
              <el-option v-for="p in metricPaths" :key="p" :value="p" :label="p" />
            </el-option-group>
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" @click="opVisible = false">取消</el-button>
        <el-button size="small" type="primary" :loading="savingOp" @click="saveOp">保存</el-button>
      </template>
    </el-dialog>

    <!-- rubric dialog -->
    <el-dialog v-model="rubricVisible" :title="rubricEditingId ? '编辑 rubric' : '新增 rubric'" width="560px">
      <el-form :model="rubricForm" label-width="100px" size="small">
        <el-form-item label="维度" required>
          <el-select v-model="rubricForm.dimension_code" style="width: 100%">
            <el-option v-for="d in DIMS" :key="d.code" :value="d.code" :label="`${d.name}（${d.code}）`" />
          </el-select>
        </el-form-item>
        <el-form-item label="接口 ID">
          <el-input-number v-model="rubricForm.interface_id" :controls="false" :min="0" style="width: 100%" />
          <div class="dim-note">0 = 通用 rubric（未覆盖时生效）</div>
        </el-form-item>
        <el-form-item label="版本" required><el-input v-model="rubricForm.version" /></el-form-item>
        <el-form-item label="模板">
          <el-input v-model="rubricForm.templateText" type="textarea" :rows="6" placeholder="{...} JSON 模板" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" @click="rubricVisible = false">取消</el-button>
        <el-button size="small" type="primary" :loading="savingRubric" @click="saveRubric">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  createAssertionOp, createModelPrice, createRubric, deleteRubric, listAssertionOps,
  listModelPrices, listRubrics, updateRubric,
} from '../api/config'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const { isAdmin } = storeToRefs(auth)

const tab = ref('prices')

// 固定维度（与 core/constants 一致）
const DIMS = [
  { code: 'completeness', name: '完成度' }, { code: 'factuality', name: '事实性' },
  { code: 'reasoning_quality', name: '思考链' }, { code: 'tool_usage', name: '工具使用' },
  { code: 'ttft', name: '首字延迟' }, { code: 'e2e', name: '端到端延迟' },
  { code: 'token_cost', name: 'Token 成本' },
]

// class_path 白名单（与 core/constants.ALLOWED_CLASS_PATHS 一致）
const assertionPaths = [
  'assertions.ops.structure.FieldPresentOp', 'assertions.ops.structure.FieldNonemptyOp',
  'assertions.ops.structure.ValueRangeOp', 'assertions.ops.structure.ValueEqualsOp',
  'assertions.ops.text.KeywordContainsOp', 'assertions.ops.tool.ToolCalledOp',
  'assertions.ops.tool.ToolNotCalledOp', 'assertions.ops.retrieval.SourceHitOp',
  'assertions.ops.collection.ListPrecisionRecallOp', 'assertions.ops.collection.ListContainsOp',
]
const metricPaths = [
  'metrics.accuracy.completeness.CompletenessMetric', 'metrics.accuracy.tool_usage.ToolUsageMetric',
  'metrics.accuracy.factuality.FactualityMetric', 'metrics.accuracy.reasoning.ReasoningMetric',
  'metrics.performance.ttft.TtftMetric', 'metrics.performance.e2e.E2eMetric',
  'metrics.cost.token_cost.TokenCostMetric',
]

// ---- 价格 ----
const prices = ref([])
const loadingPrices = ref(false)
const priceVisible = ref(false)
const savingPrice = ref(false)
const priceForm = reactive({ model: '', input_price: 0, output_price: 0, cache_hit_price: null })

const loadPrices = async () => {
  loadingPrices.value = true
  try {
    prices.value = await listModelPrices()
  } catch (e) {
    // 拦截器已弹
  } finally {
    loadingPrices.value = false
  }
}

const openPriceDialog = () => {
  priceForm.model = ''
  priceForm.input_price = 0
  priceForm.output_price = 0
  priceForm.cache_hit_price = null
  priceVisible.value = true
}

const savePrice = async () => {
  if (!priceForm.model) {
    ElMessage.warning('请输入模型名')
    return
  }
  savingPrice.value = true
  try {
    await createModelPrice({ ...priceForm })
    ElMessage.success('已保存')
    priceVisible.value = false
    await loadPrices()
  } catch (e) {
    // 拦截器已弹
  } finally {
    savingPrice.value = false
  }
}

// ---- 算子 ----
const ops = ref([])
const loadingOps = ref(false)
const opVisible = ref(false)
const savingOp = ref(false)
const opForm = reactive({ op: '', class_path: '' })

const loadOps = async () => {
  loadingOps.value = true
  try {
    ops.value = await listAssertionOps()
  } catch (e) {
    // 拦截器已弹
  } finally {
    loadingOps.value = false
  }
}

const openOpDialog = () => {
  opForm.op = ''
  opForm.class_path = ''
  opVisible.value = true
}

const saveOp = async () => {
  if (!opForm.op || !opForm.class_path) {
    ElMessage.warning('请填写算子名与 class_path')
    return
  }
  savingOp.value = true
  try {
    await createAssertionOp({ ...opForm })
    ElMessage.success('已保存')
    opVisible.value = false
    await loadOps()
  } catch (e) {
    // 拦截器已弹
  } finally {
    savingOp.value = false
  }
}

// ---- rubric ----
const rubrics = ref([])
const loadingRubrics = ref(false)
const rubricVisible = ref(false)
const rubricEditingId = ref(null)
const savingRubric = ref(false)
const rubricForm = reactive({ dimension_code: '', interface_id: 0, version: '', templateText: '{}' })

const loadRubrics = async () => {
  loadingRubrics.value = true
  try {
    rubrics.value = await listRubrics()
  } catch (e) {
    // 拦截器已弹
  } finally {
    loadingRubrics.value = false
  }
}

const openRubricDialog = (row) => {
  rubricEditingId.value = row?.id || null
  rubricForm.dimension_code = row?.dimension_code || ''
  rubricForm.interface_id = row?.interface_id ?? 0
  rubricForm.version = row?.version || ''
  rubricForm.templateText = row ? JSON.stringify(row.template, null, 2) : '{}'
  rubricVisible.value = true
}

const saveRubric = async () => {
  let template
  try {
    template = JSON.parse(rubricForm.templateText || '{}')
  } catch (e) {
    ElMessage.error('模板不是合法 JSON')
    return
  }
  const payload = {
    dimension_code: rubricForm.dimension_code, interface_id: rubricForm.interface_id,
    version: rubricForm.version, template,
  }
  savingRubric.value = true
  try {
    if (rubricEditingId.value) await updateRubric(rubricEditingId.value, payload)
    else await createRubric(payload)
    ElMessage.success('已保存')
    rubricVisible.value = false
    await loadRubrics()
  } catch (e) {
    // 拦截器已弹
  } finally {
    savingRubric.value = false
  }
}

const handleDeleteRubric = async (row) => {
  try {
    await ElMessageBox.confirm(`确认删除 rubric「${row.dimension_code}/${row.version}」？`, '提示', { type: 'warning' })
    await deleteRubric(row.id)
    await loadRubrics()
  } catch (e) {
    // 取消
  }
}

onMounted(() => {
  loadPrices()
  loadOps()
  loadRubrics()
})
</script>

<style scoped>
.page-header { margin-bottom: 12px; }
.toolbar { display: flex; gap: 8px; margin-bottom: 10px; }
.dim-note { color: #909399; font-size: 12px; margin-top: 4px; }
.tmpl { margin: 0; background: #f5f7fa; padding: 6px; font-size: 12px; max-height: 120px; overflow: auto; }
</style>
