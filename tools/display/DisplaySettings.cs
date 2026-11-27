using System;
using System.Collections.Generic;
using System.IO;
using System.Text;

namespace BigScreen.Display
{
    // 展示配置按 Windows 用户保存，独立于业务数据库及安装目录。
    internal sealed class DisplaySettings
    {
        internal string ServerUrl = "";
        internal string MonitorDevice = "";
        internal bool KeepOnTop = true;

        /// <summary>读取键值配置；path 为 UTF-8 文件路径；返回配置字典，缺失文件返回空字典。</summary>
        internal static Dictionary<string, string> ReadValues(string path)
        {
            var result = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            if (!File.Exists(path)) return result;
            foreach (string line in File.ReadAllLines(path, Encoding.UTF8))
            {
                string value = line.Trim();
                if (value.StartsWith(";") || value.StartsWith("#") || value.StartsWith("[")) continue;
                int split = value.IndexOf('=');
                if (split > 0) result[value.Substring(0, split).Trim()] = value.Substring(split + 1).Trim();
            }
            return result;
        }

        /// <summary>读取展示配置；path 为配置路径；返回配置对象，旧版维护密码字段不再使用。</summary>
        internal static DisplaySettings Load(string path)
        {
            var values = ReadValues(path);
            var settings = new DisplaySettings();
            values.TryGetValue("server_url", out settings.ServerUrl);
            values.TryGetValue("monitor", out settings.MonitorDevice);
            settings.ServerUrl = settings.ServerUrl ?? "";
            settings.MonitorDevice = settings.MonitorDevice ?? "";
            string keepOnTop;
            if (values.TryGetValue("keep_on_top", out keepOnTop) && !Boolean.TryParse(keepOnTop, out settings.KeepOnTop))
                throw new InvalidDataException("展示配置中的 keep_on_top 必须为 true 或 false。");
            return settings;
        }

        /// <summary>规范化服务地址；value 为 HTTP 地址；返回仅包含服务根路径的地址，拒绝凭据及子路径。</summary>
        internal static string NormalizeServer(string value)
        {
            Uri uri;
            if (!Uri.TryCreate(value.Trim(), UriKind.Absolute, out uri) ||
                (uri.Scheme != "http" && uri.Scheme != "https") || uri.UserInfo != "" ||
                uri.AbsolutePath != "/" || uri.Query != "" || uri.Fragment != "")
                throw new ArgumentException("服务地址须为 http://电脑IP:端口 或 HTTPS 地址，不要填写页面路径。");
            return uri.GetLeftPart(UriPartial.Authority);
        }

        /// <summary>计算大屏地址；baseDirectory 为程序目录；返回使用外置端口或自定义服务地址的 /screen 地址。</summary>
        internal string ScreenUrl(string baseDirectory)
        {
            if (ServerUrl != "") return NormalizeServer(ServerUrl) + "/screen";
            var values = ReadValues(Path.Combine(baseDirectory, "BigScreen.ini"));
            string raw;
            int port = 8080;
            if (values.TryGetValue("port", out raw) && (!Int32.TryParse(raw, out port) || port < 1 || port > 65535))
                throw new InvalidDataException("BigScreen.ini 中的 port 必须为 1～65535。");
            return "http://127.0.0.1:" + port + "/screen";
        }

        /// <summary>原子保存展示配置；path 为目标路径；无返回值，保留上次配置作为备份。</summary>
        internal void Save(string path)
        {
            if (ServerUrl != "") ServerUrl = NormalizeServer(ServerUrl);
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            string content = "[display]\r\nserver_url=" + ServerUrl + "\r\nmonitor=" + MonitorDevice +
                "\r\nkeep_on_top=" + KeepOnTop.ToString().ToLowerInvariant() + "\r\n";
            string temporary = path + ".tmp";
            File.WriteAllText(temporary, content, new UTF8Encoding(false));
            if (File.Exists(path)) File.Replace(temporary, path, path + ".bak");
            else File.Move(temporary, path);
        }
    }
}
