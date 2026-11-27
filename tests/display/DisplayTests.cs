using System;
using System.Drawing;
using System.IO;
using System.Reflection;
using System.Threading;
using System.Windows.Forms;

namespace BigScreen.Display
{
    internal static class DisplayTests
    {
        /// <summary>断言测试条件；condition 为预期条件、message 为说明；失败抛出异常。</summary>
        private static void Check(bool condition, string message)
        {
            if (!condition) throw new Exception(message);
        }

        /// <summary>断言操作被拒绝；action 为无参数操作；未抛出校验错误时测试失败。</summary>
        private static void Reject(Action action)
        {
            try { action(); }
            catch (ArgumentException) { return; }
            catch (InvalidDataException) { return; }
            throw new Exception("非法输入未被拒绝");
        }

        /// <summary>验证退出确认的主屏位置和按钮行为；exit 表示选择退出；无返回值，副屏所属窗口不得改变弹窗位置。</summary>
        private static void CheckPrimaryExitDialog(bool exit)
        {
            using (var owner = new Form { StartPosition = FormStartPosition.Manual, ShowInTaskbar = false })
            using (var timer = new System.Windows.Forms.Timer { Interval = 100 })
            {
                // 没有副屏时也将所属窗口放到主屏外，确保测试不能依赖父窗口居中而误通过。
                Rectangle primary = Screen.PrimaryScreen.Bounds;
                owner.Bounds = new Rectangle(primary.Right + 100, primary.Top + 100, 300, 200);
                foreach (Screen screen in Screen.AllScreens)
                    if (!screen.Primary) { owner.Bounds = new Rectangle(screen.Bounds.Location, new Size(300, 200)); break; }
                Exception failure = null;
                bool observed = false;
                DateTime deadline = DateTime.UtcNow.AddSeconds(5);
                owner.Show();
                timer.Tick += delegate
                {
                    Form dialog = null;
                    foreach (Form open in Application.OpenForms)
                        if (open.Text == "退出大屏展示") { dialog = open; break; }
                    if (dialog == null) return;
                    timer.Stop();
                    try
                    {
                        Check(DateTime.UtcNow <= deadline, "退出确认未及时显示");
                        Rectangle area = Screen.PrimaryScreen.WorkingArea;
                        Check(area.Contains(dialog.Bounds), "退出确认必须完整显示在电脑主屏工作区");
                        Check(Math.Abs(dialog.Left - (area.Left + (area.Width - dialog.Width) / 2)) <= 1 &&
                            Math.Abs(dialog.Top - (area.Top + (area.Height - dialog.Height) / 2)) <= 1,
                            "退出确认必须在电脑主屏居中");
                        Check(dialog.Owner == owner, "主屏弹窗应保留与展示窗口的所属关系");
                        Check(((Button)dialog.AcceptButton).Text == "继续展示" &&
                            dialog.AcceptButton == dialog.CancelButton, "回车和 Esc 默认都应继续展示");
                        Check(dialog.ActiveControl == dialog.AcceptButton, "默认焦点必须位于继续展示按钮");
                        Button chosen = null;
                        foreach (Control control in dialog.Controls)
                            if (control is Button && control.Text == (exit ? "退出展示" : "继续展示")) chosen = (Button)control;
                        Check(chosen != null, "退出确认缺少操作按钮");
                        observed = true;
                        chosen.PerformClick();
                    }
                    catch (Exception error)
                    {
                        failure = error;
                        dialog.DialogResult = DialogResult.Cancel;
                    }
                };
                timer.Start();
                bool result = DisplayDialogs.ConfirmExit(owner);
                if (failure != null) throw failure;
                Check(observed && result == exit, "退出确认返回结果与所选按钮不一致");
            }
        }

        /// <summary>验证展示配置；args 提供独立临时目录，init 模式用于隔离的窗口测试；返回退出码。</summary>
        [STAThread]
        private static int Main(string[] args)
        {
            try { return Run(args); }
            catch (Exception error)
            {
                Console.Error.WriteLine(error.GetType().Name + ": " + error.Message);
                Console.Error.WriteLine(error.StackTrace);
                return 1;
            }
        }

