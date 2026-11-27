using System;
using System.Drawing;
using System.Net;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;
using Microsoft.Win32;

namespace BigScreen.Display
{
    internal sealed class DisplayWindow : Form
    {
        private readonly DisplaySettings settings;
        private readonly string screenUrl;
        private readonly string profile;
        private readonly EventWaitHandle heartbeat;
        private readonly System.Windows.Forms.Timer timer;
        private readonly WebView2 browser;
        private readonly Label waiting;
        private bool probing, ready, navigating, authorized, prompting;
        private bool exitHotKeyRegistered;
        private const int ExitHotKeyId = 1;
        private int ticks, rendererFailures;

        /// <summary>创建无边框展示窗口；参数为配置、用户目录和心跳事件；页面地址沿用 /screen。</summary>
        internal DisplayWindow(DisplaySettings configuration, string directory, EventWaitHandle pulse)
        {
            settings = configuration;
            profile = directory;
            heartbeat = pulse;
            screenUrl = settings.ScreenUrl(AppDomain.CurrentDomain.BaseDirectory);
            Text = "BigScreen 大屏展示";
            FormBorderStyle = FormBorderStyle.None;
            AutoScaleMode = AutoScaleMode.None;
            StartPosition = FormStartPosition.Manual;
            BackColor = Color.FromArgb(7, 17, 31);
            KeyPreview = true;
            // 默认置顶防止其他窗口遮挡，维护人员可在展示设置中取消。
            TopMost = settings.KeepOnTop;
            browser = new WebView2 { Dock = DockStyle.Fill, Visible = false, DefaultBackgroundColor = BackColor };
            waiting = new Label { Dock = DockStyle.Fill, ForeColor = Color.White, BackColor = BackColor,
                TextAlign = ContentAlignment.MiddleCenter, Font = new Font("Microsoft YaHei UI", 20),
                Text = "正在连接大屏服务…" };
            Controls.Add(browser);
            Controls.Add(waiting);
            timer = new System.Windows.Forms.Timer { Interval = 1000 };
            timer.Tick += Tick;
            browser.KeyDown += BrowserKeyDown;
            Shown += InitializeBrowser;
            SystemEvents.DisplaySettingsChanged += DisplayChanged;
            PlaceOnScreen();
            timer.Start();
        }

        /// <summary>定位目标屏幕；无参数；优先保存的设备，其次非主屏，最后主屏。</summary>
        private void PlaceOnScreen()
        {
            Screen selected = null;
            foreach (Screen screen in Screen.AllScreens)
                if (screen.DeviceName == settings.MonitorDevice) selected = screen;
            if (selected == null)
                foreach (Screen screen in Screen.AllScreens)
                    if (!screen.Primary) { selected = screen; break; }
            Bounds = (selected ?? Screen.PrimaryScreen).Bounds;
        }

        /// <summary>适应显示器插拔或分辨率变化；参数为系统事件；无返回值，在界面线程重新定位。</summary>
        private void DisplayChanged(object sender, EventArgs args)
        {
            if (!IsDisposed && IsHandleCreated) BeginInvoke(new Action(PlaceOnScreen));
        }

