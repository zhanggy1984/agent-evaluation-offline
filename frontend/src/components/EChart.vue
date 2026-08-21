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

const resize = () => chart && chart.resize()

onMounted(() => {
  chart = echarts.init(el.value)
  chart.setOption(props.option)
  window.addEventListener('resize', resize)
})

watch(
  () => props.option,
  (opt) => chart && chart.setOption(opt, true),
  { deep: true }
)

onBeforeUnmount(() => {
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
