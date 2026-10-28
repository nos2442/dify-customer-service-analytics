export const colors = {
  cyan: '#72d5df', blue: '#8baaf3', amber: '#e8c17e',
  rose: '#e48d9c', text: '#edf2fa', muted: '#95a8c2', grid: '#27364b',
}
export const number = (value) => new Intl.NumberFormat('zh-CN').format(value)

// The display consumes only validated aggregate exports, never raw chat records.
export function validateDashboard(data) {
  const isCount = (v) => Number.isSafeInteger(v) && v >= 0
  const date = (v) => typeof v === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(v) &&
    Number.isFinite(Date.parse(v)) && new Date(v).toISOString().slice(0, 10) === v
  const batch = data?.batch
  if (data?.schemaVersion !== 1 || !batch || !isCount(batch.retainedRows) || !batch.retainedRows ||
      !isCount(batch.rawRows) || !isCount(batch.excludedRows) || batch.rawRows !== batch.retainedRows + batch.excludedRows ||
      !date(batch.periodStart) || !date(batch.periodEnd) || batch.periodStart > batch.periodEnd ||
      typeof batch.id !== 'string' || typeof batch.sourceLabel !== 'string' || batch.sourceMode !== 'preset') {
    throw new Error('数据版本或批次信息不完整，请重新导出数据。')
  }
  const groups = ['monthly', 'hourly', 'words', 'topics', 'sentiment']
  for (const key of groups) {
    if (!Array.isArray(data[key]) || !data[key].length || data[key].some((r) => !r || !isCount(r.count))) {
      throw new Error('图表数据缺失或计数无效，请重新导出数据。')
    }
    if (key !== 'words' && data[key].reduce((sum, r) => sum + r.count, 0) !== data.batch.retainedRows) {
      throw new Error('图表合计与批次条数不一致，请检查数据来源。')
    }
  }
  if (data.hourly.length !== 24 || data.hourly.some((r, i) => r.hour !== i)) {
    throw new Error('小时数据应按0至23时完整排列。')
  }
  const expectedMonths = []
  const end = batch.periodEnd.slice(0, 7)
  for (let current = batch.periodStart.slice(0, 7); current <= end;) {
    expectedMonths.push(current)
    const [year, month] = current.split('-').map(Number)
    current = `${month === 12 ? year + 1 : year}-${String(month === 12 ? 1 : month + 1).padStart(2, '0')}`
  }
  if (data.monthly.length !== expectedMonths.length || data.monthly.some((r, i) => r.month !== expectedMonths[i])) {
    throw new Error('月份数据缺失、重复或顺序不正确。')
  }
  if (data.topics.length !== expectedMonths.length * 3 ||
      new Set(data.topics.map((r) => `${r.month}:${r.topic}`)).size !== data.topics.length ||
      data.topics.some((r) => !expectedMonths.includes(r.month) || ![0, 1, 2].includes(r.topic)) ||
      data.monthly.some((m) => data.topics.filter((r) => r.month === m.month).reduce((sum, r) => sum + r.count, 0) !== m.count)) {
    throw new Error('主题分组不完整、重复或月度小计不一致。')
  }
  if (data.sentiment.length !== 3 || new Set(data.sentiment.map((r) => r.sentiment)).size !== 3 ||
      data.sentiment.some((r) => !['负面', '中性', '正面'].includes(r.sentiment)) ||
      !Array.isArray(data.topicLabels) || data.topicLabels.length !== 3 ||
      new Set(data.topicLabels.map((r) => r?.id)).size !== 3 ||
      data.topicLabels.some((r) => !r || ![0, 1, 2].includes(r.id) || typeof r.label !== 'string' || !r.label.trim())) {
    throw new Error('主题或情感标签缺失，请重新导出数据。')
  }
  if (data.words.length !== 100 || new Set(data.words.map((r) => r.word)).size !== 100 ||
      data.words.some((r) => typeof r.word !== 'string' || !r.word.trim() || r.count === 0)) {
    throw new Error('高频词数据不完整或存在重复词。')
  }
  return data
}

export function describe(data) {
  const peak = data.hourly.reduce((best, row) => row.count > best.count ? row : best)
  const peakMonth = data.monthly.reduce((best, row) => row.count > best.count ? row : best)
  return { peak, peakMonth, wordTotal: data.words.reduce((sum, row) => sum + row.count, 0) }
}

const chartBase = (reduceMotion) => ({
  backgroundColor: 'transparent',
  animation: !reduceMotion,
  animationDuration: 450,
  textStyle: { fontFamily: 'Microsoft YaHei, PingFang SC, sans-serif', color: colors.muted },
  aria: { enabled: true },
  tooltip: {
    trigger: 'axis', renderMode: 'richText', confine: true,
    backgroundColor: '#21334c', borderColor: '#3a506c',
    textStyle: { color: colors.text, fontSize: 12 },
    axisPointer: { type: 'line', lineStyle: { color: '#68849f', type: 'dashed' } },
  },
})

const axis = {
  axisLine: { show: false }, axisTick: { show: false },
  axisLabel: { color: colors.muted, fontSize: 11, margin: 12 },
}
const valueAxis = { ...axis, type: 'value', splitLine: { lineStyle: { color: colors.grid, type: 'dashed' } } }

