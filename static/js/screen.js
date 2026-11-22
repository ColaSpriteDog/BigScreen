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
    return value === null || value === undefined ? "—" : model.formatNumber(value) + unit;
  }

  /** 创建产线字段；label 为标题、value 为展示值、warning 为告警样式；返回字段行。 */
  function metric(label, value, warning = false) {
    const row = node("div");
    row.append(document.createTextNode(label), node("span", warning ? "val warn" : "val", value));
    return row;
  }

  /** 展示四条产线；lines 为后端计算结果；无返回值，状态和运行率始终来自停机记录。 */
  function renderLines(lines) {
    const cards = lines.map(line => {
      const card = node("section", "panel line-card" + (line.status === "running" ? "" : " is-maintenance") + (line.status === "fault" ? " is-fault" : ""));
      card.dataset.lineId = line.id;
      card.dataset.state = line.status;
      const header = node("div", "line-header");
      const title = node("span", "", line.name);
      title.title = line.name;
      const indicators = node("div", "line-indicators");
      indicators.append(node("span", "status-dot"), node("span", "line-index", line.code));
      header.append(title, indicators);
      const list = node("div", "data-list");
      const defectText = quantity(line.defect_qty) + (line.defect_rate === null ? "" : ` ${model.formatPercent(line.defect_rate)}`);
      list.append(metric("标准产量", quantity(line.standard_qty)), metric("今日产量", quantity(line.actual_qty)),
        metric("本月产量", quantity(line.monthly_qty)), metric("不良品数", defectText, line.defect_qty > 0));
      const status = metric("运行状态", `${line.status_label} ${model.formatPercent(line.runtime_rate)}`);
      status.lastElementChild.style.color = line.status === "running" ? "var(--green)" : "var(--orange)";
      status.title = "运行率按业务日 00:00 至当前时刻计算";
      list.append(status);
      const progress = node("div", "progress-bar");
      const fill = node("div", "progress-inner" + (line.status === "running" ? "" : " warn"));
      fill.style.width = `${line.runtime_rate ?? 0}%`;
      progress.append(fill);
      card.append(header, list, progress);
      return card;
    });
    document.getElementById("lineCards").replaceChildren(...cards);
  }

  /** 创建库存数值单元格；value 为数量、unit 为单位；返回独立数值单元格。 */
  function numericCell(value, unit = "") {
    const cell = node("td", "material-number");
    cell.append(node("span", "val", model.formatNumber(value)));
    if (unit && value !== null) cell.append(node("span", "inventory-unit", unit));
    return cell;
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
      element.replaceChildren(document.createTextNode(model.formatNumber(totals[0].quantity)), node("span", "unit", totals[0].unit));
    } else element.textContent = model.inventorySummary(totals);
  }

  /** 展示库存独立字段；inventory 为后端库存模块；无返回值，相同明细不重绘以保留滚动位置。 */
  function renderInventory(inventory) {
    renderSummary("rawTotal", inventory.raw_totals);
    renderSummary("semiTotal", inventory.semi_totals);
    setText("zeroCount", inventory.zero_count ? `${inventory.zero_count}项零库存` : "");
    const signature = JSON.stringify([inventory.raw, inventory.semi]);
    if (signature === inventoryCache) return;
    inventoryCache = signature;
    const materialRows = inventory.raw.map(item => {
      const row = node("tr", item.status === "zero" ? "is-shortage" : "");
      row.append(node("td", "material-name", item.material), node("td", "warehouse-field", item.warehouse),
        node("td", "material-spec", item.specification), node("td", "material-part", item.position),
        numericCell(item.quantity, item.unit), numericCell(item.weight_kg));
      const status = node("td", "material-status");
      status.append(node("span", item.status === "zero" ? "stock-warning" : "stock-available", item.status === "zero" ? "零库存" : "有库存"));
      row.append(status, numericCell(item.cuttable_qty));
      return row;
    });
    document.getElementById("rawTableBody").replaceChildren(...(materialRows.length ? materialRows : [emptyRow(8, "暂无原材料记录")]));
    const semiRows = inventory.semi.map((item, index, items) => {
      const row = node("tr");
      if (index === 0 || items[index - 1].category !== item.category) {
        let count = 1;
        while (index + count < items.length && items[index + count].category === item.category) count++;
        const category = node("td", "semi-group", item.category);
        category.rowSpan = count;
        row.append(category);
      }
      row.append(node("td", "semi-name", item.material), node("td", "warehouse-field", item.warehouse),
        node("td", "semi-spec", item.specification), numericCell(item.quantity, item.unit));
      return row;
    });
    document.getElementById("semiTableBody").replaceChildren(...(semiRows.length ? semiRows : [emptyRow(5, "暂无半成品记录")]));
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
      const card = node("li", "process-step" + (process.alert ? " is-maintenance" : "") + (process.status === "fault" ? " is-fault" : ""));
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
        const stopped = process.alert || processes[index + 1].alert;
        const connector = node("span", "process-connector" + (stopped ? " is-stopped" : ""));
        connector.dataset.state = stopped ? "stopped" : "running";
        connector.setAttribute("aria-hidden", "true");
        connector.innerHTML = '<svg viewBox="0 0 24 12" fill="none" stroke="currentColor" stroke-width="1.8"><path class="connector-track" d="M1 6h17"/><path class="connector-stream" d="M1 6h17"/><circle class="connector-spark" cx="2" cy="6" r="1.5"/><path d="m14 1 6 5-6 5" stroke-linejoin="round"/></svg>';
        card.append(connector);
      }
      return card;
    });
    document.getElementById("processFlow").replaceChildren(...items);
  }

  /** 绘制两张图；data 为后端完整数据；无返回值，图表实例在后续轮询中复用。 */
  function renderCharts(data) {
    const trendElement = document.getElementById("newTrendChart");
    const shipmentElement = document.getElementById("shipmentChart");
    if (!window.echarts) {
      trendElement.textContent = "图表组件加载失败，请刷新";
      shipmentElement.textContent = "图表组件加载失败，请刷新";
      return;
    }
    trendChart ||= echarts.init(trendElement);
    shipmentChart ||= echarts.init(shipmentElement);
    const trendOption = model.buildTrendOption(data.trend, trendElement.clientHeight < 280, data.lines);
    if (!data.trend.series.some(series => series.data.some(value => value !== null))) {
      trendOption.graphic = { type: "text", left: "center", top: "middle", style: { text: "暂无产量记录", fill: "#a9c0d1", fontSize: 12 } };
    }
    const shipmentOption = model.buildShipmentOption(data.sales.items);
    if (!data.sales.items.length) shipmentOption.graphic = { type: "text", left: "center", top: "middle", style: { text: "暂无出货记录", fill: "#a9c0d1", fontSize: 12 } };
    trendChart.setOption(trendOption, { notMerge: true });
    shipmentChart.setOption(shipmentOption, { notMerge: true });
    trendChart.resize();
    shipmentChart.resize();
    const labels = [{ name: "所选产线合计", className: "total", color: "#27536c" },
      ...data.trend.series.map(series => ({ name: series.name, lineId: series.line_id, color: model.trendColor(series.line_id), className: "" }))];
    document.getElementById("trendLegend").replaceChildren(...labels.map(item => {
      const label = node("span");
      label.title = item.name;
      if (item.lineId !== undefined) label.dataset.lineId = item.lineId;
      const mark = node("i", "legend-mark " + item.className);
      mark.style.backgroundColor = item.color;
      label.append(mark, node("span", "legend-label", item.name));
      return label;
    }));
    setText("trendTotal", `${data.trend.reported_days === 30 ? "30天累计" : "已录入累计"}：${quantity(data.trend.period_total)}`);
    document.getElementById("trendTotal").title = "按所选产线汇总；任一所选产线未录入的日期不计入合计";
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
    if (value !== null && value !== undefined) element.append(node("span", "", unit));
  }

  /** 读取已保存数据；无参数；返回 Promise，失败时保留最后成功画面并标记连接状态。 */
  async function refresh() {
    if (reading) return;
    reading = true;
    const status = document.getElementById("connectionStatus");
    try {
      const data = await BoardAPI.request("/api/dashboard", { timeout: 4000 });
      lastData = data;
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

  /** 启动溢出库存的每秒十像素滚动；无参数；无返回值，无需按钮，手动操作后暂缓恢复。 */
  function startInventoryScroll() {
    const states = [...document.querySelectorAll(".material-table-wrap")].map(element => {
      const state = { element, position: 0, lastTop: 0, focused: false, resumeAt: performance.now() + 2000, atBottom: false };
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
          state.resumeAt = now + 2000;
        } else {
          state.position = Math.min(state.position + elapsed * 10, max);
          if (state.position === max) { state.atBottom = true; state.resumeAt = now + 2000; }
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
  }

  const observer = new ResizeObserver(resizeCharts);
  observer.observe(document.getElementById("newTrendChart"));
  observer.observe(document.getElementById("shipmentChart"));
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
