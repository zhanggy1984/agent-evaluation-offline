<template>
  <div ref="el" class="echart" :style="{ height }"></div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'

const props = defineProps({
  option: { type: Object, required: true },
  height: { type: String, default: '300px' },
})

const el = ref(null)
let chart = null
let ro = null

// 容器可见且宽>0 才 resize：隐藏容器（display:none）中 ECharts init 读宽为 0 → fallback 100px，
// 对 0 宽容器 resize 无意义；tab 切回可见（0→真实宽）后这里放行，配合 rAF/RO 自动纠正
const resize = () => {
  if (el.value && el.value.clientWidth > 0) chart && chart.resize()
}

onMounted(() => {
  chart = echarts.init(el.value)
  chart.setOption(props.option)
  window.addEventListener('resize', resize)
  // P2-D16：隐藏 tab（display:none）中 init 的图容器宽度为 0，ECharts fallback 100px → 图挤在角落。
  // rAF 兜底：挂载后下一帧容器布局必然完成，此刻若已可见则强制 resize 一次纠正；仍隐藏则守卫跳过
  if (typeof requestAnimationFrame !== 'undefined') requestAnimationFrame(resize)
  // ResizeObserver 监听容器尺寸变化（tab 切回可见时 0→真实宽），自动 resize 修正
  if (typeof ResizeObserver !== 'undefined') {
    ro = new ResizeObserver(resize)
    ro.observe(el.value)
  }
})

watch(
  () => props.option,
  (opt) => chart && chart.setOption(opt, true),
  { deep: true }
)

onBeforeUnmount(() => {
  if (ro) ro.disconnect()
  window.removeEventListener('resize', resize)
  chart && chart.dispose()
  chart = null
})
</script>

<style scoped>
.echart {
  width: 100%;
}
</style>
