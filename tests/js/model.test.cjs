const test = require('node:test');
const assert = require('node:assert/strict');
const model = require('../../static/js/model.js');

test('出货排名在件号下显示名称，悬浮提示保留完整名称，空名称不占行', () => {
  const ranking = [{ part_no: 'LP000365', name: '门环左件', quantity: 8200 },
    { part_no: 'LP000366', name: '', quantity: 7300 }];
  const option = model.buildShipmentOption(ranking);
  assert.deepEqual(option.yAxis.data, ['LP000365', 'LP000366']);
  assert.equal(option.yAxis.axisLabel.formatter('LP000365', 0), '01  LP000365\n门环左件');
  assert.equal(option.yAxis.axisLabel.formatter('LP000366', 1), '02  LP000366');
  assert.equal(option.tooltip.formatter([{ name: 'LP000365', dataIndex: 0, value: 8200 }]), 'LP000365\n门环左件\n出货数量：8,200 件');
  assert.equal(option.tooltip.formatter([{ name: 'LP000366', dataIndex: 1, value: 7300 }]), 'LP000366\n出货数量：7,300 件');
  assert.deepEqual(option.series[0].data, [8200, 7300]);
  // 满八项时两行文字必须落在各自的可用高度内。
  const compact = model.buildShipmentOption(Array.from({ length: 8 }, () => ranking[0]), 210);
  assert.ok(compact.yAxis.axisLabel.lineHeight * 2 < (210 - 18) / 8);
});

test('重量以吨显示并按千克保存，保留精度、零值和空白', () => {
  assert.equal(model.weightTonnes(185370), 185.37);
  assert.equal(model.weightKilograms(185.37), 185370);
  for (const kilograms of [0, 0.01, 12.34, 185370.12]) {
    assert.equal(model.weightKilograms(model.weightTonnes(kilograms)), kilograms);
  }
  assert.equal(model.formatNumber(model.weightTonnes(0.01), 5), '0.00001');
  assert.equal(model.weightTonnes(null), null);
  assert.equal(model.weightKilograms(''), null);
});

test('零值可以显示，缺失值显示破折号，小数卷数保留', () => {
  assert.equal(model.formatNumber(0), '0');
  assert.equal(model.formatNumber(null), '—');
  assert.equal(model.formatNumber(0.5), '0.5');
});

test('不同库存单位分别展示', () => {
  assert.equal(model.inventorySummary([{ quantity: 0.5, unit: '卷' }, { quantity: 10, unit: '张' }]), '0.5（卷） / 10（张）');
});

test('趋势图保留未录入空值并正确呈现零产量', () => {
  const option = model.buildTrendOption({ dates: ['2026-11-20', '2026-11-21'], totals: [null, 10], series: [{ name: 'A线', data: [null, 0] }, { name: 'B线', data: [null, 10] }] }, true);
  assert.deepEqual(option.series[0].data, [null, 10]);
  assert.deepEqual(option.series[1].data, [null, 0]);
  assert.equal(option.tooltip.renderMode, 'richText');
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
