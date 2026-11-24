const test = require('node:test');
const assert = require('node:assert/strict');
const model = require('../../static/js/model.js');

test('出货排名在件号下显示名称，悬浮提示保留完整名称，空名称不占行', () => {
  const ranking = [{ part_no: 'LP000365', name: '门环左件', quantity: 8200 },
    { part_no: 'LP000366', name: '', quantity: 7300 }];
  const option = model.buildShipmentOption(ranking);
  assert.deepEqual(option.yAxis[0].data, ['LP000365', 'LP000366']);
  assert.equal(option.yAxis[0].axisLabel.formatter('LP000365', 0), '{leading|01}{part|LP000365}\n{indent| }{detail|门环左件}');
  assert.equal(option.yAxis[0].axisLabel.formatter('LP000366', 1), '{leading|02}{part|LP000366}');
  assert.equal(option.tooltip.formatter([{ name: 'LP000365', dataIndex: 0, value: 8200 }]), 'LP000365\n门环左件\n出货数量：8,200 件');
  assert.equal(option.tooltip.formatter([{ name: 'LP000366', dataIndex: 1, value: 7300 }]), 'LP000366\n出货数量：7,300 件');
  assert.deepEqual(option.series[0].data, [8200, 7300]);
  // 满八项时两行文字必须落在各自的可用高度内。
  const compact = model.buildShipmentOption(Array.from({ length: 8 }, () => ranking[0]), 210);
  assert.ok(compact.yAxis[0].axisLabel.lineHeight * 2 < (210 - 18) / 8);
  // 数量独立成列，零值不依赖柱体面积，长名称仍能从提示中查看全文。
  const boundary = model.buildShipmentOption([{ part_no: 'LONG-PART-NUMBER-001', name: '用于检查超长名称的门环左件', quantity: 0 }]);
  assert.equal(boundary.yAxis[1].axisLabel.formatter('', 0), '{value|0}{unit| 件}');
  assert.match(boundary.yAxis[0].axisLabel.formatter('LONG-PART-NUMBER-001', 0), /…/);
  assert.match(boundary.tooltip.formatter([{ name: 'LONG-PART-NUMBER-001', dataIndex: 0, value: 0 }]), /用于检查超长名称的门环左件/);
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

test('趋势图按产线堆叠且仅使用实际总产量折线，保留零值', () => {
  const option = model.buildTrendOption({ dates: ['2026-11-20', '2026-11-21'], totals: [0, 10], series: [{ line_id: 3, name: 'A线', data: [0, 0] }, { line_id: 4, name: 'B线', data: [0, 10] }] }, true);
  assert.equal(option.series[0].type, 'bar');
  assert.equal(option.series[0].stack, 'production-件');
  assert.deepEqual(option.series[0].data, [0, 0]);
  assert.equal(option.series[1].type, 'bar');
  assert.equal(option.series[1].stack, option.series[0].stack);
  assert.deepEqual(option.series[1].data, [0, 10]);
  assert.equal(option.series[2].type, 'line');
  assert.equal(option.series[2].name, '所选产线合计');
  assert.deepEqual(option.series[2].data, [0, 10]);
  assert.equal(option.series.filter(series => series.type === 'line').length, 1);
  assert.equal(option.tooltip.renderMode, 'richText');
});

test('四条产线颜色固定且选择变化或改名后不改变', () => {
  const trend = { dates: ['2026-11-21'], totals: [1000], series: [1, 2, 3, 4].map(line_id => ({ line_id, name: `产线${line_id}`, data: [line_id * 100] })) };
  const all = model.buildTrendOption(trend);
  assert.equal(new Set(all.series.slice(0, 4).map(series => series.itemStyle.color)).size, 4);
  assert.equal(all.series[2].itemStyle.color, '#32c3f2');
  assert.equal(all.series[3].itemStyle.color, '#ff9d23');
  const chosen = model.buildTrendOption({ ...trend, totals: [200], series: [{ line_id: 2, name: '改名后的产线', data: [200] }] });
  assert.equal(chosen.series[0].itemStyle.color, all.series[1].itemStyle.color);
  assert.equal(chosen.series[0].itemStyle.color, model.trendColor(2));
  assert.equal(chosen.series[1].id, 'selected-total');
  assert.deepEqual(chosen.series[1].data, [200]);
});

test('堆叠柱与合计折线提示分别显示，标准日产量线只绘制一次', () => {
  const option = model.buildTrendOption({ dates: ['2026-11-21'], totals: [100], series: [
    { line_id: 3, name: 'A线', data: [100] }, { line_id: 4, name: 'B线', data: [0] },
  ] }, false, [{ id: 3, standard_qty: 150 }, { id: 4, standard_qty: 200 }]);
  assert.equal(option.series.filter(series => series.markLine).length, 1);
  assert.equal(option.series[2].markLine.data[0].yAxis, 350);
  const tooltip = option.tooltip.formatter(option.series.map(series => ({ axisValue: '2026-11-21', seriesName: series.name, value: series.data[0] })));
  assert.equal(tooltip, '2026-11-21\nA线：100（件）\nB线：0（件）\n所选产线合计：100（件）\n标准产量：350（件）');
});

test('B线日报全空时标准只计A线，录入零或正产量后恢复合计', () => {
  // 空白由接口标记保留，柱图中的零值不能用于判断产线是否已录入。
  for (const quantity of [null, 0, 80]) {
    const option = model.buildTrendOption({ dates: ['2026-11-21'], totals: [100 + (quantity || 0)], series: [
      { line_id: 3, name: 'A线', data: [100], has_data: true },
      { line_id: 4, name: 'B线', data: [quantity || 0], has_data: quantity !== null },
    ] }, false, [{ id: 3, standard_qty: 150 }, { id: 4, standard_qty: 200 }]);
    const standard = quantity === null ? 150 : 350;
    assert.equal(option.series.filter(series => series.markLine).length, 1);
    assert.equal(option.series[2].markLine.data[0].yAxis, standard);
    assert.equal(option.yAxis[0].max({ max: 100 }), quantity === null ? 165 : 385);
    const tooltip = option.tooltip.formatter(option.series.map(series => ({ axisValue: '2026-11-21', seriesId: series.id, seriesName: series.name, value: series.data[0] })));
    assert.ok(tooltip.endsWith(`标准产量：${standard}（件）`));
    assert.deepEqual(option.series[1].data, [quantity || 0]);
  }
});

test('所选产线日报全空时不绘制标准线，已有日报但缺标准时也不绘制', () => {
  for (const has_data of [false, true]) {
    const option = model.buildTrendOption({ dates: ['2026-11-21'], totals: [0], series: [
      { line_id: 3, name: 'A线', data: [0], has_data },
    ] }, false, [{ id: 3, standard_qty: has_data ? null : 150 }]);
    assert.equal(option.series[1].markLine, undefined);
    assert.ok(!option.tooltip.formatter([]).includes('标准产量'));
    assert.equal(option.yAxis[0].max({ max: 0 }), 1.1);
  }
});

test('米与件分别堆叠、分别求和并使用独立纵轴与提示单位', () => {
  const option = model.buildTrendOption({ dates: ['2026-11-01'], series: [
    { line_id: 1, name: '落料', unit: '米', data: [120.5] },
    { line_id: 2, name: '点焊', unit: '件', data: [100] },
    { line_id: 3, name: '拼焊', unit: '件', data: [200] },
  ], groups: [{ unit: '米', totals: [120.5] }, { unit: '件', totals: [300] }] }, false);
  assert.deepEqual(option.yAxis.map(axis => axis.name), ['产量（米）', '产量（件）']);
  assert.notEqual(option.series[0].stack, option.series[1].stack);
  assert.equal(option.series[1].stack, option.series[2].stack);
  assert.equal(option.series[0].yAxisIndex, 0);
  assert.equal(option.series[1].yAxisIndex, 1);
  assert.deepEqual(option.series.slice(3).map(series => series.data), [[120.5], [300]]);
  const text = option.tooltip.formatter(option.series.map(series => ({ axisValue: '2026-11-01', seriesId: series.id, seriesName: series.name, value: series.data[0] })));
  assert.match(text, /落料：120.5（米）/);
  assert.match(text, /拼焊：200（件）/);
  assert.equal(option.series[0].label.formatter({ value: 120.5 }), '120.5');
});
