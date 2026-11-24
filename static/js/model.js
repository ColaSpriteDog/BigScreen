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

  /** 读取趋势单位分组；trend 为接口数据；返回各单位独立合计，旧配置输入默认按件处理。 */
  function trendGroups(trend) {
    return trend.groups || [{ unit: trend.series[0]?.unit || "件", totals: trend.totals, period_total: trend.period_total }];
  }

  /** 根据固定产线ID取颜色；lineId 为产线标识；返回不随名称或选择顺序变化的颜色。 */
  function trendColor(lineId) {
    return { 1: "#20dda4", 2: "#b897f3", 3: "#32c3f2", 4: "#ff9d23" }[lineId] || "#32c3f2";
  }

  /** 创建趋势图配置；trend 为趋势数据，compact 为紧凑模式，lines 为含标准日产量的产线配置；返回 ECharts 配置。 */
  function buildTrendOption(trend, compact = false, lines = []) {
    const groups = trendGroups(trend);
    const multiple = groups.length > 1;
    // 仅在所选产线标准值全部已配置时汇总，避免把缺失值当作零。
    const standards = groups.map(group => {
      // 本月日报全空的产线暂不计入标准，明确录入 0 的产线仍参与统计。
      const values = trend.series.filter(item => (item.unit || "件") === group.unit && item.has_data !== false)
        .map(item => lines.find(line => line.id === item.line_id)?.standard_qty);
      return values.length && values.every(value => Number.isFinite(value) && value >= 0)
        ? values.reduce((sum, value) => sum + value, 0) : null;
    });
    const series = [
      // 不同计量单位使用不同堆叠组和纵轴，避免米数与件数相加。
      ...trend.series.map(item => ({
        id: `bar-${item.line_id}`, name: item.name, type: "bar", stack: `production-${item.unit || "件"}`,
        yAxisIndex: groups.findIndex(group => group.unit === (item.unit || "件")), barWidth: multiple ? "28%" : "48%",
        data: item.data,
        // 数量居中标在对应色块内；零产量没有柱体面积，省略文字以免在基线重叠。
        label: { show: true, position: "inside", color: "#f2f7fb", fontSize: compact ? 9 : 11, fontWeight: "bold",
          textBorderColor: "#07141c", textBorderWidth: 2,
          formatter: params => params.value > 0 ? formatNumber(params.value) : "" },
        itemStyle: { color: trendColor(item.line_id) }, z: 2,
      })),
      ...groups.map((group, index) => ({
        id: multiple ? `selected-total-${group.unit}` : "selected-total",
        name: "所选产线合计" + (multiple ? unitLabel(group.unit) : ""), type: "line", data: group.totals,
        yAxisIndex: index, connectNulls: false, symbol: index ? "diamond" : "circle", symbolSize: 4,
        lineStyle: { width: 2, color: "#f2f7fb", type: index ? "dashed" : "solid" }, itemStyle: { color: "#f2f7fb" }, z: 3,
        markLine: standards[index] === null ? undefined : {
          silent: true, symbol: "none", lineStyle: { type: "dashed", width: 2, color: "#ffcf70" },
          label: { show: true, position: index ? "insideEndBottom" : "insideEndTop", color: "#ffcf70", fontSize: compact ? 10 : 12,
            formatter: `标准产量 ${formatNumber(standards[index])}${unitLabel(group.unit)}`, backgroundColor: "#0d1e29", padding: [3, 5] },
          data: [{ name: "标准产量", yAxis: standards[index] }],
        },
      })),
    ];
    return {
      animation: false,
      textStyle: { fontFamily: "Bahnschrift, Microsoft YaHei, sans-serif" },
      tooltip: { trigger: "axis", renderMode: "richText", confine: true, formatter: params => {
        const values = params.map(item => {
          const config = series.find(entry => entry.id === item.seriesId) || series.find(entry => entry.name === item.seriesName);
          const unit = groups[config?.yAxisIndex || 0].unit;
          return `${item.seriesName}：${formatNumber(item.value)}${unitLabel(unit)}`;
        });
        groups.forEach((group, index) => {
          if (standards[index] !== null) values.push(`标准产量：${formatNumber(standards[index])}${unitLabel(group.unit)}`);
        });
        return `${params[0]?.axisValue || ""}\n${values.join("\n")}`;
      },
        backgroundColor: "#122936", borderColor: "#1b3545", textStyle: { color: "#f2f7fb" } },
      grid: { top: 26, left: 4, right: multiple ? 4 : 9, bottom: 5, containLabel: true },
      xAxis: { type: "category", data: trend.dates, axisTick: { show: false },
        axisLine: { lineStyle: { color: "#1b3545" } },
        axisLabel: { color: "#a9c0d1", fontSize: compact ? 10 : 12, formatter: value => value.slice(8) } },
      yAxis: groups.map((group, index) => ({ type: "value", min: 0, position: index ? "right" : "left",
        // 标准值也参与纵轴范围，避免低产或空数据时标准虚线落到可视区域之外。
        max: range => Number((Math.max(1, range.max, standards[index] || 0) * 1.1).toPrecision(12)),
        splitNumber: compact ? 3 : 5, name: "产量" + unitLabel(group.unit),
        nameTextStyle: { color: "#a9c0d1", fontSize: 11, align: index ? "right" : "left" },
        axisLabel: { color: "#a9c0d1", fontSize: compact ? 10 : 12 },
        splitLine: { show: index === 0, lineStyle: { color: "#244151", type: "dashed" } } })),
      series,
    };
  }

  /** 创建出货排名横柱图；ranking 为已汇总排序的前八个件号、height 为容器高度；返回图表配置。 */
  function buildShipmentOption(ranking, height = 300) {
    // 根据每项可用高度调整双行标签，避免小屏显示满八项时相互遮挡。
    const labelLineHeight = Math.max(10, Math.min(16, Math.floor((height - 18) / Math.max(1, ranking.length) / 2) - 1));
    const categories = ranking.map(item => item.part_no);
    // 数量列按实际最长文本预留宽度，与柱长解耦，零数量也能完整显示。
    const quantityWidth = Math.max(52, ...ranking.map(item => formatNumber(item.quantity).length * 7 + 20));
    return {
      animation: false,
      textStyle: { fontFamily: "Bahnschrift, Microsoft YaHei, sans-serif" },
      grid: { top: 10, right: quantityWidth, bottom: 8, left: 120 },
      tooltip: { trigger: "axis", confine: true, renderMode: "richText",
        axisPointer: { type: "shadow", shadowStyle: { color: "rgba(169, 192, 209, .045)" } },
        backgroundColor: "#122936", borderColor: "#294859", borderWidth: 1, padding: [10, 12],
        textStyle: { color: "#f2f7fb", fontSize: 12, lineHeight: 20 },
        // 提示保留完整名称，便于查看坐标轴上因长度被截断的内容。
        formatter: params => {
          const item = ranking[params[0]?.dataIndex];
          return `${params[0]?.name || ""}${item?.name ? "\n" + item.name : ""}\n出货数量：${formatNumber(params[0]?.value)} 件`;
        } },
      xAxis: { type: "value", show: false, min: 0 },
      yAxis: [{ type: "category", inverse: true, data: categories,
        axisLine: { show: false }, axisTick: { show: false },
        axisLabel: { color: "#a9c0d1", fontSize: labelLineHeight < 13 ? 10 : 11, lineHeight: labelLineHeight, margin: 10, interval: 0,
          rich: {
            rank: { width: 22, align: "left", color: "#668595", fontSize: 10 },
            leading: { width: 22, align: "left", color: "#20dda4", fontSize: 10, fontWeight: 700 },
            part: { width: 88, align: "left", color: "#e0ebf2", fontWeight: 600 },
            indent: { width: 22, align: "left" },
            detail: { width: 88, align: "left", color: "#809cab", fontSize: labelLineHeight < 13 ? 9 : 10, overflow: "truncate" },
          },
          // 件号与名称分行显示，名称为空时不添加空行。
          formatter: (value, index) => {
            const rank = String(index + 1).padStart(2, "0");
            const part = value.length > 12 ? value.slice(0, 11) + "…" : value;
            const name = ranking[index]?.name || "";
            const detail = name.length > 8 ? name.slice(0, 8) + "…" : name;
            return `{${index < 3 ? "leading" : "rank"}|${rank}}{part|${part}}${name ? `\n{indent| }{detail|${detail}}` : ""}`;
          } } },
        // 右侧分类轴仅承载数量，所有数值靠右对齐，不挤占短柱条。
        { type: "category", inverse: true, position: "right", data: categories,
          axisLine: { show: false }, axisTick: { show: false },
          axisLabel: { interval: 0, margin: quantityWidth, align: "right", fontSize: 11,
            rich: { value: { color: "#e0ebf2", fontWeight: 600 }, unit: { color: "#809cab", fontSize: 10 } },
            formatter: (value, index) => `{value|${formatNumber(ranking[index].quantity)}}{unit| 件}` } }],
      series: [{ name: "出货数量", type: "bar", barWidth: Math.max(5, Math.min(10, (height - 18) / 24)),
        showBackground: true, backgroundStyle: { color: "rgba(169, 192, 209, .055)", borderRadius: 2 },
        itemStyle: { color: params => params.dataIndex < 3 ? "#20cfa0" : "#3199b5", borderRadius: 2 },
        emphasis: { itemStyle: { color: "#51e5bc" } },
        data: ranking.map(item => item.quantity) }],
      graphic: ranking.length ? undefined : { type: "text", left: "center", top: "middle",
        style: { text: "暂无出货记录", fill: "#a9c0d1", fontSize: 12 } },
    };
  }

  return Object.freeze({ formatNumber, unitLabel, weightTonnes, weightKilograms, formatPercent, inventorySummary, trendColor, trendGroups, buildTrendOption, buildShipmentOption });
})();

if (typeof module !== "undefined" && module.exports) module.exports = BoardModel;
else window.BoardModel = BoardModel;