        /// <summary>执行隔离检查；args 为测试目录和模式；返回成功状态码，失败由入口报告。</summary>
        private static int Run(string[] args)
        {
            string directory = args[0];
            Directory.CreateDirectory(directory);
            var settings = new DisplaySettings();
            if (args.Length > 1 && args[1] == "dialogs")
            {
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                CheckPrimaryExitDialog(false);
                CheckPrimaryExitDialog(true);
                Console.WriteLine("主屏退出确认、默认焦点、继续展示及退出按钮检查通过");
                return 0;
            }
            if (args.Length > 1 && args[1] == "init")
            {
                settings.ServerUrl = args[2];
                settings.Save(Path.Combine(directory, "settings.ini"));
                return 0;
            }
            settings.ServerUrl = "http://127.0.0.1:8099/";
            settings.MonitorDevice = @"\\.\DISPLAY2";
            string path = Path.Combine(directory, "配置 空格", "settings.ini");
            settings.Save(path);
            settings.Save(path);
            var restored = DisplaySettings.Load(path);
            Check(restored.MonitorDevice == settings.MonitorDevice, "显示设备必须保留");
            Check(restored.KeepOnTop, "默认应保持在最前面");
            Check(restored.ScreenUrl(directory) == "http://127.0.0.1:8099/screen", "规范化地址错误");
            Check(!File.ReadAllText(path).Contains("password_"), "新版配置不应再写入维护密码字段");
            Check(File.Exists(path + ".bak"), "更新配置必须保留备份");
            settings.ServerUrl = "";
            Check(settings.ScreenUrl(directory) == "http://127.0.0.1:8080/screen", "默认端口错误");
            File.WriteAllText(Path.Combine(directory, "BigScreen.ini"), "[server]\r\nport=8101\r\n");
            Check(settings.ScreenUrl(directory) == "http://127.0.0.1:8101/screen", "未沿用安装端口");
            File.WriteAllText(Path.Combine(directory, "BigScreen.ini"), "[server]\r\nport=65536\r\n");
            Reject(delegate { settings.ScreenUrl(directory); });
            foreach (string invalid in new[] { "file:///C:/test", "javascript:alert(1)", "http://a/edit", "http://a/?x=1", "http://user:pass@a", "http://a/#fragment" })
                Reject(delegate { DisplaySettings.NormalizeServer(invalid); });
            Check(DisplaySettings.NormalizeServer("https://example.com:8443/") == "https://example.com:8443", "HTTPS 地址错误");
            // 旧密码字段即使已损坏也不能阻止无密码版本使用已有显示设置。
            File.WriteAllText(path, "server_url=http://127.0.0.1:8099\nmonitor=legacy-screen\nkeep_on_top=false\npassword_salt=invalid\npassword_hash=invalid\n");
            var legacy = DisplaySettings.Load(path);
            Check(legacy.ServerUrl == "http://127.0.0.1:8099" && legacy.MonitorDevice == "legacy-screen" && !legacy.KeepOnTop,
                "旧配置应忽略维护密码并保留其他设置");
            legacy.Save(path);
            Check(!File.ReadAllText(path).Contains("password_"), "保存旧配置时应移除维护密码字段");
            File.WriteAllText(path, "keep_on_top=invalid\n");
            Reject(delegate { DisplaySettings.Load(path); });
            Check(Program.Quote(@"D:\中文 空格\") == "\"D:\\中文 空格\\\\\"", "命令行尾部反斜杠转义错误");
            Check(!Program.RuntimeSupported("130.0.2849.80"), "过旧内核必须提示升级");
            Check(Program.RuntimeSupported("131.0.2903.40"), "最低兼容内核应可使用");
            Check(Program.RuntimeSupported("158.0.4370.33 beta"), "带渠道的版本应可识别");
            Check(!Program.RuntimeSupported(null), "缺失内核版本必须拒绝");
            settings.ServerUrl = "http://127.0.0.1:8080";
            using (var pulse = new EventWaitHandle(false, EventResetMode.AutoReset))
            using (var window = new DisplayWindow(settings, directory, pulse))
            {
                var closing = typeof(DisplayWindow).GetMethod("OnFormClosing", BindingFlags.NonPublic | BindingFlags.Instance);
                var accidental = new FormClosingEventArgs(CloseReason.UserClosing, false);
                closing.Invoke(window, new object[] { accidental });
                Check(accidental.Cancel, "普通关闭必须被拦截");
                var shutdown = new FormClosingEventArgs(CloseReason.WindowsShutDown, false);
                closing.Invoke(window, new object[] { shutdown });
                Check(!shutdown.Cancel, "不得阻止 Windows 关机");
                window.WindowState = FormWindowState.Minimized;
                // 未显示的窗口不会收到系统尺寸消息，显式派发事件以验证恢复分支。
                typeof(DisplayWindow).GetMethod("OnResize", BindingFlags.NonPublic | BindingFlags.Instance)
                    .Invoke(window, new object[] { EventArgs.Empty });
                Check(window.WindowState != FormWindowState.Minimized, "不得保持最小化状态");
            }
            Console.WriteLine("配置、旧版兼容、端口优先级、非法地址和损坏配置检查通过");
            return 0;
        }
    }
}