export function chartOptions(data, reduceMotion = false) {
  const base = chartBase(reduceMotion)
  const grid = { top: 24, left: 49, right: 18, bottom: 34 }
  const crossYear = new Set(data.monthly.map((r) => r.month.slice(0, 4))).size > 1
  const monthLabels = data.monthly.map((r) => crossYear ? r.month : r.month.slice(5) + '月')
  const monthAxis = { ...axis, type: 'category', boundaryGap: false, data: monthLabels,
    axisLabel: { ...axis.axisLabel, rotate: crossYear ? 25 : 0 } }
  const monthly = {
    ...base, grid,
    xAxis: monthAxis,
    yAxis: { ...valueAxis, min: 0 },
    series: [{
      name: '聊天量', type: 'line', data: data.monthly.map((r) => r.count),
      smooth: false, symbol: 'circle', symbolSize: 6,
      lineStyle: { width: 2.5, color: colors.cyan }, itemStyle: { color: colors.cyan },
      areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [
        { offset: 0, color: '#72d5df55' }, { offset: 1, color: '#72d5df02' },
      ] } },
    }],
    tooltip: { ...base.tooltip, formatter: (p) => `${data.monthly[p[0].dataIndex].month}\n聊天量  ${number(p[0].value)} 条` },
  }
  const hourly = {
    ...base, grid,
    xAxis: { ...axis, type: 'category', data: data.hourly.map((r) => String(r.hour)), axisLabel: { ...axis.axisLabel, interval: 3 } },
    yAxis: valueAxis,
    series: [{ name: '聊天量', type: 'bar', barMaxWidth: 13, data: data.hourly.map((r) => ({
      value: r.count, itemStyle: { color: r.count === describe(data).peak.count ? colors.amber : '#638dcc', borderRadius: [3, 3, 0, 0] },
    })) }],
    tooltip: { ...base.tooltip, formatter: (p) => `${p[0].axisValue}:00—${p[0].axisValue}:59\n聊天量  ${number(p[0].value)} 条` },
  }
  const topicPalette = [colors.cyan, colors.blue, colors.amber]
  const topics = {
    ...base, grid: { ...grid, top: 35 },
    legend: { top: 0, right: 9, itemWidth: 9, itemHeight: 9, textStyle: { color: colors.muted, fontSize: 11 } },
    color: topicPalette,
    xAxis: monthAxis,
    yAxis: valueAxis,
    series: [0, 1, 2].map((id) => ({
      name: `主题${id}`, type: 'line', stack: 'topic', symbol: 'none', smooth: false,
      lineStyle: { width: 1.6 }, areaStyle: { opacity: .28 },
      data: data.monthly.map((month) => data.topics.find((r) => r.month === month.month && r.topic === id)?.count ?? 0),
    })),
    tooltip: { ...base.tooltip, formatter: (p) => `${data.monthly[p[0].dataIndex].month}\n${p.map((r) => `${r.seriesName}  ${number(r.value)} 条`).join('\n')}` },
  }
  const sentimentColors = { '负面': colors.rose, '中性': colors.amber, '正面': colors.cyan }
  const sentiment = {
    ...base,
    tooltip: { ...base.tooltip, trigger: 'item', formatter: (p) => `${p.name}模型标签\n${number(p.data.count)} 条  ${(p.data.count / data.batch.retainedRows * 100).toFixed(2)}%` },
    series: [{
      name: '情感模型标签', type: 'pie', roseType: 'area', radius: [0, '82%'], center: ['50%', '50%'],
      label: { show: false }, labelLine: { show: false },
      itemStyle: { borderColor: '#142137', borderWidth: 2, borderRadius: 3 },
      emphasis: { scaleSize: 5 },
      data: ['负面', '中性', '正面'].map((name) => {
        const count = data.sentiment.find((r) => r.sentiment === name).count
        // Equal-angle rose radius is sqrt(count), so sector AREA represents count.
        // Keep the untransformed count for tooltips, accessible text and totals.
        return { name, count, value: Math.sqrt(count), itemStyle: { color: sentimentColors[name] } }
      }),
    }],
  }
  const wordPalette = [colors.cyan, '#b7d4ec', '#8baaf3', '#e8c17e', '#a4c8d2', '#5b99ae']
  const words = {
    ...base,
    tooltip: { ...base.tooltip, trigger: 'item', formatter: (p) => `${p.name}\n出现 ${number(p.value)} 次` },
    series: [{
      type: 'wordCloud', shape: 'circle', left: '3%', top: '3%', width: '94%', height: '94%',
      sizeRange: [12, 48], rotationRange: [0, 0], gridSize: 8,
      drawOutOfBound: false, shrinkToFit: true, layoutAnimation: false,
      textStyle: { fontFamily: 'Microsoft YaHei, sans-serif', fontWeight: 500 },
      emphasis: { textStyle: { color: '#ffffff' } },
      data: data.words.map((r, i) => ({ name: r.word, value: r.count, textStyle: { color: wordPalette[i % wordPalette.length] } })),
    }],
  }
  return { monthly, hourly, topics, sentiment, words }
}
