const test = require('node:test');
const assert = require('node:assert/strict');
const model = require('../../static/js/model.js');

test('零值可以显示，缺失值显示破折号，小数卷数保留', () => {
  assert.equal(model.formatNumber(0), '0');
  assert.equal(model.formatNumber(null), '—');
  assert.equal(model.formatNumber(0.5), '0.5');
});

test('不同库存单位分别展示', () => {
  assert.equal(model.inventorySummary([{ quantity: 0.5, unit: '卷' }, { quantity: 10, unit: '张' }]), '0.5 卷 / 10 张');
});

test('趋势图保留未录入空值并正确呈现零产量', () => {
  const option = model.buildTrendOption({ dates: ['2026-11-20', '2026-11-21'], totals: [null, 10], series: [{ name: 'A线', data: [null, 0] }, { name: 'B线', data: [null, 10] }] }, true);
  assert.deepEqual(option.series[0].data, [null, 10]);
  assert.deepEqual(option.series[1].data, [null, 0]);
  assert.equal(option.tooltip.renderMode, 'richText');
});

test('出货图使用传入件号及数量，不修改原明细', () => {
  const data = [{ part_no: 'P-1', quantity: 3200 }, { part_no: 'P-2', quantity: 0 }];
  const option = model.buildShipmentOption(data);
  assert.deepEqual(option.yAxis.data, ['P-1', 'P-2']);
  assert.deepEqual(option.series[0].data, [3200, 0]);
  assert.equal(data.length, 2);
});

test('四条产线颜色固定且选择变化或改名后不改变', () => {
  const trend = { dates: ['2026-11-21'], totals: [1000], series: [1, 2, 3, 4].map(line_id => ({ line_id, name: `产线${line_id}`, data: [line_id * 100] })) };
  const all = model.buildTrendOption(trend);
  assert.equal(new Set(all.series.slice(1).map(series => series.itemStyle.color)).size, 4);
  assert.equal(all.series[0].name, '所选产线合计');
  assert.equal(all.series[3].itemStyle.color, '#32c3f2');
  assert.equal(all.series[4].itemStyle.color, '#ff9d23');
  const chosen = model.buildTrendOption({ ...trend, totals: [200], series: [{ line_id: 2, name: '改名后的产线', data: [200] }] });
  assert.equal(chosen.series[1].itemStyle.color, all.series[2].itemStyle.color);
  assert.equal(chosen.series[1].itemStyle.color, model.trendColor(2));
  assert.equal(chosen.series[1].id, 'line-2');
});
