<template>
  <div>
    <el-page-header :content="isAdmin ? '可编辑热生效项' : '只读'" class="page-header">
      <template #title>配置中心</template>
    </el-page-header>

    <el-tabs v-model="activeScope">
      <el-tab-pane
        v-for="(group, scope) in grouped"
        :key="scope"
        :label="scopeLabel(scope)"
        :name="scope"
      >
        <el-table :data="group" size="small" border>
          <el-table-column prop="key" label="配置项" min-width="200" />
          <el-table-column label="说明" min-width="300">
            <template #default="{ row }">
              <div v-if="row._meta">
                <div class="cfg-label">{{ row._meta.label }}<span v-if="row._meta.unit" class="cfg-unit">（{{ row._meta.unit }}）</span></div>
                <div class="cfg-desc">{{ row._meta.desc }}</div>
              </div>
              <span v-else class="dim-note">{{ row.key }}</span>
            </template>
          </el-table-column>
          <el-table-column label="值" min-width="240">
            <template #default="{ row }">
              <el-switch
                v-if="row._type === 'bool'"
                v-model="row.value"
                :disabled="!editable(row)"
              />
              <el-input-number
                v-else-if="row._type === 'number'"
                v-model="row.value"
                :disabled="!editable(row)"
                :controls="false"
                :precision="row._meta?.precision ?? 0"
                style="width: 140px"
              />
              <span v-if="row._type === 'number' && row._meta?.unit" class="unit-suffix">{{ row._meta.unit }}</span>
              <el-input
                v-else-if="row._type === 'text'"
                v-model="row.value"
                :disabled="!editable(row)"
              />
              <!-- list/dict/null：JSON 文本域编辑，保存时 parse 还原类型；null 显示空占位 -->
              <el-input
                v-else
                :model-value="row._jsonText"
                type="textarea"
                :rows="2"
                :disabled="!editable(row)"
                placeholder="null（使用默认）"
                @update:model-value="(v) => (row._jsonText = v)"
              />
            </template>
          </el-table-column>
          <el-table-column label="热生效" width="90">
            <template #default="{ row }">
              <el-tag :type="row.is_hot ? 'success' : 'info'" size="small">
                {{ row.is_hot ? '是' : '否' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="updated_at" label="更新时间" width="180" />
        </el-table>
      </el-tab-pane>
    </el-tabs>

    <div class="footer-bar">
      <el-button v-if="isAdmin" type="primary" :loading="saving" @click="handleSave">
        保存全部修改
      </el-button>
      <el-button :loading="loading" @click="load">刷新</el-button>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { ElMessage } from 'element-plus'
import { getGlobalConfig, putGlobalConfig } from '../api/config'
import { useAuthStore } from '../stores/auth'
import { CONFIG_META } from '../constants/configMeta'

const auth = useAuthStore()
const { isAdmin } = storeToRefs(auth)

const activeScope = ref('run')
const loading = ref(false)
const saving = ref(false)
const configs = ref([])

const SCOPE_LABEL = { run: '运行期', global: '进程级', registration: '注册期' }
const scopeLabel = (scope) => SCOPE_LABEL[scope] || scope

// 按 scope 分组；同时标记值类型（bool/number/text/json）
const grouped = computed(() => {
  const map = {}
  for (const c of configs.value) {
    const t = typeof c.value
    c._type = t === 'boolean' ? 'bool' : t === 'number' ? 'number' : t === 'string' ? 'text' : 'json'
    if (c._type === 'json' && !c._jsonText) c._jsonText = c.value === null ? '' : JSON.stringify(c.value)
    c._meta = CONFIG_META[c.key] || null
    ;(map[c.scope] ||= []).push(c)
  }
  return map
})

// 可编辑：admin + is_hot + 有契约（CONFIG_META）。无契约残留项（alarm.*/smtp.* 等）
// 后端 PUT /config/global 会 2003 拒绝，置灰避免「改了没保存」的困惑。
const editable = (row) => isAdmin.value && row.is_hot && CONFIG_META[row.key]

const load = async () => {
  loading.value = true
  try {
    configs.value = await getGlobalConfig()
  } catch (e) {
    // 拦截器已弹
  } finally {
    loading.value = false
  }
}

const handleSave = async () => {
  const values = {}
  for (const c of configs.value) {
    // 只收集有契约项：无契约残留项（alarm.*/smtp.* 等）后端无 meta 契约会拒绝全盘保存
    if (!c.is_hot || !CONFIG_META[c.key]) continue
    if (c._type === 'json') {
      // JSON 文本域：空串表示 null（配置默认），否则解析还原类型；非法 JSON 直接中断保存
      try {
        values[c.key] = c._jsonText.trim() === '' ? null : JSON.parse(c._jsonText)
      } catch (e) {
        ElMessage.error(`「${c.key}」的 JSON 值非法，请修正后重试`)
        return
      }
    } else {
      values[c.key] = c.value
    }
  }
  if (!Object.keys(values).length) {
    ElMessage.info('没有可保存的热生效项')
    return
  }
  saving.value = true
  try {
    await putGlobalConfig(values)
    ElMessage.success('配置已保存')
    await load()
  } catch (e) {
    // 拦截器已弹
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.page-header {
  margin-bottom: 12px;
}
.footer-bar {
  margin-top: 12px;
  display: flex;
  gap: 8px;
}
.cfg-label {
  font-size: 13px;
  font-weight: 600;
}
.cfg-unit {
  color: var(--el-text-color-secondary);
  font-weight: 400;
}
.cfg-desc {
  font-size: 12px;
  color: var(--el-text-color-regular);
  margin-top: 2px;
}
.unit-suffix {
  margin-left: 6px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
</style>
