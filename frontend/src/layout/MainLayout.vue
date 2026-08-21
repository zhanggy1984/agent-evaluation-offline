<template>
  <el-container class="layout">
    <el-aside width="200px" class="aside">
      <div class="logo">AI Agent 评测</div>
      <el-menu
        :default-active="$route.path"
        router
        background-color="#001529"
        text-color="rgba(255,255,255,0.65)"
        active-text-color="#ffffff"
      >
        <el-menu-item index="/dashboard">看板</el-menu-item>
        <el-menu-item index="/config">配置中心</el-menu-item>
        <el-menu-item index="/agents">Agent 管理</el-menu-item>
        <el-menu-item index="/cases">用例管理</el-menu-item>
        <el-menu-item v-if="isAdmin" index="/users">用户管理</el-menu-item>
      </el-menu>
    </el-aside>
    <el-container>
      <el-header class="header">
        <div class="header-title">AI Agent 评测系统</div>
        <div class="header-user">
          <span>{{ username }}（{{ roleLabel }}）</span>
          <el-button link type="primary" @click="goChangePassword">修改密码</el-button>
          <el-button link type="danger" @click="handleLogout">退出登录</el-button>
        </div>
      </el-header>
      <el-main class="main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import { ElMessageBox } from 'element-plus'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const auth = useAuthStore()
const { username, role, isAdmin } = storeToRefs(auth)
const isStaff = computed(() => ['admin', 'evaluator'].includes(auth.role))

const ROLE_LABEL = { admin: '管理员', evaluator: '评测员', viewer: '观察者' }
const roleLabel = computed(() => ROLE_LABEL[role.value] || role.value)

const goChangePassword = () => router.push('/change-password')

const handleLogout = async () => {
  await ElMessageBox.confirm('确认退出登录？', '提示', { type: 'warning' })
  await auth.logout()
  router.push('/login')
}
</script>

<style scoped>
.layout {
  height: 100%;
}
.aside {
  background-color: #001529;
}
.logo {
  height: 60px;
  line-height: 60px;
  text-align: center;
  color: #fff;
  font-size: 16px;
  font-weight: 600;
}
.aside :deep(.el-menu) {
  border-right: none;
}
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background-color: #fff;
  border-bottom: 1px solid #e4e7ed;
}
.header-title {
  font-size: 16px;
  font-weight: 600;
}
.header-user {
  display: flex;
  align-items: center;
  gap: 8px;
}
.main {
  overflow-y: auto;
}
</style>
