const test = require('node:test');
const assert = require('node:assert/strict');
const model = require('../../static/js/model.js');

test('点焊三条并行线共用单线计划，停机按设备总工时校验且保留空白', () => {
  const line = { id: 2, name: '点焊线', parallel_units: 3, actual_qty: 839, planned_hours: 20.4, abnormal_hours: 27.75 };
  assert.ok(Math.abs(model.plannedEquipmentHours(line) - 61.2) < 1e-8);
  assert.deepEqual(model.productionIssues(line, '2026-11-19'), []);
  assert.ok(model.productionIssues({ ...line, abnormal_hours: 62 }, '2026-11-19').some(issue => issue.severity === 'error'));
  assert.ok(model.productionIssues({ ...line, abnormal_hours: model.plannedEquipmentHours(line) }, '2026-11-19').some(issue => issue.severity === 'error'));
  assert.deepEqual(model.productionIssues({ ...line, actual_qty: 0, abnormal_hours: model.plannedEquipmentHours(line) }, '2026-11-19'), []);
  assert.equal(model.plannedEquipmentHours({ ...line, planned_hours: null }), null);
  assert.equal(model.plannedEquipmentHours({ ...line, planned_hours: '' }), '');
  assert.equal(model.plannedEquipmentHours({ ...line, parallel_units: 1 }), 20.4);
});

test('日报矛盾即时拦截，OEE 缺项保留未知值，零产量全天停机有效', () => {
  const line = { id: 4, name: 'B线', actual_qty: 510, defect_qty: 0, planned_hours: 20.4, abnormal_hours: 20.4, theoretical_max_per_hour: 60 };
  assert.ok(model.productionIssues(line, '2026-11-24').some(issue => issue.severity === 'error' && issue.field === 'abnormal_hours'));
  const unknown = { ...line, abnormal_hours: null, defect_qty: null };
  assert.deepEqual(model.productionIssues(unknown, '2026-11-24').map(issue => issue.field), ['abnormal_hours', 'defect_qty']);
  assert.equal(unknown.abnormal_hours, null);
  assert.deepEqual(model.productionIssues({ ...line, actual_qty: 0 }, '2026-11-24'), []);
  assert.deepEqual(model.productionIssues({ ...line, actual_qty: null }, '2026-11-24'), []);
  assert.ok(model.productionIssues({ ...line, planned_hours: 0, abnormal_hours: null }, '2026-11-24').some(issue => issue.field === 'planned_hours' && issue.severity === 'error'));
});

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

test('趋势图按产线并排且分别显示实际产量折线，保留零值', () => {
  const option = model.buildTrendOption({ dates: ['2026-11-20', '2026-11-21'], totals: [0, 10], series: [{ line_id: 3, name: 'A线', data: [0, 0] }, { line_id: 4, name: 'B线', data: [0, 10] }] }, true);
  assert.equal(option.series[0].type, 'bar');
  assert.equal(option.series[0].stack, undefined);
  assert.deepEqual(option.series[0].data, [0, 0]);
  assert.equal(option.series[1].type, 'bar');
  assert.equal(option.series[1].stack, undefined);
  assert.deepEqual(option.series[1].data, [0, 10]);
  assert.equal(option.series[2].type, 'line');
  assert.equal(option.series[2].name, 'A线');
  assert.deepEqual(option.series[2].data, [0, 0]);
  assert.deepEqual(option.series[3].data, [0, 10]);
  assert.equal(option.series.filter(series => series.type === 'line').length, 2);
  assert.equal(option.series[2].lineStyle.color, option.series[0].itemStyle.color);
  assert.equal(option.series[3].lineStyle.color, option.series[1].itemStyle.color);
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
  assert.equal(chosen.series[1].id, 'line-2');
  assert.deepEqual(chosen.series[1].data, [200]);
});

test('各产线单独绘制标准虚线，柱与折线的提示不重复', () => {
  const option = model.buildTrendOption({ dates: ['2026-11-21'], totals: [100], series: [
    { line_id: 3, name: 'A线', data: [100] }, { line_id: 4, name: 'B线', data: [0] },
  ] }, false, [{ id: 3, standard_qty: 1000 }, { id: 4, standard_qty: 1500 }]);
  assert.deepEqual(option.series.filter(series => series.markLine).map(series => series.markLine.data[0].yAxis), [1000, 1500]);
  assert.equal(option.series[2].markLine.lineStyle.color, '#39e75f');
  assert.equal(option.series[3].markLine.lineStyle.color, '#39e75f');
  assert.equal(option.series[0].label.position, 'top');
  assert.equal(option.series[1].label.position, 'inside');
  // 标准参考线保留颜色，但必须位于所有实际柱体下方。
  for (const marked of option.series.filter(series => series.markLine)) {
    assert.ok(option.series.filter(series => series.type === 'bar').every(bar => marked.markLine.z < bar.z));
    assert.equal(marked.markLine.lineStyle.width, 1.5);
    assert.equal(marked.markLine.lineStyle.opacity, 1);
  }
  assert.equal(option.yAxis[0].max({ max: 100 }), 1650);
  const tooltip = option.tooltip.formatter(option.series.map(series => ({ axisValue: '2026-11-21', seriesId: series.id, seriesName: series.name, value: series.data[0] })));
  assert.equal(tooltip, '2026-11-21\nA线：100（件）\nB线：0（件）\nA线标准：1,000（件）\nB线标准：1,500（件）');
});

