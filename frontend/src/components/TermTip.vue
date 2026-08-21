<template>
  <el-tooltip :content="tip" placement="top" effect="dark" :show-after="200">
    <span class="term-tip">{{ label }}</span>
  </el-tooltip>
</template>

<script setup>
import { computed } from 'vue'
import { TERMS } from '../constants/terms'

const props = defineProps({
  term: { type: String, required: true },
  label: { type: String, default: '' },
})

const tip = computed(() => TERMS[props.term]?.desc || props.term)
const label = computed(() => props.label || TERMS[props.term]?.name || props.term)
</script>

<style scoped>
.term-tip {
  cursor: help;
  border-bottom: 1px dashed #909399;
}
</style>
