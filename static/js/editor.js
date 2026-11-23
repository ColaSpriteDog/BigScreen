"use strict";

/** 启动编辑页面；无参数；无返回值，缺少依赖时保留明确的加载错误。 */
(() => {
  if (!window.Vue || !window.ElementPlus || !window.BoardAPI) {
    document.getElementById("bootStatus").textContent = "编辑组件加载失败，请刷新页面";
    return;
  }

  /** 深复制接口数据；value 为 JSON 数据；返回独立草稿，避免修改缓存。 */
  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  /** 统计草稿差异；current、saved 为当前值和读取基线；返回修改字段数，新增或删除一行计一次。 */
  function countChanges(current, saved) {
    if (current === saved || (current == null && saved == null)) return 0;
    if (Array.isArray(current) && Array.isArray(saved)) {
      const previous = new Map(saved.map((item, index) => [item._key ?? item.id ?? index, item]));
      let total = 0;
      current.forEach((item, index) => {
        const key = item._key ?? item.id ?? index;
        total += previous.has(key) ? countChanges(item, previous.get(key)) : 1;
        previous.delete(key);
      });
      return total + previous.size;
    }
    if (current && saved && typeof current === "object" && typeof saved === "object") {
      return [...new Set([...Object.keys(current), ...Object.keys(saved)])]
        .filter(key => !["_key", "_audit", "_configuration_audit"].includes(key)).reduce((total, key) => total + countChanges(current[key], saved[key]), 0);
    }
    return 1;
  }

  const application = Vue.createApp({
    /** 创建页面状态；无参数；返回表单草稿、版本和加载状态。 */
    data() {
      return { activeTab: "production", ready: false, moduleReady: false, loading: false, saving: false,
        modules: [
          { id: "production", label: "生产日报", saveLabel: "保存日报", icon: "M3 21V10l6 4V7l6 4V3h6v18ZM7 17h1m4 0h1m4 0h1" },
          { id: "inventory", label: "库存明细", saveLabel: "保存库存", icon: "m12 2 10 5v10l-10 5-10-5V7ZM2 7l10 5 10-5M12 12v10M7 4.5l10 5" },
          { id: "sales", label: "销售出货", saveLabel: "保存出货", icon: "M3 3v18h18M6 15l5-5 4 3 6-8M16 5h5v5" },
          { id: "settings", label: "流程与设置", saveLabel: "保存设置", icon: "M3 6h18M3 12h18M3 18h18M7 3v6M17 9v6M9 15v6" },
          { id: "employees", label: "员工管理", saveLabel: "保存员工", icon: "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M16 3a4 4 0 0 1 0 8M22 21v-2a4 4 0 0 0-3-3.87M13 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0" },
        ],
        selectedDate: "", loadedDate: "", selectedMonth: "", loadedMonth: "", businessDate: "", timezone: "UTC",
        serverTime: "", errorMessage: "", savedAt: "", version: 0,
        versions: { production: 0, inventory: 0, sales: 0, settings: 0 },
        dirty: { production: false, inventory: false, sales: false, settings: false }, keyCounter: 0, partSearch: "",
        baselines: {}, employees: { items: [] }, currentEmployee: null,
        production: { lines: [], overview: {}, downtimes: [] }, inventory: { raw: [], semi: [] },
        inventoryImport: null, importDialogVisible: false,
        sales: { order_qty: null, items: [] }, settingsForm: { timezone: "UTC", safety_start_date: null, lines: [], processes: [] } };
    },
    computed: {
      /** 读取当前导航标题与保存文案；无参数；返回选中模块的配置。 */
      currentModule() { return this.modules.find(item => item.id === this.activeTab); },
      /** 计算所选日报的星期；无参数；返回与日期对应的中文星期。 */
      selectedWeekday() { return this.selectedDate ? new Intl.DateTimeFormat("zh-CN", { weekday: "long", timeZone: "UTC" }).format(new Date(this.selectedDate + "T12:00:00Z")) : ""; },
      /** 判断当前模块是否修改；无参数；返回布尔值。 */
      isDirty() { return this.changeCount > 0; },
      /** 统计当前模块的实际修改；无参数；返回相对于最近读取数据的字段或增删行数量。 */
      changeCount() { return this.dirty[this.activeTab] ? countChanges(this.moduleDraft(this.activeTab), this.baselines[this.activeTab]) : 0; },
      /** 显示当前读写与草稿状态；无参数；返回底部保存栏的反馈文本。 */
      saveStatusLabel() {
        if (this.saving) return "正在保存…";
        if (this.loading) return "正在读取…";
        if (this.isDirty) return this.changeCount + " 处修改未保存";
        return this.moduleReady ? "所有修改已保存" : "数据未加载";
      },
      /** 生成保存状态；无参数；返回业务时区中的最近保存时间。 */
      savedLabel() {
        return this.savedAt ? "最近保存 " + new Intl.DateTimeFormat("zh-CN", { timeZone: this.timezone, hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(new Date(this.savedAt)) : "尚无保存记录";
      },
      /** 汇总原材料；无参数；返回按单位分组的预览文本。 */
      rawSummary() { return this.stockSummary(this.inventory.raw); },
      /** 汇总半成品；无参数；返回按单位分组的预览文本。 */
      semiSummary() { return this.stockSummary(this.inventory.semi); },
      /** 统计原材料零库存；无参数；返回数量为零的记录数，未填写数量不计入。 */
      rawZeroCount() { return this.inventory.raw.filter(item => item.quantity === 0).length; },
      /** 统计趋势图选中产线；无参数；返回选中数量，用于配置页反馈。 */
      trendLineCount() { return this.settingsForm.lines.filter(line => line.show_in_trend).length; },
      /** 复用已有原材料仓库；无参数；返回去重后的下拉选项。 */
      rawWarehouses() { return [...new Set(["原材料仓库", ...this.inventory.raw.map(item => item.warehouse).filter(Boolean)])]; },
      /** 复用已有半成品仓库；无参数；返回去重后的下拉选项。 */
      semiWarehouses() { return [...new Set(["半成品仓库", ...this.inventory.semi.map(item => item.warehouse).filter(Boolean)])]; },
      /** 计算完整出货预览；无参数；返回全部件号的数量总和或空值。 */
      shippedTotal() { return this.sales.items.length ? this.sales.items.reduce((sum, item) => sum + (Number(item.quantity) || 0), 0) : null; },
      /** 筛选出货草稿；无参数；返回与搜索词匹配的原行引用。 */
      filteredShipments() {
        const search = this.partSearch.trim().toLowerCase();
        return this.sales.items.filter(item => !search || item.part_no.toLowerCase().includes(search));
      },
    },
    methods: {
      /** 格式化数值；value 为字段值；返回展示文本。 */
      fmt(value) { return BoardModel.formatNumber(value); },
      /** 读取吨数输入；value 为千克重量；返回吨数，保留原字段避免改写既有数据。 */
      weightTonnes(value) { return BoardModel.weightTonnes(value); },
      /** 格式化吨数预览；value 为千克重量；返回最多五位小数的吨数文本。 */
      fmtWeight(value) { return BoardModel.formatNumber(this.weightTonnes(value), 5); },
      /** 更新重量草稿；row 为原材料行、value 为输入吨数；无返回值，保存时仍提交千克。 */
      updateWeight(row, value) { row.weight_kg = BoardModel.weightKilograms(value); this.markDirty("inventory"); },
      /** 读取固定产线颜色；id 为产线标识；返回与大屏趋势图一致的颜色值。 */
      lineColor(id) { return BoardModel.trendColor(id); },
      /** 格式化百分比；value 为百分数；返回展示文本。 */
      percent(value) { return BoardModel.formatPercent(value); },
      /** 预览比率；actual、planned 为分子分母；返回百分数或缺失值。 */
      rate(actual, planned) { return actual == null || planned == null || Number(planned) === 0 ? null : Number(actual) / Number(planned) * 100; },
      /** 预览库存分组；items 为库存草稿；返回数量与单位文本。 */
      stockSummary(items) {
        const totals = new Map();
        for (const item of items) if (item.quantity != null && item.unit) totals.set(item.unit, (totals.get(item.unit) || 0) + Number(item.quantity));
        return BoardModel.inventorySummary([...totals].map(([unit, quantity]) => ({ unit, quantity })));
      },
      /** 生成草稿行键；无参数；返回页面内唯一键。 */
      newKey() { return `draft-${++this.keyCounter}`; },
      /** 给接口记录分配行键；items 为记录列表；返回独立表格草稿。 */
      keyed(items) { return clone(items).map(item => ({ ...item, _key: this.newKey() })); },
      /** 标记修改；module 为模块名；无返回值。 */
      markDirty(module) { this.dirty[module] = true; },
      /** 读取模块草稿；module 为模块名；返回对应业务表单对象，不包含页面搜索和加载状态。 */
      moduleDraft(module) { return module === "settings" ? this.settingsForm : this[module]; },
      /** 查找已读取的行；module、group 为模块和列表名，row 为当前行；返回原始行或缺失值。 */
      savedRow(module, group, row) {
        return this.baselines[module]?.[group]?.find(item => row._key ? item._key === row._key : item.id === row.id);
      },
      /** 判断字段是否修改；module、group、field 定位字段，row 可指定列表行；返回是否需要强调。 */
      hasChanged(module, group, field, row = null) {
        if (!this.baselines[module]) return false;
        const current = row || (group ? this.moduleDraft(module)[group] : this.moduleDraft(module));
        const saved = row ? this.savedRow(module, group, row) : (group ? this.baselines[module][group] : this.baselines[module]);
        return !saved || countChanges(current[field], saved[field]) > 0;
      },
      /** 标记表格行状态；module、group 定位列表，row 为当前行；返回修改及零库存样式名。 */
      rowClass(module, group, row) {
        const changed = this.baselines[module] && countChanges(row, this.savedRow(module, group, row)) > 0;
        const zero = module === "inventory" && group === "raw" && row.quantity === 0;
        return [changed ? "is-edited" : "", zero ? "is-zero" : ""].filter(Boolean).join(" ");
      },
      /** 标记可编辑单元格；module、group 定位列表，cell 包含行与字段列；返回修改强调样式名。 */
      cellClass(module, group, cell) {
        return cell.column.property && this.hasChanged(module, group, cell.column.property, cell.row) ? "is-changed" : "";
      },
      /** 定位请求错误；无参数；返回 DOM 更新后的 Promise，使长表错误进入视口并获得键盘焦点。 */
      async focusError() {
        await this.$nextTick();
        const element = this.$refs.requestError?.$el;
        if (element) {
          element.focus({ preventScroll: true });
          element.scrollIntoView({ block: "center", behavior: "instant" });
        }
      },
      /** 应用接口快照；snapshot 为数据、module 为目标、initial 表示首次；无返回值。 */
      applySnapshot(snapshot, module, initial = false) {
        this.version = snapshot.version;
        this.businessDate = snapshot.business_date;
        this.timezone = snapshot.settings.timezone;
        this.serverTime = snapshot.server_time;
        this.savedAt = snapshot.version ? snapshot.updated_at : "";
        if (initial || module === "production") {
          this.production = clone(snapshot.production);
          this.production.downtimes = this.keyed(snapshot.production.downtimes);
          this.selectedDate = this.loadedDate = snapshot.date;
        }
        if (initial || module === "inventory") this.inventory = { raw: this.keyed(snapshot.inventory.raw), semi: this.keyed(snapshot.inventory.semi) };
        if (initial || module === "sales") {
          this.sales = { order_qty: snapshot.sales.order_qty, _audit: clone(snapshot.sales._audit), items: this.keyed(snapshot.sales.items) };
          this.selectedMonth = this.loadedMonth = snapshot.month;
          this.partSearch = "";
        }
        if (initial || module === "settings") this.settingsForm = { timezone: snapshot.settings.timezone,
          safety_start_date: snapshot.settings.safety_start_date,
          _audit: clone(snapshot.settings._audit),
          lines: clone(snapshot.production.lines).map(line => ({ ...line, _audit: line._configuration_audit, show_in_trend: Boolean(line.show_in_trend) })),
          processes: clone(snapshot.processes.filter(item => item.id === 1 || item.id === 5)) };
        this.versions[module] = snapshot.version;
        for (const item of this.modules) if (initial || item.id === module) {
          this.baselines[item.id] = clone(this.moduleDraft(item.id));
          this.dirty[item.id] = false;
        }
        this.ready = this.moduleReady = true;
      },
      /** 加载当前模块；module 为模块名、initial 表示首次；返回成功与否，失败保留草稿。 */
      async loadModule(module, initial = false) {
        this.loading = true;
        this.moduleReady = false;
        this.errorMessage = "";
        try {
          if (initial) {
            const account = await BoardAPI.request("/api/auth/session");
            this.currentEmployee = account.employee;
          }
          if (module === "employees") {
            const snapshot = await BoardAPI.request("/api/employees");
            this.employees = { items: this.keyed(snapshot.employees) };
            this.versions.employees = snapshot.version;
            this.savedAt = snapshot.updated_at;
            this.baselines.employees = clone(this.employees);
            this.dirty.employees = false;
            this.moduleReady = true;
            return true;
          }
          const query = new URLSearchParams();
          if (this.selectedDate) query.set("date", this.selectedDate);
          if (this.selectedMonth) query.set("month", this.selectedMonth);
          const snapshot = await BoardAPI.request("/api/editor?" + query);
          this.applySnapshot(snapshot, module, initial);
          return true;
        } catch (error) {
          if (error.status === 401) this.handleSessionExpired();
          this.errorMessage = error.message;
          return false;
        } finally {
          this.loading = false;
          if (this.errorMessage && !this.saving) await this.focusError();
        }
      },
      /** 询问是否放弃草稿；无参数；返回用户是否选择放弃。 */
      async discardDraft() {
        if (!this.isDirty) return true;
        try {
          await ElementPlus.ElMessageBox.confirm("当前模块有未保存修改。", "未保存的修改", { confirmButtonText: "放弃修改", cancelButtonText: "继续编辑", type: "warning", closeOnClickModal: false });
          return true;
        } catch (_) { return false; }
      },
      /** 控制模块离开；无参数；返回能否切换，避免丢失尚未保存的输入。 */
      async canLeaveTab() {
        if (this.loading || this.saving) return false;
        if (!this.isDirty) {
          this.loading = true;
          this.moduleReady = false;
          return true;
        }
        if (!await this.discardDraft()) return false;
        this.dirty[this.activeTab] = false;
        this.loading = true;
        this.moduleReady = false;
        return true;
      },
      /** 切换侧栏模块；module 为目标模块名；返回 Promise，确认放弃后才切换并读取。 */
      async switchModule(module) {
        if (module === this.activeTab || !await this.canLeaveTab()) return;
        this.activeTab = module;
        window.scrollTo({ top: 0, behavior: "instant" });
        await this.loadModule(module);
      },
      /** 处理模块键盘导航；event 为按键、module 为焦点模块；返回 Promise，方向键及首尾键选择模块。 */
      async navigateModules(event, module) {
        const index = this.modules.findIndex(item => item.id === module);
        const offsets = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 };
        let next;
        if (event.key === "Home") next = 0;
        else if (event.key === "End") next = this.modules.length - 1;
        else if (offsets[event.key]) next = (index + offsets[event.key] + this.modules.length) % this.modules.length;
        else return;
        event.preventDefault();
        await this.switchModule(this.modules[next].id);
        await this.$nextTick();
        document.getElementById("tab-" + this.activeTab).focus();
      },
      /** 手动重载；无参数；返回 Promise，放弃草稿需由用户确认。 */
      async reloadCurrent() {
        if (this.loading || this.saving) return;
        if (await this.discardDraft()) await this.loadModule(this.activeTab);
      },
      /** 切换日报日期；value 为 YYYY-MM-DD；返回 Promise，失败时恢复原日期。 */
      async changeDate(value) {
        if (!value || !await this.discardDraft()) { this.selectedDate = this.loadedDate; return; }
        if (!await this.loadModule("production")) this.selectedDate = this.loadedDate;
      },
      /** 按天移动日期；offset 为天数；返回切换日期的 Promise。 */
      async stepDate(offset) {
        const date = new Date(this.selectedDate + "T12:00:00Z");
        date.setUTCDate(date.getUTCDate() + offset);
        const next = date.toISOString().slice(0, 10);
        this.selectedDate = next;
        await this.changeDate(next);
      },
      /** 回到业务日今天；无参数；返回切换 Promise。 */
      async goToday() { this.selectedDate = this.businessDate; await this.changeDate(this.selectedDate); },
      /** 切换销售月份；value 为 YYYY-MM；返回 Promise，取消时恢复原月份。 */
      async changeMonth(value) {
        if (!value || !await this.discardDraft()) { this.selectedMonth = this.loadedMonth; return; }
        if (!await this.loadModule("sales")) this.selectedMonth = this.loadedMonth;
      },
      /** 构造当前模块请求；无参数；返回接口路径与业务载荷。 */
      saveRequest() {
        const module = this.activeTab;
        const version = this.versions[module];
        if (module === "employees") return { path: "/api/employees", body: { version, employees: clone(this.employees.items) } };
        if (module === "production") return { path: "/api/production/" + this.selectedDate, body: { version, ...clone(this.production) } };
        if (module === "inventory") return { path: "/api/inventory", body: { version, ...clone(this.inventory) } };
        if (module === "sales") return { path: "/api/sales/" + this.selectedMonth, body: { version, order_qty: this.sales.order_qty, items: clone(this.sales.items) } };
        if (!this.settingsForm.lines.some(line => line.show_in_trend)) throw new Error("趋势图至少选择一条产线");
        return { path: "/api/settings", body: { version, ...clone(this.settingsForm) } };
      },
      /** 保存当前模块；无参数；返回 Promise，失败保留全部草稿并展示错误。 */
      async saveModule() {
        if (this.saving || !this.moduleReady) return;
        this.saving = true;
        this.errorMessage = "";
        try {
          const request = this.saveRequest();
          const result = await BoardAPI.request(request.path, { method: "PUT", body: request.body });
          this.savedAt = result.updated_at;
          this.dirty[this.activeTab] = false;
          if (this.activeTab === "employees" || !this.currentEmployee) {
            const account = await BoardAPI.request("/api/auth/session");
            this.currentEmployee = account.employee;
          }
          BoardAPI.notifySaved();
          ElementPlus.ElMessage.success("保存成功");
          await this.loadModule(this.activeTab);
        } catch (error) {
          if (error.status === 401) this.handleSessionExpired();
          this.errorMessage = error.message;
        } finally {
          this.saving = false;
          if (this.errorMessage) await this.focusError();
        }
      },
      /** 匹配导入库存；items 为解析记录、kind 为 raw/semi；返回保留已有标识和仓库的完整快照。 */
      prepareImportedStock(items, kind) {
        const fields = kind === "raw" ? ["material", "specification", "position", "unit"] : ["category", "material", "specification", "unit"];
        const identity = item => JSON.stringify(fields.map(field => String(item[field] ?? "").replace(/\s+/g, "")));
        const existing = new Map();
        for (const item of this.inventory[kind]) {
          const key = identity(item);
          if (!existing.has(key)) existing.set(key, []);
          existing.get(key).push(item);
        }
        return items.map(item => {
          const candidates = existing.get(identity(item)) || [];
          if (candidates.length > 1) throw new Error(`第 ${item.source_row} 行物料对应多个仓库或重复草稿，请先明确仓库后再导入`);
          const { source_row, ...values } = item;
          const previous = candidates[0];
          // 同一物料继续使用原 ID，保存时才能保留首次录入人和未变化行的修改记录。
          return previous ? { ...previous, ...values, specification: previous.specification, warehouse: previous.warehouse } : { ...values, _key: this.newKey() };
        });
      },
      /** 解析所选 Excel；event 为文件选择事件；返回 Promise，成功仅打开预览，失败保留原草稿。 */
      async previewInventoryFile(event) {
        const file = event.target.files?.[0];
        event.target.value = "";
        if (!file || this.loading || this.saving || !this.moduleReady) return;
        this.inventoryImport = null;
        this.errorMessage = "";
        if (!/\.(xls|xlsx)$/i.test(file.name) || file.size > 160 * 1024 * 1024) {
          this.errorMessage = "请选择不超过 160 MiB 的 .xls 或 .xlsx 文件";
          await this.focusError();
          return;
        }
        this.loading = true;
        try {
          const body = new FormData();
          body.append("file", file);
          const result = await BoardAPI.request("/api/inventory/preview", { method: "POST", formData: body, timeout: 120000 });
          const raw = this.prepareImportedStock(result.raw, "raw");
          const semi = this.prepareImportedStock(result.semi, "semi");
          this.inventoryImport = { ...result, filename: file.name, prepared: { raw, semi } };
          this.importDialogVisible = true;
        } catch (error) {
          if (error.status === 401) this.handleSessionExpired();
          this.errorMessage = error.message;
        } finally {
          this.loading = false;
          if (this.errorMessage) await this.focusError();
        }
      },
      /** 应用预览到库存草稿；无参数；无返回值，按整份快照替换两类库存，仍需手动保存。 */
      applyInventoryImport() {
        if (!this.inventoryImport) return;
        this.inventory = clone(this.inventoryImport.prepared);
        this.markDirty("inventory");
        this.importDialogVisible = false;
        ElementPlus.ElMessage.success("已应用到草稿，请核对后保存库存");
      },
      /** 释放导入预览；无参数；无返回值，不改变库存草稿。 */
      clearInventoryImport() { this.inventoryImport = null; },
      /** 新增原材料草稿；无参数；无返回值，数量保持未填写。 */
      addRaw() {
        this.inventory.raw.push({ _key: this.newKey(), material: "", warehouse: this.rawWarehouses[0], specification: "", position: "", quantity: null, unit: "卷", weight_kg: null, cuttable_qty: null });
        this.markDirty("inventory");
      },
      /** 新增员工草稿；无参数；无返回值，新员工默认启用并需填写账号、姓名和密码。 */
      addEmployee() {
        this.employees.items.push({ _key: this.newKey(), username: "", name: "", password: "", enabled: true });
        this.markDirty("employees");
      },
      /** 删除员工草稿；row 为目标员工；返回 Promise，确认后移除并在保存时生效。 */
      async removeEmployee(row) {
        if (row.id === this.currentEmployee?.id) return;
        try {
          await ElementPlus.ElMessageBox.confirm("保存后将删除此员工，该员工将无法登录。", "删除员工", { confirmButtonText: "删除", cancelButtonText: "取消", type: "warning" });
          this.employees.items.splice(this.employees.items.indexOf(row), 1);
          this.markDirty("employees");
        } catch (_) { /* 取消时保留员工。 */ }
      },
      /** 处理会话失效；无参数；无返回值，有草稿时保留页面并提供重新登录入口。 */
      handleSessionExpired() {
        if (this.isDirty) {
          this.currentEmployee = null;
          ElementPlus.ElMessage.warning("登录已失效，请重新登录后再保存；当前草稿已保留");
        } else window.location.replace("/login");
      },
      /** 退出当前账号；无参数；返回 Promise，先确认放弃草稿再清除会话。 */
      async logout() {
        if (this.loading || this.saving || !await this.discardDraft()) return;
        try {
          await BoardAPI.request("/api/auth/logout", { method: "POST" });
          this.dirty[this.activeTab] = false;
          window.location.replace("/login");
        } catch (error) {
          if (error.status === 401) { this.dirty[this.activeTab] = false; window.location.replace("/login"); }
          else { this.errorMessage = error.message; await this.focusError(); }
        }
      },
      /** 新增半成品草稿；无参数；无返回值。 */
      addSemi() {
        this.inventory.semi.push({ _key: this.newKey(), category: "左件", material: "", warehouse: this.semiWarehouses[0], specification: "", quantity: null, unit: "件" });
        this.markDirty("inventory");
      },
      /** 复制库存行；kind 为 raw/semi，index 为行号；无返回值，新行获得独立标识。 */
      copyRow(kind, index) {
        const item = clone(this.inventory[kind][index]);
        delete item.id;
        delete item._audit;
        item._key = this.newKey();
        this.inventory[kind].splice(index + 1, 0, item);
        this.markDirty("inventory");
      },
      /** 删除草稿行；kind 为库存或停机类型，index 为行号；无返回值，保存后才更新数据库。 */
      removeRow(kind, index) {
        if (kind === "downtimes") { this.production.downtimes.splice(index, 1); this.markDirty("production"); }
        else { this.inventory[kind].splice(index, 1); this.markDirty("inventory"); }
      },
      /** 新增件号草稿；无参数；无返回值，并清空搜索使新行可见。 */
      addShipment() {
        this.partSearch = "";
        this.sales.items.push({ _key: this.newKey(), part_no: "", quantity: null });
        this.markDirty("sales");
      },
      /** 删除指定出货草稿；item 为原行引用；无返回值。 */
      removeShipment(item) {
        this.sales.items.splice(this.sales.items.indexOf(item), 1);
        this.markDirty("sales");
      },
      /** 新增停机时段；无参数；无返回值，按选定日期初始化开始时间。 */
      addDowntime() {
        this.production.downtimes.push({ _key: this.newKey(), line_id: this.production.lines[0].id,
          start_at: this.selectedDate + "T08:00:00", end_at: null, kind: "stopped", note: "" });
        this.markDirty("production");
      },
      /** 定位停机表格；无参数；无返回值。 */
      scrollToDowntimes() { document.getElementById("downtimeSection").scrollIntoView({ behavior: "smooth", block: "start" }); },
      /** 开始即时停机；line 为产线、kind 为停机类型；返回原子保存 Promise。 */
      async startStop(line, kind) { await this.quickStop("/api/downtime/start", { line_id: line.id, kind }); },
      /** 恢复产线运行；line 含未结束记录ID；返回原子保存 Promise。 */
      async finishStop(line) { await this.quickStop(`/api/downtime/${line.open_downtime_id}/finish`, {}); },
      /** 执行即时停机操作；path/body 为接口及参数；返回 Promise，已有草稿时保留草稿并提示。 */
      async quickStop(path, body) {
        if (this.isDirty) { ElementPlus.ElMessage.warning("请先保存当前修改，再执行即时停机操作"); return; }
        if (this.saving || !this.moduleReady) return;
        this.saving = true;
        this.errorMessage = "";
        try {
          const result = await BoardAPI.request(path, { method: "POST", body: { version: this.versions.production, ...body } });
          this.savedAt = result.updated_at;
          BoardAPI.notifySaved();
          await this.loadModule("production");
          ElementPlus.ElMessage.success("产线状态已更新");
        } catch (error) { if (error.status === 401) this.handleSessionExpired(); this.errorMessage = error.message; }
        finally {
          this.saving = false;
          if (this.errorMessage) await this.focusError();
        }
      },
      /** 防止关闭时丢失草稿；event 为浏览器事件；无返回值。 */
      preventUnload(event) { if (this.isDirty) { event.preventDefault(); event.returnValue = ""; } },
    },
    /** 挂载编辑器；无参数；无返回值并读取当前业务日期的数据。 */
    mounted() {
      document.getElementById("bootStatus").hidden = true;
      this.loadModule("production", true);
      window.addEventListener("beforeunload", this.preventUnload);
    },
  });
  application.component("record-audit", {
    props: { meta: Object, timezone: { type: String, default: "UTC" }, prefix: { type: String, default: "" } },
    computed: {
      /** 判断是否有可查的人员记录；无参数；返回是否显示详细浮层。 */
      recorded() { return this.meta?.state === "saved"; },
      /** 生成紧凑修改信息；无参数；返回姓名和业务时区时间，历史及草稿显示对应状态。 */
      label() {
        if (!this.meta || this.meta.state === "unsaved") return "未保存";
        if (!this.recorded) return "历史数据";
        return this.prefix + this.meta.modified_by_name + " · " + this.formatTime(this.meta.modified_at, false);
      },
      /** 生成只读人员详情；无参数；返回首次录入与最后修改两行完整信息。 */
      details() {
        if (!this.recorded) return [];
        return [this.meta.created_at ? "录入：" + this.meta.created_by_name + " · " + this.formatTime(this.meta.created_at, true) : "录入：历史数据",
          "修改：" + this.meta.modified_by_name + " · " + this.formatTime(this.meta.modified_at, true)];
      },
    },
    methods: {
      /** 按业务时区显示时间；value 为 ISO 时间，full 控制是否包含年份秒数；返回格式化文本。 */
      formatTime(value, full) {
        return new Intl.DateTimeFormat("zh-CN", { timeZone: this.timezone, ...(full ? { year: "numeric", second: "2-digit" } : {}),
          month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(value));
      },
    },
    template: `<el-tooltip :disabled="!recorded" :trigger="['hover','focus','click']" effect="light" placement="top" :show-after="150">
      <template #content><div class="audit-details"><div v-for="line in details" :key="line">{{line}}</div></div></template>
      <span class="record-audit" :tabindex="recorded?0:undefined" :aria-label="recorded?details.join('；'):label">{{label}}</span>
    </el-tooltip>`,
  });
  application.use(ElementPlus, { locale: window.ElementPlusLocaleZhCn });
  application.mount("#editorApp");
})();
