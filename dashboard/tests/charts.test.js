import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { chartOptions, describe, validateDashboard } from '../src/chartOptions.js'

const data = JSON.parse(readFileSync(new URL('../public/data/dashboard.json', import.meta.url)))

test('the five rendered series reconcile with the exported 42,860-record batch', () => {
  validateDashboard(data)
  const options = chartOptions(data, true)
  const total = (values) => values.reduce((sum, row) => sum + (row.value ?? row), 0)
  assert.equal(data.batch.retainedRows, 42860)
  assert.equal(options.monthly.series[0].data.length, 7)
  assert.equal(total(options.monthly.series[0].data), 42860)
  assert.equal(options.hourly.series[0].data.length, 24)
  assert.equal(total(options.hourly.series[0].data), 42860)
  assert.equal(options.topics.series.length, 3)
  assert.equal(total(options.topics.series.flatMap((s) => s.data)), 42860)
  assert.deepEqual(options.sentiment.series[0].data.map((r) => r.count), [39046, 1466, 2348])
  for (const row of options.sentiment.series[0].data) assert.ok(Math.abs(row.value ** 2 - row.count) < .00001)
  assert.match(options.sentiment.tooltip.formatter({ name: '负面', data: { count: 39046 } }), /39,046 条  91.10%/)
  assert.equal(options.sentiment.series[0].roseType, 'area')
  assert.equal(options.words.series[0].data.length, 100)
  assert.equal(options.words.series[0].data[0].name, '吗')
  assert.equal(options.monthly.animation, false)
  assert.equal(describe(data).wordTotal, 322628)
})

test('rejects inconsistent data instead of presenting a healthy dashboard', () => {
  const broken = structuredClone(data)
  broken.monthly[0].count += 1
  assert.throws(() => validateDashboard(broken), /合计/)
  const missingHour = structuredClone(data)
  missingHour.hourly[0].hour = 23
  assert.throws(() => validateDashboard(missingHour), /小时数据/)
  const invalid = structuredClone(data)
  invalid.sentiment[0].count = -1
  assert.throws(() => validateDashboard(invalid), /计数无效/)
})

test('rejects missing render fields and duplicate topic keys before updating the page', () => {
  const changes = [
    (d) => { delete d.batch.periodStart },
    (d) => { delete d.monthly[0].month },
    (d) => { d.sentiment[0].sentiment = 'unknown' },
    (d) => { d.topics[1].topic = d.topics[0].topic },
    (d) => { d.topicLabels = [] },
    (d) => { d.words[1].word = d.words[0].word },
  ]
  for (const change of changes) {
    const broken = structuredClone(data)
    change(broken)
    assert.throws(() => validateDashboard(broken))
  }
})