        /// <summary>初始化 Edge 内核；参数为窗口事件；无返回值，失败交给守护进程重建。</summary>
        private async void InitializeBrowser(object sender, EventArgs args)
        {
            try
            {
                var environment = await CoreWebView2Environment.CreateAsync(null, System.IO.Path.Combine(profile, "WebView2"));
                await browser.EnsureCoreWebView2Async(environment);
                var core = browser.CoreWebView2;
                core.Settings.AreDefaultContextMenusEnabled = false;
                core.Settings.AreDevToolsEnabled = false;
                core.Settings.AreBrowserAcceleratorKeysEnabled = false;
                core.Settings.IsStatusBarEnabled = false;
                core.Settings.IsZoomControlEnabled = false;
                core.Settings.IsPinchZoomEnabled = false;
                core.Settings.IsBuiltInErrorPageEnabled = false;
                browser.ZoomFactor = 1.0;
                core.NavigationStarting += delegate(object source, CoreWebView2NavigationStartingEventArgs navigation)
                {
                    // 展示窗口不进入后台，也不接受网页链接跳转到其他站点。
                    navigation.Cancel = !String.Equals(navigation.Uri, screenUrl, StringComparison.OrdinalIgnoreCase);
                };
                core.NewWindowRequested += delegate(object source, CoreWebView2NewWindowRequestedEventArgs request) { request.Handled = true; };
                core.DownloadStarting += delegate(object source, CoreWebView2DownloadStartingEventArgs download) { download.Cancel = true; };
                core.PermissionRequested += delegate(object source, CoreWebView2PermissionRequestedEventArgs request) { request.State = CoreWebView2PermissionState.Deny; };
                core.NavigationCompleted += delegate(object source, CoreWebView2NavigationCompletedEventArgs navigation)
                {
                    navigating = false;
                    ready = navigation.IsSuccess && navigation.HttpStatusCode == 200;
                    browser.Visible = ready;
                    waiting.Visible = !ready;
                    if (ready)
                    {
                        Program.Log("大屏页面加载完成。");
                        browser.BringToFront();
                        browser.Focus();
                    }
                };
                core.ProcessFailed += delegate(object source, CoreWebView2ProcessFailedEventArgs failure)
                {
                    Program.Log("网页内核异常：" + failure.ProcessFailedKind);
                    // 浏览器进程退出时控制器已不可复用；持续的渲染故障也重建整个窗口。
                    if (failure.ProcessFailedKind == CoreWebView2ProcessFailedKind.BrowserProcessExited ||
                        failure.ProcessFailedKind == CoreWebView2ProcessFailedKind.RenderProcessUnresponsive || ++rendererFailures > 3)
                        Environment.Exit(1);
                    ready = false;
                    navigating = false;
                };
                core.WebMessageReceived += delegate(object source, CoreWebView2WebMessageReceivedEventArgs message)
                {
                    if (message.Source == screenUrl && message.TryGetWebMessageAsString() == "bigscreen-exit")
                        BeginInvoke(new Action(RequestExit));
                };
                // 网页获得焦点时仍可呼出原生维护窗口，不向网页开放业务或系统调用接口。
                await core.AddScriptToExecuteOnDocumentCreatedAsync(
                    "document.addEventListener('keydown',function(e){if(e.ctrlKey&&e.shiftKey&&e.code==='KeyQ'){e.preventDefault();window.chrome.webview.postMessage('bigscreen-exit');}},true);");
                await ProbeService();
            }
            catch (Exception error)
            {
                Program.Log("网页初始化失败：" + error.Message);
                Environment.Exit(1);
            }
        }

        /// <summary>检测服务就绪状态；无参数；异步返回，未加载成功时自动重试，不覆盖断网前的画面。</summary>
        private async Task ProbeService()
        {
            if (probing || ready || navigating || browser.CoreWebView2 == null) return;
            probing = true;
            bool available = await Task.Run(delegate
            {
                try
                {
                    var request = (HttpWebRequest)WebRequest.Create(new Uri(new Uri(screenUrl), "/api/health"));
                    request.Timeout = 2000;
                    request.ReadWriteTimeout = 2000;
                    request.Proxy = null;
                    request.AllowAutoRedirect = false;
                    using (var response = (HttpWebResponse)request.GetResponse())
                        return response.StatusCode == HttpStatusCode.OK;
                }
                catch (WebException) { return false; }
            });
            probing = false;
            if (IsDisposed) return;
            if (available)
            {
                navigating = true;
                browser.CoreWebView2.Navigate(screenUrl);
            }
            else waiting.Text = "等待大屏服务连接…";
        }

        /// <summary>发送界面心跳并保持唤醒；参数为定时事件；每五秒重试初始连接，无返回值。</summary>
        private async void Tick(object sender, EventArgs args)
        {
            heartbeat.Set();
            NativeMethods.SetThreadExecutionState(0x80000003);
            if (++ticks % 5 == 0) await ProbeService();
        }

