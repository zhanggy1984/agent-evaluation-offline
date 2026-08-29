<template>
  <div class="pwd-wrap">
    <el-card class="pwd-card">
      <h2 class="pwd-title">修改密码</h2>
      <el-form ref="formRef" :model="form" :rules="rules">
        <el-form-item prop="old_password">
          <el-input
            v-model="form.old_password"
            type="password"
            placeholder="原密码"
            show-password
          />
        </el-form-item>
        <el-form-item prop="new_password">
          <el-input
            v-model="form.new_password"
            type="password"
            placeholder="新密码（至少 8 位）"
            show-password
          />
        </el-form-item>
        <el-form-item prop="confirm">
          <el-input
            v-model="form.confirm"
            type="password"
            placeholder="确认新密码"
            show-password
          />
        </el-form-item>
        <el-button type="primary" class="pwd-btn" :loading="loading" @click="handleSubmit">
          提交
        </el-button>
        <!-- P2-D11：强制改密用户无「取消」语义——守卫会把非改密页弹回，按钮是虚假期望，
             故隐藏；非强制用户（MainLayout 顶部入口主动改密）保留取消回首页 -->
        <el-button v-if="!auth.must_change_password" class="pwd-btn" @click="goBack">取消</el-button>
      </el-form>
    </el-card>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const auth = useAuthStore()

const formRef = ref()
const loading = ref(false)
const form = reactive({ old_password: '', new_password: '', confirm: '' })
const rules = {
  old_password: [{ required: true, message: '请输入原密码', trigger: 'blur' }],
  new_password: [
    { required: true, message: '请输入新密码', trigger: 'blur' },
    { min: 8, message: '新密码至少 8 位', trigger: 'blur' },
  ],
  confirm: [
    { required: true, message: '请确认新密码', trigger: 'blur' },
    {
      validator: (rule, value, callback) =>
        value === form.new_password ? callback() : callback(new Error('两次输入的密码不一致')),
      trigger: 'blur',
    },
  ],
}

const handleSubmit = async () => {
  await formRef.value.validate()
  loading.value = true
  try {
    // 改密成功 → 后端全族 refresh 撤销 → store 清态，强制重登
    await auth.changePassword(form.old_password, form.new_password)
    ElMessage.success('密码已修改，请重新登录')
    router.push('/login')
  } catch (e) {
    // 错误提示已由拦截器弹出
  } finally {
    loading.value = false
  }
}

const goBack = () => router.push('/')
</script>

<style scoped>
.pwd-wrap {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #1f2d3d 0%, #2c3e50 100%);
}
.pwd-card {
  width: 380px;
}
.pwd-title {
  text-align: center;
  margin: 0 0 24px;
}
.pwd-btn {
  width: 100%;
  margin-left: 0 !important;
  margin-top: 4px;
}
</style>
