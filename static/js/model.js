"use strict";

/** 构建展示规则模块；无参数；返回可供浏览器及单元测试复用的公开函数。 */
const BoardModel = (() => {
  /** 格式化数值；value 为输入，digits 为最多小数位；返回数值文本或缺失符号。 */
  function formatNumber(value, digits = 2) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
    return Number(value).toLocaleString("zh-CN", { maximumFractionDigits: digits });
  }

  /** 统一单位标注；unit 为业务单位；返回中文括号包裹的单位，未提供时返回空字符串。 */
  function unitLabel(unit) {
    return unit ? `（${unit}）` : "";
  }

  /** 将库存重量换算为吨；value 为保存的千克数；返回吨数，空白保持为空。 */
  function weightTonnes(value) {
    return value == null || value === "" ? null : Number(value) / 1000;
  }

  /** 将编辑的吨数换回千克；value 为吨数；返回保留两位小数的千克数，空白保持为空。 */
  function weightKilograms(value) {
    return value == null || value === "" ? null : Number((Number(value) * 1000).toFixed(2));
  }

  /** 格式化百分比；value 为百分数数值；返回带百分号的文本或缺失符号。 */
  function formatPercent(value) {
    return value === null || value === undefined ? "—" : `${formatNumber(value, 1)}%`;
  }

  /** 按单位展示库存；totals 为后端分组汇总；返回各单位独立的摘要文本。 */
  function inventorySummary(totals) {
    return totals.length ? totals.map(item => `${formatNumber(item.quantity)}${unitLabel(item.unit)}`).join(" / ") : "—";
  }

  /** 保证零数量时仍有可见坐标范围；range 为图表数据范围；返回正的坐标上限。 */
  function positiveMaximum(range) {
    return Math.max(1, range.max);
  }

  /** 生成趋势图提示；params 为轴数据，standardQty 为所选产线标准日产量合计；返回画布提示文本。 */
  function trendTooltip(params, standardQty = null) {
    const standard = standardQty === null ? "" : `\n标准产量：${formatNumber(standardQty)}${unitLabel("件")}`;
    return `${params[0]?.axisValue || ""}\n${params.map(item => `${item.seriesName}：${formatNumber(item.value)}${unitLabel("件")}`).join("\n")}${standard}`;
  }

  /** 根据固定产线ID取颜色；lineId 为产线标识；返回不随名称或选择顺序变化的颜色。 */
  function trendColor(lineId) {
    return { 1: "#20dda4", 2: "#b897f3", 3: "#32c3f2", 4: "#ff9d23" }[lineId] || "#32c3f2";
  }

  /** 创建趋势图配置；trend 为趋势数据，compact 为紧凑模式，lines 为含标准日产量的产线配置；返回 ECharts 配置。 */
  function buildTrendOption(trend, compact = false, lines = []) {
    const standards = trend.series.map(item => lines.find(line => line.id === item.line_id)?.standard_qty);
    // 仅在所选产线标准值全部已配置时汇总，避免把缺失值当作零。
    const standardQty = standards.length && standards.every(value => Number.isFinite(value) && value >= 0)
      ? standards.reduce((sum, value) => sum + value, 0) : null;
    const standardLine = standardQty === null ? undefined : {
      silent: true, symbol: "none",
      lineStyle: { type: "dashed", width: 2, color: "#ffcf70" },
      label: { show: true, position: "insideEndTop", color: "#ffcf70", fontSize: compact ? 10 : 12,
        formatter: `标准产量 ${formatNumber(standardQty)}${unitLabel("件")}`, backgroundColor: "#0d1e29", padding: [3, 5] },
      data: [{ name: "标准产量", yAxis: standardQty }],
    };
    return {
      animation: false,
      textStyle: { fontFamily: "Bahnschrift, Microsoft YaHei, sans-serif" },
      tooltip: { trigger: "axis", renderMode: "richText", confine: true, formatter: params => trendTooltip(params, standardQty),
        backgroundColor: "#122936", borderColor: "#1b3545", textStyle: { color: "#f2f7fb" } },
      grid: { top: 26, left: 4, right: 9, bottom: 5, containLabel: true },
      xAxis: { type: "category", data: trend.dates, axisTick: { show: false },
        axisLine: { lineStyle: { color: "#1b3545" } },
        axisLabel: { color: "#a9c0d1", fontSize: compact ? 10 : 12, formatter: value => value.slice(8) } },
      yAxis: { type: "value", min: 0, splitNumber: compact ? 3 : 5, name: "产量（件）",
        nameTextStyle: { color: "#a9c0d1", fontSize: 11, align: "left" },
        axisLabel: { color: "#a9c0d1", fontSize: compact ? 10 : 12 },
        splitLine: { lineStyle: { color: "#244151", type: "dashed" } } },
      series: [{ id: "selected-total", name: "所选产线合计", type: "bar", data: trend.totals, barWidth: "48%", itemStyle: { color: "#27536c" }, markLine: standardLine },
        ...trend.series.map(item => ({ id: `line-${item.line_id}`, name: item.name, type: "line", data: item.data,
          connectNulls: false, symbol: "circle", symbolSize: 4, lineStyle: { width: 2, color: trendColor(item.line_id) },
          itemStyle: { color: trendColor(item.line_id) }, z: 3 }))],
    };
  }

  /** 创建出货图配置；items 为已排序的件号及数量；返回不修改输入的 ECharts 配置。 */
  function buildShipmentOption(items) {
    return {
      animation: false,
      textStyle: { fontFamily: "Bahnschrift, Microsoft YaHei, sans-serif" },
      grid: { top: 3, right: 70, left: 2, bottom: 3, containLabel: true },
      tooltip: { trigger: "axis", renderMode: "richText", confine: true,
        backgroundColor: "#122936", borderColor: "#1b3545", textStyle: { color: "#f2f7fb" } },
      xAxis: { type: "value", min: 0, max: positiveMaximum, show: false },
      yAxis: { type: "category", inverse: true, data: items.map(item => item.part_no),
        axisLine: { show: false }, axisTick: { show: false },
        axisLabel: { color: "#e0ebf2", fontSize: 11, interval: 0,
          formatter: (value, index) => `${String(index + 1).padStart(2, "0")}  ${value}` } },
      series: [{ name: "出货数量（件）", type: "bar", data: items.map(item => item.quantity), barWidth: 12,
        showBackground: true, backgroundStyle: { color: "#122936", borderRadius: 2 },
        itemStyle: { color: parameters => parameters.dataIndex < 3 ? "#20dda4" : "#32c3f2", borderRadius: 2 },
        label: { show: true, position: "right", color: "#e0ebf2", fontSize: 11,
          formatter: parameters => `${formatNumber(parameters.value)}${unitLabel("件")}` } }],
    };
  }

  return Object.freeze({ formatNumber, unitLabel, weightTonnes, weightKilograms, formatPercent, inventorySummary, trendColor, buildTrendOption, buildShipmentOption });
})();

if (typeof module !== "undefined" && module.exports) module.exports = BoardModel;
else window.BoardModel = BoardModel;
