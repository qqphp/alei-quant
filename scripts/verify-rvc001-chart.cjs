// 使用真实行情检查前端副图的颜色、轨道、信号坐标和提示。
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const root = path.resolve(__dirname, '..')
const ts = require(path.join(root, 'frontend/node_modules/typescript'))

function loadSource(file, imports = {}) {
  const source = fs.readFileSync(path.join(root, 'frontend/src', file), 'utf8')
  const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText
  const exports = {}
  new Function('require', 'exports', output)(name => {
    assert.ok(name in imports, `Unexpected import: ${name}`)
    return imports[name]
  }, exports)
  return exports
}

const api = loadSource('api.ts')
const { rvcSeries, rvcTooltip, reportRedExitMode } = loadSource('rvc001.ts', { './api': api })
assert.equal(reportRedExitMode('cash-ledger-0.13.0'), 'pink')
assert.equal(reportRedExitMode('cash-ledger-0.13.1'), 'pink_or_green')
assert.equal(reportRedExitMode('cash-ledger-0.12.1'), 'pink_or_green')
const fixtures = JSON.parse(fs.readFileSync(path.join(root, 'runtime/rvc001-chart-inputs.json'), 'utf8'))
let markers = 0
for (const fixture of fixtures) {
  const bars = fixture.bars, dates = bars.map(bar => bar.date)
  const series = rvcSeries(bars, dates, 2)
  assert.equal(series.length, 5)
  assert.ok(series.every(item => item.xAxisIndex === 1 && item.yAxisIndex === 1))
  const colors = { red: '#ff0000', pink: '#ff66ff', blue: '#0088ff', green: '#00ff00' }
  for (let index = 0; index < bars.length; index++) {
    const value = bars[index].rvc001, candle = series[0].data[index]
    assert.deepEqual(candle.value, [value.open, value.close, value.low, value.high])
    assert.equal(candle.itemStyle.color, colors[value.state])
    assert.equal(candle.itemStyle.color0, colors[value.state])
    assert.equal(candle.itemStyle.borderColorDoji, colors[value.state])
    const points = series[0].markPoint.data.filter(point => point.coord[0] === index)
    const redGreenExit = value.red_exit && value.state === 'green'
    assert.equal(points.length, [value.buy, value.pink_exit, value.green_exit || redGreenExit, value.down_cross].filter(Boolean).length)
    if (value.green_exit && redGreenExit) {
      assert.equal(points.filter(point => point.name.startsWith('绿箭头')).length, 1)
      assert.ok(points.find(point => point.name.startsWith('绿箭头')).name.includes('阿雷红变、阿雷绿变'))
      assert.ok(rvcTooltip(bars[index], dates[index], 2).includes('阿雷红变、阿雷绿变出场'))
    }
    for (const point of points) {
      const isBuy = point.name.startsWith('红箭头')
      assert.equal(point.coord[1], isBuy ? value.low * 0.985 : value.high * 1.015)
      assert.equal(point.date, dates[index])
      assert.equal(point.symbolRotate, isBuy ? 0 : 180)
    }
    if (value.green_exit && value.down_cross) assert.notDeepEqual(points[0].symbolOffset, points[1].symbolOffset)
    const tooltip = rvcTooltip(bars[index], dates[index], 2)
    assert.ok(tooltip.includes(dates[index]) && tooltip.includes('信号价') && tooltip.includes('EMA3'))
    assert.ok(!tooltip.includes('undefined') && !tooltip.includes('NaN'))
  }
  for (const [index, field] of ['up', 'down', 'up1', 'down1'].entries()) {
    assert.deepEqual(series[index + 1].data, bars.map(bar => bar.rvc001[field]))
    assert.equal(series[index + 1].connectNulls, false)
    assert.equal(series[index + 1].itemStyle.color, index < 2 ? '#888888' : '#666666')
  }
  markers += series[0].markPoint.data.length
}
const sample = fixtures[0].bars[0]
const singleExit = { ...sample, rvc001: { ...sample.rvc001, state: 'green', buy: false, red_exit: false,
  pink_exit: false, green_exit: true, down_cross: true } }
assert.ok(rvcSeries([singleExit], ['2024-06-03'], 2)[0].markPoint.data[0].name.includes('阿雷绿变出场'))
assert.ok(!rvcTooltip(singleExit, '2024-06-03', 2).includes('阿雷红变'))
console.log(JSON.stringify({ verified: true, cases: fixtures.length, bars: fixtures.reduce((sum, item) => sum + item.bars.length, 0), markers }))
