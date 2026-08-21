<template>
  <div>
    <el-page-header title="用户管理" class="page-header" />

    <div class="toolbar">
      <el-select
        v-model="query.role"
        placeholder="按角色筛选"
        clearable
        style="width: 160px"
        @change="reload"
      >
        <el-option v-for="r in ROLES" :key="r.value" :label="r.label" :value="r.value" />
      </el-select>
      <el-button type="primary" @click="openCreate">新建用户</el-button>
    </div>

    <el-table :data="users" size="small" border v-loading="loading">
      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column prop="username" label="用户名" min-width="140" />
      <el-table-column label="角色" width="140">
        <template #default="{ row }">
          <el-select
            :model-value="row.role"
            size="small"
            :disabled="row.id === myId"
            @change="(v) => updateRole(row, v)"
          >
            <el-option v-for="r in ROLES" :key="r.value" :label="r.label" :value="r.value" />
          </el-select>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-switch
            :model-value="row.enabled"
            :disabled="row.id === myId"
            @change="(v) => toggleEnabled(row, v)"
          />
        </template>
      </el-table-column>
      <el-table-column label="操作" width="180">
        <template #default="{ row }">
          <el-button link type="primary" @click="openResetPwd(row)">重置密码</el-button>
          <el-button link type="danger" :disabled="row.id === myId" @click="handleDisable(row)">
            禁用
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination
      v-model:current-page="query.page"
      :page-size="query.page_size"
      :total="total"
      layout="total, prev, pager, next"
      class="pager"
      @current-change="load"
    />

    <!-- 新建用户 -->
    <el-dialog v-model="createVisible" title="新建用户" width="420px">
      <el-form ref="createRef" :model="createForm" :rules="createRules" label-width="70px">
        <el-form-item label="用户名" prop="username">
          <el-input v-model="createForm.username" />
        </el-form-item>
        <el-form-item label="密码" prop="password">
          <el-input v-model="createForm.password" type="password" show-password />
        </el-form-item>
        <el-form-item label="角色" prop="role">
          <el-select v-model="createForm.role" style="width: 100%">
            <el-option v-for="r in ROLES" :key="r.value" :label="r.label" :value="r.value" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="handleCreate">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listUsers, createUser, updateUser, disableUser } from '../api/users'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()

const ROLES = [
  { value: 'admin', label: '管理员' },
  { value: 'evaluator', label: '评测员' },
  { value: 'viewer', label: '观察者' },
]

const users = ref([])
const total = ref(0)
const loading = ref(false)
const myId = ref(null)
const query = reactive({ page: 1, page_size: 20, role: undefined })

const load = async () => {
  loading.value = true
  try {
    const data = await listUsers({
      page: query.page,
      page_size: query.page_size,
      role: query.role || undefined,
    })
    users.value = data.items
    total.value = data.total
  } catch (e) {
    // 拦截器已弹
  } finally {
    loading.value = false
  }
}

const reload = () => {
  query.page = 1
  load()
}

const updateRole = async (row, role) => {
  await updateUser(row.id, { role })
  ElMessage.success('角色已更新')
  await load()
}

const toggleEnabled = async (row, enabled) => {
  await updateUser(row.id, { enabled })
  ElMessage.success(enabled ? '已启用' : '已禁用')
  await load()
}

const handleDisable = async (row) => {
  await ElMessageBox.confirm(`确认禁用用户「${row.username}」？禁用后不可登录。`, '提示', {
    type: 'warning',
  })
  await disableUser(row.id)
  ElMessage.success('已禁用')
  await load()
}

const createVisible = ref(false)
const creating = ref(false)
const createRef = ref()
const createForm = reactive({ username: '', password: '', role: 'viewer' })
const createRules = {
  username: [
    { required: true, min: 2, message: '用户名至少 2 位', trigger: 'blur' },
  ],
  password: [
    { required: true, min: 8, message: '密码至少 8 位', trigger: 'blur' },
  ],
  role: [{ required: true, message: '请选择角色', trigger: 'change' }],
}

const openCreate = () => {
  createForm.username = ''
  createForm.password = ''
  createForm.role = 'viewer'
  createVisible.value = true
}

const handleCreate = async () => {
  await createRef.value.validate()
  creating.value = true
  try {
    await createUser({ ...createForm })
    ElMessage.success('用户已创建（首次登录需修改密码）')
    createVisible.value = false
    await load()
  } catch (e) {
    // 拦截器已弹
  } finally {
    creating.value = false
  }
}

const openResetPwd = async (row) => {
  const { value } = await ElMessageBox.prompt(
    `为「${row.username}」设置新密码（至少 8 位）：`,
    '重置密码',
    {
      inputType: 'password',
      inputValidator: (v) => (v && v.length >= 8) || '密码至少 8 位',
    }
  )
  await updateUser(row.id, { password: value })
  ElMessage.success('密码已重置')
}

onMounted(async () => {
  // 记录当前登录用户 id（保护：禁止操作自身），需先拉 /me
  try {
    const me = await auth.fetchMe()
    myId.value = me.id
  } catch (e) {
    // 未登录状态由路由守卫兜底
  }
  await load()
})
</script>

<style scoped>
.page-header {
  margin-bottom: 12px;
}
.toolbar {
  margin-bottom: 12px;
  display: flex;
  gap: 12px;
}
.pager {
  margin-top: 12px;
  justify-content: flex-end;
}
</style>
