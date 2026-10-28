<script setup>
import { onMounted, onBeforeUnmount, ref, watch } from 'vue'
import * as echarts from 'echarts'
import 'echarts-wordcloud'

const props = defineProps({
  title: String, description: String, chartId: String, accessibleText: String,
  option: { type: Object, required: true },
})
const host = ref(null)
let chart
let observer
function draw(option) {
  chart?.setOption({ ...option, aria: { enabled: true, label: { description: props.accessibleText } } }, { notMerge: true })
}
onMounted(() => {
  chart = echarts.init(host.value)
  draw(props.option)
  observer = new ResizeObserver(() => chart?.resize())
  observer.observe(host.value)
})
watch(() => props.option, draw)
onBeforeUnmount(() => { observer?.disconnect(); chart?.dispose(); chart = null })
</script>

<template>
  <section class="chart-panel" :aria-labelledby="`${chartId}-title`" :data-chart="chartId">
    <header class="panel-heading">
      <h2 :id="`${chartId}-title`">{{ title }}</h2>
      <span>{{ description }}</span>
    </header>
    <div ref="host" class="chart-canvas" role="img" :aria-label="accessibleText"></div>
    <div v-if="$slots.default" class="panel-caption"><slot /></div>
  </section>
</template>