test('B线日报全空时暂不绘制标准线，录入零或正产量后独立显示', () => {
  // 空白由接口标记保留，柱图中的零值不能用于判断产线是否已录入。
  for (const quantity of [null, 0, 80]) {
    const option = model.buildTrendOption({ dates: ['2026-11-21'], totals: [100 + (quantity || 0)], series: [
      { line_id: 3, name: 'A线', data: [100], has_data: true },
      { line_id: 4, name: 'B线', data: [quantity || 0], has_data: quantity !== null },
    ] }, false, [{ id: 3, standard_qty: 150 }, { id: 4, standard_qty: 200 }]);
    assert.equal(option.series.filter(series => series.markLine).length, quantity === null ? 1 : 2);
    assert.equal(option.series[2].markLine.data[0].yAxis, 150);
    assert.equal(option.series[3].markLine?.data[0].yAxis, quantity === null ? undefined : 200);
    assert.equal(option.yAxis[0].max({ max: 100 }), quantity === null ? 165 : 220);
    const tooltip = option.tooltip.formatter(option.series.map(series => ({ axisValue: '2026-11-21', seriesId: series.id, seriesName: series.name, value: series.data[0] })));
    assert.equal(tooltip.includes('B线标准：200（件）'), quantity !== null);
    assert.deepEqual(option.series[1].data, [quantity || 0]);
  }
});

test('所选产线日报全空时不绘制标准线，已有日报但缺标准时也不绘制', () => {
  for (const has_data of [false, true]) {
    const option = model.buildTrendOption({ dates: ['2026-11-21'], totals: [0], series: [
      { line_id: 3, name: 'A线', data: [0], has_data },
    ] }, false, [{ id: 3, standard_qty: has_data ? null : 150 }]);
    assert.equal(option.series[1].markLine, undefined);
    assert.ok(!option.tooltip.formatter([]).includes('标准'));
    assert.equal(option.yAxis[0].max({ max: 0 }), 1.1);
  }
});

test('米与件按产线并排，柱与折线使用相同纵轴及提示单位', () => {
  const option = model.buildTrendOption({ dates: ['2026-11-01'], series: [
    { line_id: 1, name: '落料', unit: '米', data: [120.5] },
    { line_id: 2, name: '点焊', unit: '件', data: [100] },
    { line_id: 3, name: '拼焊', unit: '件', data: [200] },
  ], groups: [{ unit: '米', totals: [120.5] }, { unit: '件', totals: [300] }] }, false);
  assert.deepEqual(option.yAxis.map(axis => axis.name), ['产量（米）', '产量（件）']);
  assert.ok(option.series.every(series => series.stack === undefined));
  assert.equal(option.series[0].yAxisIndex, 0);
  assert.equal(option.series[1].yAxisIndex, 1);
  assert.deepEqual(option.series.slice(3).map(series => series.data), [[120.5], [100], [200]]);
  assert.deepEqual(option.series.slice(3).map(series => series.yAxisIndex), [0, 1, 1]);
  const text = option.tooltip.formatter(option.series.map(series => ({ axisValue: '2026-11-01', seriesId: series.id, seriesName: series.name, value: series.data[0] })));
  assert.match(text, /落料：120.5（米）/);
  assert.match(text, /拼焊：200（件）/);
  assert.equal(option.series[0].label.formatter({ value: 120.5 }), '120.5');
});

test('同单位同标准共用标线且不在绘图区覆盖标签，不同单位仍使用各自纵轴', () => {
  const option = model.buildTrendOption({ dates: ['2026-11-21'], series: [
    { line_id: 1, name: '落料', unit: '米', data: [800] },
    { line_id: 3, name: 'A线', unit: '件', data: [900] },
    { line_id: 4, name: 'B线', unit: '件', data: [950] },
  ], groups: [{ unit: '米', totals: [800] }, { unit: '件', totals: [1850] }] }, false,
  [{ id: 1, standard_qty: 1000 }, { id: 3, standard_qty: 1000 }, { id: 4, standard_qty: 1000 }]);
  const marked = option.series.filter(series => series.markLine);
  assert.equal(marked.length, 2);
  assert.deepEqual(marked.map(series => series.yAxisIndex), [0, 1]);
  assert.ok(marked.every(series => series.markLine.label.show === false));
  assert.ok(option.series[5].markLine === undefined);
});

test('缺失或非法标准不影响其他产线，零标准仍可显示', () => {
  for (const value of [null, undefined, -1, NaN, Infinity]) {
    const option = model.buildTrendOption({ dates: ['2026-11-21'], series: [
      { line_id: 3, name: 'A线', data: [0] }, { line_id: 4, name: 'B线', data: [0] },
    ], totals: [0] }, true, [{ id: 3, standard_qty: value }, { id: 4, standard_qty: 0 }]);
    assert.equal(option.series[2].markLine, undefined);
    assert.equal(option.series[3].markLine.data[0].yAxis, 0);
    assert.equal(option.yAxis[0].max({ max: 0 }), 1.1);
  }
});

test('产线同名时按ID保留各自产量，提示仅去除各线重复的折线数据', () => {
  const option = model.buildTrendOption({ dates: ['2026-11-21'], totals: [300], series: [
    { line_id: 3, name: '生产线', data: [100] }, { line_id: 4, name: '生产线', data: [200] },
  ] });
  const text = option.tooltip.formatter(option.series.map(series => ({ axisValue: '2026-11-21', seriesId: series.id, seriesName: series.name, value: series.data[0] })));
  assert.equal(text, '2026-11-21\n生产线：100（件）\n生产线：200（件）');
});
