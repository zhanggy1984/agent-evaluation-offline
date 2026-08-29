<template>
  <div class="nf-wrap">
    <h1 class="nf-code">404</h1>
    <p class="nf-text">页面不存在或已被移除</p>
    <el-button type="primary" @click="goHome">返回首页</el-button>
  </div>
</template>

<script setup>
import { useRouter } from 'vue-router'

const router = useRouter()
const goHome = () => router.push('/')
</script>

<style scoped>
.nf-wrap {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding-top: 80px;
  gap: 12px;
}
.nf-code {
  font-size: 72px;
  margin: 0;
  color: #909399;
}
.nf-text {
  color: #606266;
  margin: 0 0 8px;
}
</style>
