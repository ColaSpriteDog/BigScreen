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

  /** 格式化百分比；value 为百分数数值；返回最多两位小数的百分比文本或缺失符号。 */
  function formatPercent(value) {
    return value === null || value === undefined ? "—" : `${formatNumber(value, 2)}%`;
  }

  /** 按单位展示库存；totals 为后端分组汇总；返回各单位独立的摘要文本。 */
  function inventorySummary(totals) {
    return totals.length ? totals.map(item => `${formatNumber(item.quantity)}${unitLabel(item.unit)}`).join(" / ") : "—";
  }

  /** 保证零数量时仍有可见坐标范围；range 为图表数据范围；返回正的坐标上限。 */
  function positiveMaximum(range) {
    return Math.max(1, range.max);
  }

  /** 检查填写中的日报；line 为产线草稿、day 为日期；返回可定位的问题，缺项不自动补零。 */
  function productionIssues(line, day) {
    if (line.actual_qty == null || line.actual_qty === "") return [];
    const actual = Number(line.actual_qty), planned = line.planned_hours, abnormal = line.abnormal_hours;
    const issues = [];
    const add = (field, message, severity = "warning") => issues.push({ date: day, line_id: line.id, name: line.name, field, message, severity });
    if (planned == null || planned === "") add("planned_hours", "请填写计划生产时长。", "error");
    else if (actual > 0 && planned === 0) add("planned_hours", "已有产量，计划生产时长必须大于 0，请核对。", "error");
    if (abnormal != null && abnormal !== "" && planned != null) {
      if (abnormal > planned) add("abnormal_hours", "异常停机工时不能超过计划生产时长，请核对。", "error");
      else if (actual > 0 && abnormal >= planned) add("abnormal_hours", `已有产量 ${formatNumber(actual)}，异常停机工时必须小于计划工时 ${formatNumber(planned)} 小时，请核对。`, "error");
    }
    if (![3, 4].includes(line.id)) return issues;
    if (planned === 0 && actual === 0) add("planned_hours", "计划生产时长为 0，本月设备综合效率暂无法计算；请核对是否无生产计划。");
    if (abnormal == null || abnormal === "") add("abnormal_hours", "未填写异常停机工时，本月设备综合效率暂无法计算；确认无异常停机请填 0。");
    if (actual > 0 && (line.defect_qty == null || line.defect_qty === "")) add("defect_qty", "未填写不良数量，本月设备综合效率暂无法计算；确认无不良请填 0。");
    if (!(line.theoretical_max_per_hour > 0)) add("theoretical_max_per_hour", "未配置设备理论最大产量，请到流程与设置填写。");
    return issues;
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
    const entries = trend.series.map(item => {
      const unit = item.unit || "件";
      const standard = lines.find(line => line.id === item.line_id)?.standard_qty;
      return { ...item, unit, yAxisIndex: groups.findIndex(group => group.unit === unit), color: trendColor(item.line_id),
        // 本月日报全空时暂不绘制标准线，明确录入零的产线仍显示标准。
        standard: item.has_data !== false && Number.isFinite(standard) && standard >= 0 ? standard : null };
    });
    // 同单位且同数值的标准共用参考线，图例仍分别标注产线，避免相同虚线互相覆盖。
    const standards = [];
    entries.forEach(item => {
      if (item.standard === null) return;
      const shared = standards.find(entry => entry.unit === item.unit && entry.value === item.standard);
      if (shared) shared.lines.push(item);
      else standards.push({ unit: item.unit, value: item.standard, lines: [item] });
    });
    const series = [
      // 各产线按日期并排，米与件仍使用各自纵轴，不叠加数量。
      ...entries.map(item => ({
          id: `bar-${item.line_id}`, name: item.name, type: "bar",
          yAxisIndex: item.yAxisIndex, barWidth: `${Math.min(48, 62 / entries.length)}%`, barGap: "12%", data: item.data,
          // A 线数值置于柱顶，其余产线保留柱内数值，密集时隐藏重叠文字。
          label: { show: true, position: item.line_id === 3 ? "top" : "inside", distance: 6, color: "#f2f7fb", fontSize: compact ? 9 : 11,
            textBorderColor: "#07141c", textBorderWidth: 2,
            formatter: params => params.value > 0 ? formatNumber(params.value) : "" },
          // 当前 ECharts 将标准线固定绘制在第 5 层，柱体及数值需位于其上方。
          labelLayout: { hideOverlap: true }, itemStyle: { color: item.color }, z: 6,
      })),
      ...entries.map((item, index) => {
        const standard = standards.find(entry => entry.lines[0].line_id === item.line_id);
        return {
          id: `line-${item.line_id}`, name: item.name, type: "line", data: item.data,
          yAxisIndex: item.yAxisIndex, connectNulls: false, symbol: ["circle", "diamond", "rect", "triangle"][index % 4], symbolSize: compact ? 3 : 4,
          lineStyle: { width: 2, color: item.color }, itemStyle: { color: item.color }, z: 8,
          markLine: standard ? {
            // 标准线置于柱体和数值下方，避免默认高层级虚线横穿实际产量。
            z: 5, silent: true, symbol: "none", lineStyle: { type: "dashed", width: 1, opacity: 0.55, color: item.color },
            // 标准值由顶部图例与悬浮提示展示，避免文字底色遮挡柱体和实际折线。
            label: { show: false },
            data: [{ name: "标准产量", yAxis: standard.value }],
          } : undefined,
        };
      }),
    ];
    return {
      animation: false,
      textStyle: { fontFamily: "Bahnschrift, Microsoft YaHei, sans-serif" },
      tooltip: { trigger: "axis", renderMode: "richText", confine: true, formatter: params => {
        // 柱与折线来自同一份日产量，悬浮提示按产线去重。
        const seen = new Set();
        const values = params.flatMap(item => {
          const config = series.find(entry => entry.id === item.seriesId) || series.find(entry => entry.name === item.seriesName);
          const key = config?.id.replace(/^(bar|line)-/, "") || item.seriesName;
          if (seen.has(key)) return [];
          seen.add(key);
          const unit = groups[config?.yAxisIndex || 0].unit;
          return [`${item.seriesName}：${formatNumber(item.value)}${unitLabel(unit)}`];
        });
        entries.forEach(item => {
          if (item.standard !== null) values.push(`${item.name}标准：${formatNumber(item.standard)}${unitLabel(item.unit)}`);
        });
        return `${params[0]?.axisValue || ""}\n${values.join("\n")}`;
      },
        backgroundColor: "#122936", borderColor: "#1b3545", textStyle: { color: "#f2f7fb" } },
      grid: { top: 26, left: 4, right: multiple ? 4 : 9, bottom: 5, containLabel: true },
      xAxis: { type: "category", data: trend.dates, axisTick: { show: false },
        axisLine: { lineStyle: { color: "#1b3545" } },
        axisLabel: { color: "#a9c0d1", fontSize: compact ? 10 : 12, formatter: value => value.slice(8) } },
      yAxis: groups.map((group, index) => ({ type: "value", min: 0, position: index ? "right" : "left",
        // 各产线标准值参与纵轴范围，避免标准虚线落到可视区域之外。
        max: range => Number((Math.max(1, range.max, ...entries
          .filter(item => item.unit === group.unit).map(item => item.standard || 0)) * 1.1).toPrecision(12)),
        splitNumber: compact ? 3 : 5, name: "产量" + unitLabel(group.unit),
        nameTextStyle: { color: "#a9c0d1", fontSize: 11, align: index ? "right" : "left" },
        axisLabel: { color: "#a9c0d1", fontSize: compact ? 10 : 12 },
        splitLine: { show: index === 0, lineStyle: { color: "#244151", type: "dashed" } } })),
      series,
    };
  }

  /** 创建出货排名横柱图；ranking 为已汇总排序的前八个件号、height 为容器高度；返回图表配置。 */
  function buildShipmentOption(ranking, height = 300) {
    // 每条记录最多占 52 像素，少量数据靠上紧凑排列，满榜时按容器高度收缩。
    const plotHeight = Math.min(Math.max(0, height - 18), Math.max(1, ranking.length) * 52);
    // 根据每项可用高度调整双行标签，避免小屏显示满八项时相互遮挡。
    const labelLineHeight = Math.max(10, Math.min(16, Math.floor(plotHeight / Math.max(1, ranking.length) / 2) - 1));
    const categories = ranking.map(item => item.part_no);
    // 数量列按实际最长文本预留宽度，与柱长解耦，零数量也能完整显示。
    const quantityWidth = Math.max(52, ...ranking.map(item => formatNumber(item.quantity).length * 7 + 20));
    return {
      animation: false,
      textStyle: { fontFamily: "Bahnschrift, Microsoft YaHei, sans-serif" },
      grid: { top: 10, right: quantityWidth, height: plotHeight, left: 120 },
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

  return Object.freeze({ formatNumber, unitLabel, weightTonnes, weightKilograms, formatPercent, inventorySummary, productionIssues, trendColor, trendGroups, buildTrendOption, buildShipmentOption });
})();

if (typeof module !== "undefined" && module.exports) module.exports = BoardModel;
else window.BoardModel = BoardModel;
