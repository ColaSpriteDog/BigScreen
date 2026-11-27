using System;
using System.Diagnostics;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Win32;

namespace BigScreen.Display
{
    internal static class Program
    {
        internal const int NormalExit = 42;
        internal static string Profile;

        /// <summary>记录展示运行状态；text 为日志内容；无返回值，限制日志大小且不影响展示。</summary>
        internal static void Log(string text)
        {
            try
            {
                string path = Path.Combine(Profile, "display.log");
                if (File.Exists(path) && new FileInfo(path).Length > 1024 * 1024)
                {
                    File.Copy(path, path + ".previous", true);
                    File.WriteAllText(path, "", Encoding.UTF8);
                }
                File.AppendAllText(path, DateTimeOffset.Now.ToString("o") + " " + text + Environment.NewLine, Encoding.UTF8);
            }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
        }

        /// <summary>转义 Windows 命令行参数；value 为一个参数；返回带引号且保留尾反斜杠的文本。</summary>
        internal static string Quote(string value)
        {
            var output = new StringBuilder("\"");
            int slashes = 0;
            foreach (char character in value)
            {
                if (character == '\\') { slashes++; continue; }
                output.Append('\\', character == '"' ? slashes * 2 + 1 : slashes);
                output.Append(character);
                slashes = 0;
            }
            output.Append('\\', slashes * 2);
            return output.Append('"').ToString();
        }

        /// <summary>检查内核兼容性；value 为 WebView2 版本字符串；返回是否满足当前 SDK 的最低 131 版本。</summary>
        internal static bool RuntimeSupported(string value)
        {
            Version version;
            return value != null && Version.TryParse(value.Split(' ')[0], out version) && version.Major >= 131;
        }

