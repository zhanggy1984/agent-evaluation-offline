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

const resize = () => chart && chart.resize()

onMounted(() => {
  chart = echarts.init(el.value)
  chart.setOption(props.option)
  window.addEventListener('resize', resize)
  // P2-D16：隐藏 tab（display:none）中 init 的图容器宽度为 0，ECharts fallback 100px → 图挤在角落。
  // ResizeObserver 监听容器尺寸变化（tab 切回可见时 0→真实宽），自动 resize 修正。
  if (typeof ResizeObserver !== 'undefined') {
    ro = new ResizeObserver(() => chart && chart.resize())
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
