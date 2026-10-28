<script setup>
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import ChartPanel from './components/ChartPanel.vue'
import { chartOptions, colors, describe, number, validateDashboard } from './chartOptions.js'

const data = ref(null)
const loading = ref(true)
const error = ref('')
const fullscreen = ref(false)
const message = ref('')
const summary = computed(() => data.value ? describe(data.value) : null)
const charts = computed(() => data.value ? chartOptions(data.value, window.matchMedia('(prefers-reduced-motion: reduce)').matches) : null)
const formatDate = (value) => value.replaceAll('-', '.')
const counts = computed(() => ['负面', '中性', '正面'].map((label) => ({
  label, count: data.value.sentiment.find((row) => row.sentiment === label).count,
  color: { '负面': colors.rose, '中性': colors.amber, '正面': colors.cyan }[label],
})))

async function load() {
  loading.value = true
  error.value = ''
  try {
    const response = await fetch(`${import.meta.env.BASE_URL}data/dashboard.json`, { cache: 'no-store' })
    if (!response.ok) throw new Error(`数据文件读取失败（HTTP ${response.status}）。`)
    data.value = validateDashboard(await response.json())
  } catch (reason) {
    error.value = `${reason.message || '数据文件读取失败。'}${data.value ? ' 仍显示上次载入的批次。' : ' 请检查数据导出和启动方式。'}`
  } finally { loading.value = false }
}
function syncFullscreen() { fullscreen.value = !!document.fullscreenElement }
async function toggleFullscreen() {
  try {
    if (document.fullscreenElement) await document.exitFullscreen()
    else if (document.documentElement.requestFullscreen) await document.documentElement.requestFullscreen()
    else message.value = '当前浏览器未提供全屏接口，可按 F11 展示。'
  } catch { message.value = '未能进入全屏，可按 F11 展示。' }
}
onMounted(() => { load(); document.addEventListener('fullscreenchange', syncFullscreen) })
onBeforeUnmount(() => document.removeEventListener('fullscreenchange', syncFullscreen))
</script>

