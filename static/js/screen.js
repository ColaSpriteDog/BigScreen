"use strict";

/** 初始化联网大屏；无参数；无返回值，启动读取、时钟和默认库存滚动。 */
(() => {
  const model = window.BoardModel;
  let lastData = null;
  let inventoryCache = "";
  let processCache = "";
  let reading = false;
  let serverOffset = 0;
  let trendChart = null;
  let shipmentChart = null;

  /** 对齐产量折线；chartModel 为完成默认布局的图表模型；按同源柱体中心更新折点，无返回值。 */
  function alignTrendLinePoints(chartModel) {
    chartModel.eachSeriesByType("line", line => {
      if (!line.id.startsWith("line-")) return;
      const bar = chartModel.queryComponents({ mainType: "series", id: line.id.replace(/^line-/, "bar-") })[0];
      if (!bar || bar.subType !== "bar") return;
      const points = line.getData().getLayout("points");
      if (!points) return;
      const bars = bar.getData();
      for (let index = 0; index < bars.count(); index++) {
        const layout = bars.getItemLayout(index);
        // 使用实际柱宽兼容零产量、独立纵轴和窗口缩放，空数据仍保留断点。
        if (layout && Number.isFinite(layout.x) && Number.isFinite(points[index * 2])) {
          points[index * 2] = layout.x + layout.width / 2;
        }
      }
    });
  }

  // 在默认柱与折线布局完成后执行，数据刷新和 resize 都会重新计算坐标。
  if (window.echarts) echarts.registerLayout(echarts.PRIORITY.VISUAL.PROGRESSIVE_LAYOUT + 1, alignTrendLinePoints);

  /** 创建安全文本节点；tag、className、value 为元素类型、样式及内容；返回 DOM 元素。 */
  function node(tag, className = "", value = "") {
    const element = document.createElement(tag);
    element.className = className;
    element.textContent = value;
    return element;
  }

  /** 更新指定元素文本；id 为元素ID、value 为内容；无返回值。 */
  function setText(id, value) {
    document.getElementById(id).textContent = value;
  }

  /** 为数值附加单位；value 为数值、unit 为单位；返回缺失符号或格式化文本。 */
  function quantity(value, unit = "件") {
    return value === null || value === undefined ? "—" : model.formatNumber(value) + model.unitLabel(unit);
  }

  /** 创建产线字段；label 为标题、value 为展示值、warning 为告警样式；返回字段行。 */
  function metric(label, value, warning = false) {
    const row = node("div");
    // 标题使用独立节点，缩小标题字号时保留数值的原有字号与强调层级。
    row.append(node("span", "metric-label", label), node("span", warning ? "val warn" : "val", value));
    row.title = value;
    return row;
  }

  /** 格式化产线数量；value 为原始数量、line 为产线信息；返回展示文本，仅落料线米数截去小数，缺失保持为空。 */
  function lineQuantity(value, line) {
    // 固定产线标识不受名称修改影响；仅调整卡片显示，不改变原始数据和百分比。
    const integerMetres = line.id === 1 && line.unit === "米";
    return model.formatNumber(integerMetres && value != null ? Math.trunc(value) : value);
  }

  /** 对齐落料线指标；无参数；优先共用列宽，仅在数值溢出时借用第二行空白，无返回值且不修改字号。 */
  function fitLineLayout() {
    const list = document.querySelector('.line-card[data-line-id="1"] .data-list');
    if (!list) return;
    // 每次从对齐布局重新测量，窗口放大或数值变短后可自动恢复列对齐。
    list.classList.remove("is-compact");
    const overflow = [...list.children].slice(0, 4).some(row => {
      const value = row.querySelector(".val");
      const range = document.createRange();
      range.selectNodeContents(value);
      return range.getBoundingClientRect().width > value.getBoundingClientRect().width + 0.1;
    });
    list.classList.toggle("is-compact", overflow);
  }

  /** 展示四条产线；lines 为后端结果；无返回值，运行状态来自配置，运行率来自本月累计工时。 */
  function renderLines(lines) {
    const cards = lines.map(line => {
      const building = line.status === "construction";
      const card = node("section", "panel line-card" + (building ? " is-construction" : line.status === "running" ? "" : " is-maintenance") + (line.status === "fault" ? " is-fault" : ""));
      card.dataset.lineId = line.id;
      card.dataset.state = line.status;
      const header = node("div", "line-header");
      const title = node("span", "", line.name);
      title.title = line.name;
      const indicators = node("div", "line-indicators");
      indicators.append(node("span", "status-dot"), node("span", "line-index", line.code));
      header.append(title, indicators);
      const list = node("div", "data-list");
      // 产量单位放在字段名后；不良指标合并数量与比例，单位随数量展示。
      const unit = model.unitLabel(line.unit || "件");
      // 标准值为日产量，单位补充时间口径，避免与月累计数量混淆。
      const dailyUnit = model.unitLabel((line.unit || "件") + "/天");
      // 昨日产量与报废使用同一业务日期，缺失保持为空，不能以更早的记录替代昨日。
      // 括号内仅展示昨日不良率，避免数量与百分比连读；缺失数量不附加单位。
      // 报废数量保留最多两位小数，避免小额报废被截为零；产量仍按整数显示。
      const defectText = (line.yesterday_defect_qty == null ? "—" : `${model.formatNumber(line.yesterday_defect_qty)} ${line.unit || "件"}`)
        // 不良率固定保留两位小数，避免较小的非零比例被显示为 0%。
        + (line.yesterday_defect_rate == null ? "" : `（${line.yesterday_defect_rate.toFixed(2)}%）`);
      list.append(metric("标准产量" + dailyUnit, lineQuantity(line.standard_qty, line)), metric("昨日产量" + unit, lineQuantity(line.yesterday_qty, line)),
        metric("本月产量" + unit, lineQuantity(line.monthly_qty, line)), metric(line.unit === "米" ? "昨日报废" : "昨日不良", defectText, line.yesterday_defect_qty > 0));
      const rate = line.runtime_rate == null ? "" : " " + model.formatPercent(line.runtime_rate);
      const status = metric("运行状态（月）", line.status_label + rate);
      status.lastElementChild.classList.add("line-state");
      status.title = `本月累计计划生产 ${model.formatNumber(line.monthly_planned_hours)}（小时）；异常停机 ${model.formatNumber(line.monthly_abnormal_hours)}（小时）；运行率按本月已录入数据的累计工时计算，缺少异常停机工时或无生产计划时不显示百分比`;
      list.append(status);
      const progress = node("div", "progress-bar");
      progress.hidden = line.runtime_rate == null;
      const fill = node("div", "progress-inner" + (line.status === "running" ? "" : " warn"));
      fill.style.width = `${line.runtime_rate ?? 0}%`;
      progress.append(fill);
      card.append(header, list, progress);
      return card;
    });
    document.getElementById("lineCards").replaceChildren(...cards);
    fitLineLayout();
  }

  /** 创建库存数值单元格；value 为数量、digits 为最多小数位、className 为业务样式；返回数字单元格，单位显示在表头。 */
  function numericCell(value, digits = 2, className = "") {
    const cell = node("td", "material-number" + (className ? " " + className : ""));
    cell.append(node("span", value == null ? "val is-empty" : "val", model.formatNumber(value, digits)));
    return cell;
  }

  /** 创建原材料规格单元格；value 为源规格；返回按斜线分段换行、保留完整内容的单元格。 */
  function specificationCell(value) {
    const cell = node("td", "material-spec");
    const parts = String(value ?? "").split("/");
    parts.forEach((part, index) => {
      const last = index === parts.length - 1;
      cell.append(node("span", "material-spec-part", part + (last ? "" : "/")));
      if (!last) cell.append(document.createElement("wbr"));
    });
    return cell;
  }

  /** 提取库存单位；items 为同类库存记录；返回去重后的单位列，空表保留一个无单位库存列。 */
  function inventoryUnits(items) {
    const units = [...new Set(items.map(item => item.unit))];
    return units.length ? units : [""];
  }

  /** 更新库存表头；id 为行标识，before/after 为固定字段，units 为单位列；返回完整列配置。 */
  function inventoryHeader(id, before, units, after = []) {
    const columns = [...before, ...units.map(unit => ({ label: "剩余数量" + model.unitLabel(unit), className: "material-number stock-heading", width: 31 })), ...after];
    document.getElementById(id).replaceChildren(...columns.map(column => {
      const heading = node("th", column.className || "");
      // 单位紧跟字段名称显示，使用较小字号保持表头单行。
      const unit = column.label.match(/（[^）]+）$/);
      heading.append(document.createTextNode(unit ? column.label.slice(0, unit.index) : column.label));
      if (unit) heading.append(node("span", "inventory-column-unit", unit[0]));
      heading.scope = "col";
      return heading;
    }));
    return columns;
  }

  /** 创建空表提示；columns 为列数、label 为文案；返回跨列空记录行。 */
  function emptyRow(columns, label) {
    const row = node("tr", "empty-row");
    const cell = node("td", "", label);
    cell.colSpan = columns;
    row.append(cell);
    return row;
  }

  /** 更新库存摘要；id 为摘要节点，totals 为按单位分组的数据；无返回值。 */
  function renderSummary(id, totals) {
    const element = document.getElementById(id);
    element.classList.toggle("multiple", totals.length > 1);
    if (totals.length === 1) {
      element.replaceChildren(document.createTextNode(model.formatNumber(totals[0].quantity)), node("span", "unit", model.unitLabel(totals[0].unit)));
    } else element.textContent = model.inventorySummary(totals);
  }

  /** 展示库存独立字段；inventory 为后端库存模块；无返回值，相同明细不重绘以保留滚动位置。 */
  function renderInventory(inventory) {
    renderSummary("rawTotal", inventory.raw_totals);
    // 仅汇总已填写的千克重量后换算为吨，全空时保持缺失，明确零值正常展示。
    const weights = inventory.raw.filter(item => item.weight_kg != null && item.weight_kg !== "");
    const totalWeight = weights.length ? model.weightTonnes(weights.reduce((sum, item) => sum + Number(item.weight_kg), 0)) : null;
    document.getElementById("rawWeight").replaceChildren(document.createTextNode(model.formatNumber(totalWeight, 5)), node("span", "unit", model.unitLabel("吨")));
    renderSummary("semiTotal", inventory.semi_totals);
    setText("zeroCount", inventory.zero_count ? `${inventory.zero_count}项零库存` : "");
    const signature = JSON.stringify([inventory.raw, inventory.semi]);
    if (signature === inventoryCache) return;
    inventoryCache = signature;
    const rawUnits = inventoryUnits(inventory.raw);
    const semiUnits = inventoryUnits(inventory.semi);
    const rawColumns = inventoryHeader("rawTableHeader", [
      // 产品名称分配更多宽度，减少长名称拆行，带单位的数量表头继续保持单行。
      { label: "产品名称", width: 24 }, { label: "规格型号", width: 25 },
    ], rawUnits, [
      { label: "重量（吨）", className: "material-number", width: 22 },
      // 可切列兼顾单行表头与六位件数，单一单位时总列宽权重保持不变。
      { label: "可切（件）", className: "material-number", width: 24 },
    ]);
    const totalWidth = rawColumns.reduce((sum, column) => sum + column.width, 0);
    document.getElementById("rawTableColumns").replaceChildren(...rawColumns.map(column => {
      const element = node("col");
      element.style.width = column.width / totalWidth * 100 + "%";
      return element;
    }));
    inventoryHeader("semiTableHeader", [{ label: "类别" }, { label: "产品名称" },
      { label: "规格型号", className: "semi-spec" }], semiUnits);
    const materialRows = inventory.raw.map(item => {
      const row = node("tr", item.status === "zero" ? "is-shortage" : "");
      // 钢卷归入原材料，行内按汇总表展示产品、规格、余量、重量和预计可切数量。
      row.append(node("td", "material-name material-part", item.position || item.material),
        specificationCell(item.specification),
        ...rawUnits.map(unit => numericCell(item.unit === unit ? item.quantity : null, 2, "stock-quantity")), numericCell(model.weightTonnes(item.weight_kg), 5));
      row.append(numericCell(item.cuttable_qty));
      return row;
    });
    document.getElementById("rawTableBody").replaceChildren(...(materialRows.length ? materialRows : [emptyRow(4 + rawUnits.length, "暂无原材料记录")]));
    const semiRows = inventory.semi.map((item, index, items) => {
      const row = node("tr");
      // 连续的左右件合并类别单元格，保留分类且不重复显示仓库。
      if (index === 0 || items[index - 1].category !== item.category) {
        let count = 1;
        while (index + count < items.length && items[index + count].category === item.category) count++;
        const category = node("td", "semi-group");
        category.append(node("span", "semi-group-label", item.category));
        category.rowSpan = count;
        row.append(category);
      }
      row.append(node("td", "semi-name", item.material),
        node("td", "semi-spec", item.specification), ...semiUnits.map(unit => numericCell(item.unit === unit ? item.quantity : null)));
      return row;
    });
    document.getElementById("semiTableBody").replaceChildren(...(semiRows.length ? semiRows : [emptyRow(3 + semiUnits.length, "暂无半成品记录")]));
  }

  /** 展示图片工序节点；processes 为后端关联状态；无返回值，仅在状态变化时替换节点。 */
  function renderProcesses(processes) {
    const signature = JSON.stringify(processes);
    if (signature === processCache) return;
    processCache = signature;
    const warning = processes.filter(item => item.alert).map(item => `${item.name}（${item.alert_label || item.status_label}）`).join("、");
    const warningElement = document.getElementById("flowWarning");
    warningElement.hidden = !warning;
    warningElement.textContent = warning ? "异常联动：" + warning : "";
    warningElement.title = warning;
    const items = processes.map((process, index) => {
      const card = node("li", "process-step" + (process.status === "construction" ? " is-construction" : process.alert ? " is-maintenance" : "") + (process.status === "fault" ? " is-fault" : ""));
      card.dataset.processId = process.id;
      card.dataset.state = process.status;
      card.title = process.alert_label || process.status_label;
      if (process.alert) {
        const alert = node("span", "process-alert", "!");
        alert.setAttribute("aria-hidden", "true");
        card.append(alert);
      }
      const illustration = node("img", "process-illustration");
      illustration.src = process.image_path;
      illustration.alt = "";
      illustration.width = 280;
      illustration.height = 226;
      card.append(illustration, node("strong", "", process.name), node("span", "step-status", process.status_label));
      if (index !== processes.length - 1) {
        // 连接任意一端停机时静止提示；其余连接继续向右流动。
        const stopped = !["running", "normal"].includes(process.status) || !["running", "normal"].includes(processes[index + 1].status);
        const connector = node("span", "process-connector" + (stopped ? " is-stopped" : "")
          + (stopped && !process.alert && !processes[index + 1].alert ? " is-construction" : ""));
        connector.dataset.state = stopped ? "stopped" : "running";
        connector.setAttribute("aria-hidden", "true");
        connector.innerHTML = '<svg viewBox="0 0 24 12" fill="none" stroke="currentColor" stroke-width="1.8"><path class="connector-track" d="M1 6h17"/><path class="connector-stream" d="M1 6h17"/><circle class="connector-spark" cx="2" cy="6" r="1.5"/><path d="m14 1 6 5-6 5" stroke-linejoin="round"/></svg>';
        card.append(connector);
      }
      return card;
    });
    document.getElementById("processFlow").replaceChildren(...items);
  }

  /** 绘制产量趋势；data 为后端完整数据；无返回值，图表实例在后续轮询中复用。 */
  function renderCharts(data) {
    const trendElement = document.getElementById("newTrendChart");
    const shipmentElement = document.getElementById("shipmentChart");
    if (!window.echarts) {
      trendElement.textContent = "图表组件加载失败，请刷新";
      shipmentElement.textContent = "图表组件加载失败，请刷新";
      return;
    }
    shipmentChart ||= echarts.init(shipmentElement);
    shipmentChart.setOption(model.buildShipmentOption(data.sales.ranking, shipmentElement.clientHeight), { notMerge: true });
    shipmentChart.resize();
    trendChart ||= echarts.init(trendElement);
    const trendOption = model.buildTrendOption(data.trend, trendElement.clientHeight < 280, data.lines);
    if (data.trend.reported_days === 0) {
      trendOption.graphic = { type: "text", left: "center", top: "middle", style: { text: "暂无产量记录", fill: "#a9c0d1", fontSize: 12 } };
    }
    trendChart.setOption(trendOption, { notMerge: true });
    trendChart.resize();
    const groups = model.trendGroups(data.trend);
    // 每条产线共用一个图例入口，颜色对应并排柱和实际折线，标准虚线统一使用亮绿色。
    const labels = data.trend.series.map(series => {
      const standard = data.lines.find(line => line.id === series.line_id)?.standard_qty;
      const suffix = Number.isFinite(standard) && standard >= 0
        ? ` · 标准 ${model.formatNumber(standard)}${model.unitLabel(series.unit || "件")}/日`
        : groups.length > 1 ? model.unitLabel(series.unit) : "";
      return { name: series.name + suffix, lineId: series.line_id, color: model.trendColor(series.line_id) };
    });
    document.getElementById("trendLegend").replaceChildren(...labels.map(item => {
      const label = node("span");
      label.title = item.name;
      if (item.lineId !== undefined) label.dataset.lineId = item.lineId;
      const mark = node("i", "legend-mark");
      mark.style.backgroundColor = item.color;
      label.append(mark, node("span", "legend-label", item.name));
      return label;
    }));
    setText("trendTotal", `本月累计：${groups.map(group => quantity(group.period_total, group.unit)).join(" / ")}`);
    document.getElementById("trendTotal").title = "按所选产线实际产量汇总；未录入产量按0计算";
  }

  /** 更新全局指标与销量；data 为后端完整数据；无返回值。 */
  function renderMetrics(data) {
    setText("orderQty", model.formatNumber(data.sales.order_qty));
    setText("shippedQty", model.formatNumber(data.sales.shipped_qty));
    setText("shipmentRate", model.formatPercent(data.sales.achievement_rate));
    setKpi("plannedQty", data.overview.planned_qty, "件");
    setKpi("actualQty", data.overview.actual_qty, "件");
    setText("completionRate", model.formatPercent(data.overview.completion_rate));
    setText("oeePercent", model.formatPercent(data.overview.oee_percent));
    setText("qualityPercent", model.formatPercent(data.overview.quality_percent));
    setKpi("averageOutput", data.overview.average_output, "件/时");
    setText("safetyDays", model.formatNumber(data.safety_days));
  }

  /** 以较小字号展示指标单位；id 为目标、value 为数值、unit 为单位；无返回值。 */
  function setKpi(id, value, unit) {
    const element = document.getElementById(id);
    element.replaceChildren(document.createTextNode(model.formatNumber(value)));
    if (value !== null && value !== undefined) element.append(node("span", "", model.unitLabel(unit)));
  }

  /** 读取已保存数据；无参数；返回 Promise，失败时保留最后成功画面并标记连接状态。 */
  async function refresh() {
    if (reading) return;
    reading = true;
    const status = document.getElementById("connectionStatus");
    try {
      const data = await BoardAPI.request("/api/dashboard", { timeout: 4000 });
      lastData = data;
      // 隐藏品牌但保留网格占位，标题居中位置不随开关变化。
      document.querySelector(".company-brand").style.visibility = data.show_logo === true ? "visible" : "hidden";
      serverOffset = Date.parse(data.server_time) - Date.now();
      renderLines(data.lines);
      renderInventory(data.inventory);
      renderMetrics(data);
      renderProcesses(data.processes);
      renderCharts(data);
      status.textContent = "数据已同步";
      status.classList.remove("is-error");
      status.title = "最近保存：" + data.updated_at;
      document.body.dataset.version = data.version;
      updateClock();
    } catch (error) {
      status.textContent = "连接中断";
      status.classList.add("is-error");
      status.title = error.message;
    } finally { reading = false; }
  }

  /** 更新时间；无参数；无返回值，采用服务端偏移与统一业务时区。 */
  function updateClock() {
    const now = new Date(Date.now() + serverOffset);
    const element = document.getElementById("currentTime");
    element.dateTime = now.toISOString();
    element.textContent = new Intl.DateTimeFormat("zh-CN", { timeZone: lastData?.timezone || "UTC", year: "numeric", month: "2-digit", day: "2-digit", weekday: "long", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(now);
  }

  /** 启动库存明细每秒十像素滚动；无参数；无返回值，无需按钮，手动操作后暂缓恢复。 */
  function startInventoryScroll() {
    // 首尾各停留四秒，给远距离查看明细留出阅读时间。
    const endPause = 4000;
    const states = [...document.querySelectorAll(".material-table-wrap")].map(element => {
      const state = { element, position: 0, lastTop: 0, focused: false, resumeAt: performance.now() + endPause, atBottom: false };
      element.addEventListener("focusin", () => { state.focused = true; });
      element.addEventListener("focusout", event => { state.focused = element.contains(event.relatedTarget); });
      for (const event of ["wheel", "touchstart", "touchmove"]) element.addEventListener(event, () => { state.resumeAt = performance.now() + 3000; }, { passive: true });
      return state;
    });
    let previous = performance.now();
    /** 移动一帧；now 为高精度时间戳；无返回值并继续安排下一帧。 */
    function tick(now) {
      const elapsed = Math.min(now - previous, 100) / 1000;
      previous = now;
      for (const state of states) {
        const element = state.element;
        const max = element.scrollHeight - element.clientHeight;
        if (Math.abs(element.scrollTop - state.lastTop) > 1) {
          state.position = element.scrollTop;
          state.resumeAt = now + 3000;
          state.atBottom = false;
        }
        state.lastTop = element.scrollTop;
        if (document.hidden || state.focused || max <= 0 || now < state.resumeAt) continue;
        if (state.atBottom) {
          state.position = 0;
          state.atBottom = false;
          state.resumeAt = now + endPause;
        } else {
          state.position = Math.min(state.position + elapsed * 10, max);
          if (state.position === max) { state.atBottom = true; state.resumeAt = now + endPause; }
        }
        element.scrollTop = state.position;
        state.lastTop = element.scrollTop;
      }
      requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
  }

  /** 同步画布大小；无参数；无返回值，避免观察图表内部画布导致布局循环。 */
  function resizeCharts() {
    trendChart?.resize();
    shipmentChart?.resize();
    // 缩放后按最新记录数重算绘图区，保持少量数据紧凑、满榜文字不重叠。
    if (shipmentChart && lastData) {
      shipmentChart.setOption(model.buildShipmentOption(lastData.sales.ranking, document.getElementById("shipmentChart").clientHeight), { notMerge: true });
    }
  }

  const observer = new ResizeObserver(resizeCharts);
  observer.observe(document.getElementById("newTrendChart"));
  observer.observe(document.getElementById("shipmentChart"));
  // 仅观察卡片区域，尺寸和字体加载变化后重新判断是否能共用对齐列。
  const lineObserver = new ResizeObserver(fitLineLayout);
  lineObserver.observe(document.getElementById("lineCards"));
  document.fonts.ready.then(fitLineLayout);
  if (typeof BroadcastChannel !== "undefined") {
    const channel = new BroadcastChannel("bigscreen-data");
    channel.onmessage = refresh;
  }
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
  startInventoryScroll();
  refresh();
  setInterval(refresh, 5000);
  setInterval(updateClock, 1000);
})();