        /// <summary>启动独立展示入口；args 为配置或内部窗口参数；无返回值，错误返回非零退出码。</summary>
        [STAThread]
        private static void Main(string[] args)
        {
            bool configure = false;
            string token = null;
            Profile = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "BigScreen", "Display");
            try
            {
                for (int index = 0; index < args.Length; index++)
                {
                    if (args[index] == "--configure") configure = true;
                    else if (args[index] == "--profile" && index + 1 < args.Length) Profile = Path.GetFullPath(args[++index]);
                    else if (args[index] == "--window" && index + 1 < args.Length) token = args[++index];
                    else throw new ArgumentException("未知参数：" + args[index]);
                }
                Directory.CreateDirectory(Profile);
                try { NativeMethods.SetProcessDpiAwarenessContext(new IntPtr(-4)); }
                catch (EntryPointNotFoundException) { }
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                Application.SetUnhandledExceptionMode(UnhandledExceptionMode.ThrowException);
                string path = Path.Combine(Profile, "settings.ini");
                var settings = DisplaySettings.Load(path);
                if (token != null)
                {
                    using (var pulse = EventWaitHandle.OpenExisting("Local\\BigScreen.Display.Pulse." + token))
                    using (var window = new DisplayWindow(settings, Profile, pulse)) Application.Run(window);
                    return;
                }
                // 同一登录会话和配置目录只运行一个守护进程，重复双击不会覆盖 LED 窗口。
                string identity;
                using (var hash = SHA256.Create()) identity = BitConverter.ToString(hash.ComputeHash(Encoding.UTF8.GetBytes(Profile.ToUpperInvariant()))).Replace("-", "");
                bool created;
                using (var mutex = new Mutex(true, "Local\\BigScreen.Display." + identity, out created))
                using (var installationLock = new Mutex(false, "Local\\BigScreen.Display.Running"))
                {
                    if (!created)
                    {
                        MessageBox.Show("大屏展示已在运行。修改设置请先按 Ctrl + Shift + Q 确认退出。", "BigScreen 大屏展示");
                        return;
                    }
                    try
                    {
                        // 缺少内核时只提示一次，不进入守护重启循环。
                        try
                        {
                            if (!RuntimeSupported(CoreWebView2Environment.GetAvailableBrowserVersionString()))
                                throw new InvalidOperationException("Microsoft Edge WebView2 Runtime 版本过旧。请安装交付包中的离线运行库（需 131 或以上版本）。");
                        }
                        catch (WebView2RuntimeNotFoundException)
                        {
                            throw new InvalidOperationException("缺少 Microsoft Edge WebView2 Runtime。请安装交付包中的 WebView2 离线运行库后重试。");
                        }
                        if ((!File.Exists(path) || configure) && !DisplayDialogs.Configure(settings, path)) return;
                        settings.ScreenUrl(AppDomain.CurrentDomain.BaseDirectory);
                        using (var supervisor = new DisplaySupervisor()) Application.Run(supervisor);
                    }
                    finally { mutex.ReleaseMutex(); }
                }
            }
            catch (Exception error)
            {
                Log("启动失败：" + error.GetType().Name + " " + error.Message);
                if (token == null) MessageBox.Show(error.Message + "\r\n\r\n日志：" + Path.Combine(Profile, "display.log"),
                    "大屏展示启动失败", MessageBoxButtons.OK, MessageBoxIcon.Error);
                Environment.ExitCode = 1;
            }
        }
    }

    internal sealed class DisplaySupervisor : ApplicationContext
    {
        private readonly System.Windows.Forms.Timer timer;
        private readonly Microsoft.Win32.SafeHandles.SafeFileHandle job;
        private Process child;
        private EventWaitHandle heartbeat;
        private DateTime lastHeartbeat, started, restartAfter;
        private int failures;
        private bool stopping;

        /// <summary>创建展示守护；无参数；管理子窗口心跳、崩溃恢复及 Windows 会话结束。</summary>
        internal DisplaySupervisor()
        {
            job = NativeMethods.CreateChildJob();
            timer = new System.Windows.Forms.Timer { Interval = 1000 };
            timer.Tick += Tick;
            SystemEvents.SessionEnding += SessionEnding;
            StartWindow();
            timer.Start();
        }

        /// <summary>启动受管窗口；无参数；每次创建独立心跳事件并纳入作业对象。</summary>
        private void StartWindow()
        {
            if (heartbeat != null) heartbeat.Dispose();
            string token = Guid.NewGuid().ToString("N");
            heartbeat = new EventWaitHandle(false, EventResetMode.AutoReset, "Local\\BigScreen.Display.Pulse." + token);
            var start = new ProcessStartInfo(Application.ExecutablePath,
                "--profile " + Program.Quote(Program.Profile) + " --window " + token) { UseShellExecute = false, CreateNoWindow = true };
            child = Process.Start(start);
            try { NativeMethods.AttachChild(job, child); }
            catch
            {
                if (!child.HasExited) child.Kill();
                child.Dispose();
                child = null;
                throw;
            }
            started = lastHeartbeat = DateTime.UtcNow;
            Program.Log("展示窗口已启动，进程 " + child.Id);
        }

        /// <summary>检查展示窗口；参数为定时事件；异常退出或界面失去心跳 45 秒时按退避策略重启。</summary>
        private void Tick(object sender, EventArgs args)
        {
            if (stopping) return;
            if (child == null)
            {
                if (DateTime.UtcNow >= restartAfter) StartWindow();
                return;
            }
            if (heartbeat.WaitOne(0)) lastHeartbeat = DateTime.UtcNow;
            if (!child.HasExited && (DateTime.UtcNow - lastHeartbeat).TotalSeconds > 45)
            {
                Program.Log("展示窗口 45 秒无界面心跳，重新启动。");
                child.Kill();
                return;
            }
            if (!child.HasExited) return;
            int code = child.ExitCode;
            child.Dispose();
            child = null;
            if (code == Program.NormalExit) { ExitThread(); return; }
            failures = (DateTime.UtcNow - started).TotalSeconds > 60 ? 1 : Math.Min(failures + 1, 5);
            int delay = Math.Min(30, 1 << failures);
            restartAfter = DateTime.UtcNow.AddSeconds(delay);
            Program.Log("展示窗口异常退出（" + code + "），" + delay + " 秒后恢复。");
        }

        /// <summary>响应 Windows 注销和关机；参数为系统事件；停止重启，允许系统结束会话。</summary>
        private void SessionEnding(object sender, SessionEndingEventArgs args)
        {
            stopping = true;
        }

        /// <summary>释放守护资源；disposing 为托管释放标记；无返回值，仅回收本程序子进程。</summary>
        protected override void Dispose(bool disposing)
        {
            if (disposing)
            {
                stopping = true;
                SystemEvents.SessionEnding -= SessionEnding;
                timer.Dispose();
                job.Dispose();
                if (child != null) child.Dispose();
                if (heartbeat != null) heartbeat.Dispose();
            }
            base.Dispose(disposing);
        }
    }
}