<template>
  <main class="dashboard">
    <header class="masthead">
      <div class="brand">
        <svg class="brand-mark" viewBox="0 0 40 40" aria-hidden="true"><rect x="1" y="1" width="38" height="38" rx="11"/><path d="M11 27V19m9 8V12m9 15V8"/></svg>
        <div><h1>聊天数据分析</h1><p>客服会话的时间分布、话题与情感观察</p></div>
      </div>
      <div class="header-actions">
        <details class="data-info">
          <summary>数据说明</summary>
          <div class="data-popover" v-if="data">
            <h2>当前数据口径</h2>
            <p>{{ data.batch.sourceLabel }}，不包含公众号新增消息。</p>
            <p>原始 {{ number(data.batch.rawRows) }} 条，排除 {{ number(data.batch.excludedRows) }} 条，保留 {{ number(data.batch.retainedRows) }} 条。</p>
            <p>小时图为整个期间累计；词频统计出现次数；主题为混合模型组。情感标签未作全量人工校准，不代表投诉率或满意度。</p>
            <p class="batch-code">批次 {{ data.batch.id }}</p>
            <p class="batch-code">导出 {{ data.batch.generatedAt }}</p>
          </div>
          <div class="data-popover" v-else>数据载入后可查看批次说明。</div>
        </details>
        <button class="quiet-button" :disabled="loading" @click="load">{{ loading ? '读取中…' : '重新读取' }}</button>
        <button class="fullscreen-button" @click="toggleFullscreen">
          <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M7 3H3v4m10-4h4v4M3 13v4h4m10-4v4h-4"/></svg>
          {{ fullscreen ? '退出全屏' : '全屏展示' }}
        </button>
      </div>
    </header>

    <p v-if="error" class="status-message error-message" role="alert">{{ error }}</p>
    <p v-if="message" class="status-message" role="status">{{ message }}</p>
    <div v-if="!data" class="empty-state" role="status">
      <span class="loading-line" v-if="loading"></span>
      <h2>{{ loading ? '正在读取分析数据' : '数据尚未载入' }}</h2>
      <p>{{ loading ? '加载月度、小时、词频、主题与情感统计。' : '请检查数据文件，然后点击“重新读取”。' }}</p>
    </div>

    <template v-if="data">
      <section class="overview" aria-label="批次概览">
        <div class="main-metric"><span class="metric-label">有效聊天记录 <span class="unit">条</span></span><strong>{{ number(data.batch.retainedRows) }}</strong><span class="metric-note">课程预置数据</span></div>
        <div class="metric"><span class="metric-label">分析区间</span><strong class="date-value">{{ formatDate(data.batch.periodStart) }} <span>—</span> {{ formatDate(data.batch.periodEnd) }}</strong><span class="metric-note">{{ data.monthly.length }}个月<span v-if="data.batch.lastMonthPartial">，末月未满自然月</span></span></div>
        <div class="metric"><span class="metric-label">最活跃时段</span><strong>{{ String(summary.peak.hour).padStart(2, '0') }}<span class="time-unit">:00</span></strong><span class="metric-note">累计 {{ number(summary.peak.count) }} 条</span></div>
        <div class="metric"><span class="metric-label">主题分组</span><strong>3<span class="time-unit">组</span></strong><span class="metric-note">保留原模型混合分组</span></div>
      </section>

      <div class="chart-grid">
        <ChartPanel class="month-panel" chart-id="monthly" title="月度聊天量" :description="`${data.monthly.length}个月累计`" :option="charts.monthly" :accessible-text="`月度聊天量共${data.monthly.length}个月，合计${data.batch.retainedRows}条`">
          峰值月份 <b>{{ summary.peakMonth.month }}</b><span>{{ number(summary.peakMonth.count) }} 条</span>
        </ChartPanel>
        <ChartPanel class="hour-panel" chart-id="hourly" title="24小时聊天密度" description="全期间累计" :option="charts.hourly" :accessible-text="`0至23时聊天量，最高为${summary.peak.hour}时，共${summary.peak.count}条`">
          横轴为小时<span>高亮为最活跃时段</span>
        </ChartPanel>
        <ChartPanel class="word-panel" chart-id="words" title="大家在聊什么" description="高频词 TOP100" :option="charts.words" accessible-text="聊天词语出现频次前100名，悬停查看次数">
          <div class="word-total">高频词出现 <b>{{ number(summary.wordTotal) }}</b> 次</div>
          <p>词频为出现次数，同一条聊天可包含多个词。</p>
        </ChartPanel>
        <ChartPanel class="topic-panel" chart-id="topics" title="月度主题趋势" description="三组混合主题" :option="charts.topics" accessible-text="主题0、1、2的月度堆叠面积趋势，可点击图例切换显示">
          <div class="topic-notes"><p v-for="topic in data.topicLabels" :key="topic.id"><i :style="{ background: [colors.cyan, colors.blue, colors.amber][topic.id] }"></i><b>{{ topic.id }}</b> {{ topic.label }}</p></div>
        </ChartPanel>
        <ChartPanel class="sentiment-panel" chart-id="sentiment" title="情感标签分布" description="SnowNLP 模型" :option="charts.sentiment" accessible-text="情感玫瑰图，显示模型负面、中性及正面标签计数">
          <div class="sentiment-counts"><div v-for="item in counts" :key="item.label"><span><i :style="{ background: item.color }"></i>{{ item.label }}</span><b>{{ number(item.count) }}</b></div></div>
        </ChartPanel>
      </div>

      <footer class="dashboard-footer">
        <span class="batch-state"><i></i>已载入批次 <b>{{ data.batch.id }}</b></span>
        <span>模型分组与情感标签仅作分析参考，不代表准确业务分类、投诉率或满意度。</span>
      </footer>
    </template>
  </main>
</template>
