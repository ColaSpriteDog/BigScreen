using System;
using System.Drawing;
using System.Windows.Forms;

namespace BigScreen.Display
{
    internal static class DisplayDialogs
    {
        /// <summary>创建原生维护窗口；title 为标题、height 为内容高度；返回固定尺寸居中窗口。</summary>
        private static Form Dialog(string title, int height)
        {
            return new Form { Text = title, ClientSize = new Size(500, height),
                Font = new Font("Microsoft YaHei UI", 10), FormBorderStyle = FormBorderStyle.FixedDialog,
                StartPosition = FormStartPosition.CenterScreen, MaximizeBox = false, MinimizeBox = false,
                TopMost = true, AutoScaleMode = AutoScaleMode.Dpi };
        }

        /// <summary>放置表单标签；form 为窗口、text 为文案、top 为纵坐标；无返回值。</summary>
        private static void Label(Form form, string text, int top)
        {
            form.Controls.Add(new Label { Text = text, Left = 24, Top = top, Width = 450, Height = 24 });
        }

        /// <summary>将维护窗口居中到电脑主屏；form 为已完成缩放的窗口；无返回值，使用工作区避开任务栏。</summary>
        private static void CenterOnPrimaryScreen(Form form)
        {
            Rectangle area = Screen.PrimaryScreen.WorkingArea;
            form.Location = new Point(area.Left + Math.Max(0, (area.Width - form.Width) / 2),
                area.Top + Math.Max(0, (area.Height - form.Height) / 2));
        }

        /// <summary>在电脑主屏确认退出；owner 为大屏窗口；返回是否明确退出，默认继续展示，无需密码。</summary>
        internal static bool ConfirmExit(IWin32Window owner)
        {
            using (var form = Dialog("退出大屏展示", 140))
            {
                // LED 可能位于室外，退出操作必须留在主屏，不跟随大屏窗口居中。
                form.StartPosition = FormStartPosition.Manual;
                CenterOnPrimaryScreen(form);
                form.Load += delegate { CenterOnPrimaryScreen(form); };
                Label(form, "确定退出大屏展示？", 24);
                var accept = new Button { Text = "退出展示", Left = 270, Top = 84, Width = 100, Height = 32, DialogResult = DialogResult.OK };
                var cancel = new Button { Text = "继续展示", Left = 380, Top = 84, Width = 94, Height = 32, DialogResult = DialogResult.Cancel };
                form.Controls.AddRange(new Control[] { accept, cancel });
                // 默认焦点和回车均保留展示，避免快捷键误触后直接结束。
                form.AcceptButton = cancel;
                form.CancelButton = cancel;
                form.Shown += delegate
                {
                    // 主副屏缩放不同时，显示后按最终尺寸重新居中并激活维护窗口。
                    CenterOnPrimaryScreen(form);
                    form.Activate();
                    cancel.Focus();
                };
                return form.ShowDialog(owner) == DialogResult.OK;
            }
        }

        /// <summary>编辑展示选项；settings 为现有配置、path 为保存路径；返回是否保存。</summary>
        internal static bool Configure(DisplaySettings settings, string path)
        {
            using (var form = Dialog("大屏展示设置", 315))
            {
                Label(form, "服务地址（留空连接本机服务）", 20);
                var address = new TextBox { Left = 24, Top = 50, Width = 450, Text = settings.ServerUrl };
                Label(form, "显示屏", 95);
                var screens = new ComboBox { Left = 24, Top = 125, Width = 450, DropDownStyle = ComboBoxStyle.DropDownList };
                screens.Items.Add("自动选择第二块屏幕，没有时使用主屏");
                screens.SelectedIndex = 0;
                foreach (Screen screen in Screen.AllScreens)
                {
                    int index = screens.Items.Add(screen.DeviceName + "  " + screen.Bounds.Width + " × " + screen.Bounds.Height + (screen.Primary ? "（主屏）" : ""));
                    if (settings.MonitorDevice == screen.DeviceName) screens.SelectedIndex = index;
                }
                var keepOnTop = new CheckBox { Text = "保持在其他窗口前面", Left = 24, Top = 170, Width = 400, Height = 26, Checked = settings.KeepOnTop };
                var save = new Button { Text = "保存并展示", Left = 270, Top = 255, Width = 110, Height = 34 };
                var cancel = new Button { Text = "取消", Left = 390, Top = 255, Width = 84, Height = 34, DialogResult = DialogResult.Cancel };
                form.Controls.AddRange(new Control[] { address, screens, keepOnTop, save, cancel });
                Label(form, "退出快捷键：Ctrl + Shift + Q", 210);
                save.Click += delegate
                {
                    try
                    {
                        string server = address.Text.Trim() == "" ? "" : DisplaySettings.NormalizeServer(address.Text);
                        settings.ServerUrl = server;
                        settings.KeepOnTop = keepOnTop.Checked;
                        settings.MonitorDevice = screens.SelectedIndex == 0 ? "" : Screen.AllScreens[screens.SelectedIndex - 1].DeviceName;
                        settings.Save(path);
                        form.DialogResult = DialogResult.OK;
                    }
                    catch (Exception error) { MessageBox.Show(form, error.Message, "无法保存", MessageBoxButtons.OK, MessageBoxIcon.Warning); }
                };
                form.AcceptButton = save;
                form.CancelButton = cancel;
                return form.ShowDialog() == DialogResult.OK;
            }
        }
    }
}