        /// <summary>处理网页焦点下的维护快捷键；参数为按键事件；无返回值。</summary>
        private void BrowserKeyDown(object sender, KeyEventArgs args)
        {
            if (args.KeyData == (Keys.Control | Keys.Shift | Keys.Q))
            {
                args.Handled = true;
                args.SuppressKeyPress = true;
                BeginInvoke(new Action(RequestExit));
            }
        }

        /// <summary>处理原生控件焦点下的快捷键；参数为消息和键值；返回是否已处理。</summary>
        protected override bool ProcessCmdKey(ref Message message, Keys keyData)
        {
            if (keyData == (Keys.Control | Keys.Shift | Keys.Q)) { RequestExit(); return true; }
            if (keyData == (Keys.Alt | Keys.F4) || keyData == Keys.F11) return true;
            return base.ProcessCmdKey(ref message, keyData);
        }

        /// <summary>注册运行期间的全局退出快捷键；args 为句柄事件；无返回值，冲突时保留窗口内快捷键。</summary>
        protected override void OnHandleCreated(EventArgs args)
        {
            base.OnHandleCreated(args);
            // Ctrl + Shift + Q 加禁止连发，主屏操作其他应用时也能呼出退出确认。
            exitHotKeyRegistered = NativeMethods.RegisterHotKey(Handle, ExitHotKeyId, 0x4006, (uint)Keys.Q);
            if (!exitHotKeyRegistered) Program.Log("全局退出快捷键已被占用，请切换到大屏窗口后按 Ctrl + Shift + Q。");
        }

        /// <summary>释放全局快捷键；args 为句柄事件；无返回值，支持窗口句柄重建及正常退出。</summary>
        protected override void OnHandleDestroyed(EventArgs args)
        {
            if (exitHotKeyRegistered) NativeMethods.UnregisterHotKey(Handle, ExitHotKeyId);
            exitHotKeyRegistered = false;
            base.OnHandleDestroyed(args);
        }

        /// <summary>处理全局退出和系统最小化命令；message 为 Windows 消息；无返回值。</summary>
        protected override void WndProc(ref Message message)
        {
            if (message.Msg == 0x0312 && message.WParam.ToInt32() == ExitHotKeyId)
            {
                BeginInvoke(new Action(RequestExit));
                return;
            }
            if (message.Msg == 0x0112 && (message.WParam.ToInt64() & 0xfff0) == 0xf020) return;
            base.WndProc(ref message);
        }

        /// <summary>确认并正常退出；无参数；成功使用专用退出码通知守护进程停止恢复。</summary>
        private void RequestExit()
        {
            if (prompting) return;
            prompting = true;
            try
            {
                if (DisplayDialogs.ConfirmExit(this))
                {
                    authorized = true;
                    Program.Log("已确认退出展示。");
                    Environment.ExitCode = Program.NormalExit;
                    Close();
                }
            }
            finally { prompting = false; }
        }

        /// <summary>拦截普通关闭；args 为关闭原因；无返回值，允许已确认退出和 Windows 关机。</summary>
        protected override void OnFormClosing(FormClosingEventArgs args)
        {
            if (!authorized && args.CloseReason != CloseReason.WindowsShutDown)
                args.Cancel = true;
            base.OnFormClosing(args);
        }

        /// <summary>防止任务栏等操作意外最小化；args 为尺寸事件；无返回值，正常退出不干预。</summary>
        protected override void OnResize(EventArgs args)
        {
            if (settings != null && !authorized && WindowState == FormWindowState.Minimized)
                WindowState = FormWindowState.Normal;
            base.OnResize(args);
        }

        /// <summary>释放显示资源；disposing 表示托管释放；无返回值，撤销本线程的保持唤醒请求。</summary>
        protected override void Dispose(bool disposing)
        {
            if (disposing)
            {
                SystemEvents.DisplaySettingsChanged -= DisplayChanged;
                timer.Dispose();
                NativeMethods.SetThreadExecutionState(0x80000000);
            }
            base.Dispose(disposing);
        }
    }
}
