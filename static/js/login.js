"use strict";

/** 绑定登录表单；无参数；无返回值，成功后进入编辑页，失败保留输入并显示错误。 */
(() => {
  const form = document.getElementById("loginForm");
  const password = document.getElementById("password");
  const toggle = document.getElementById("togglePassword");
  const button = document.getElementById("loginButton");
  const errorBox = document.getElementById("loginError");
  const idleButtonText = button.textContent;
  const eyeOpen = document.getElementById("eye-open");
  const eyeClosed = document.getElementById("eye-closed");
  /** 切换密码可见性；无参数；无返回值，同步按钮的辅助功能状态。 */
  toggle.addEventListener("click", () => {
    const visible = password.type === "password";
    password.type = visible ? "text" : "password";
    eyeOpen.style.display = visible ? "none" : "block";
    eyeClosed.style.display = visible ? "block" : "none";
    toggle.setAttribute("aria-label", visible ? "隐藏密码" : "显示密码");
    toggle.setAttribute("aria-pressed", String(visible));
  });
  /** 提交登录；event 为表单提交事件；返回处理请求的 Promise，防止重复提交。 */
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (button.disabled) return;
    button.disabled = true;
    button.textContent = "正在登录…";
    errorBox.hidden = true;
    try {
      await BoardAPI.request("/api/auth/login", { method: "POST", body: {
        username: document.getElementById("username").value.trim(), password: password.value } });
      window.location.replace("/edit");
    } catch (error) {
      errorBox.textContent = error.message;
      errorBox.hidden = false;
      errorBox.focus();
    } finally {
      button.disabled = false;
      button.textContent = idleButtonText;
    }
  });
})();
