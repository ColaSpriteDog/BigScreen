"use strict";

/** 构建同源接口模块；无参数；返回请求与页面更新通知函数。 */
const BoardAPI = (() => {
  /** 请求 JSON 接口；path 为同源路径、options 可含 method/body/timeout；返回解析结果或抛出中文错误。 */
  async function request(path, options = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), options.timeout || 10000);
    try {
      const response = await fetch(path, { method: options.method || "GET", cache: "no-store", signal: controller.signal,
        // 文件上传由浏览器生成 multipart 边界，普通接口继续发送 JSON。
        headers: options.formData ? undefined : { "Content-Type": "application/json" },
        body: options.formData || (options.body === undefined ? undefined : JSON.stringify(options.body)) });
      let result;
      try { result = await response.json(); }
      catch (_) { throw new Error("服务返回格式不正确，请稍后重试"); }
      if (!response.ok) {
        const error = new Error(result.error || "请求失败");
        error.status = response.status;
        throw error;
      }
      return result;
    } catch (error) {
      if (error.name === "AbortError") throw new Error("请求超时，请检查服务连接");
      if (error instanceof TypeError) throw new Error("无法连接服务，请检查网络");
      throw error;
    } finally { clearTimeout(timer); }
  }

  /** 通知同一浏览器的大屏刷新；无参数；无返回值，跨设备仍由定时读取更新。 */
  function notifySaved() {
    if (typeof BroadcastChannel !== "undefined") {
      const channel = new BroadcastChannel("bigscreen-data");
      channel.postMessage("saved");
      channel.close();
    }
  }

  return Object.freeze({ request, notifySaved });
})();

if (typeof module !== "undefined" && module.exports) module.exports = BoardAPI;
else window.BoardAPI = BoardAPI;
