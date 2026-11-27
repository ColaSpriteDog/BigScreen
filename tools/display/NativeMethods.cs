using System;
using System.Diagnostics;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

namespace BigScreen.Display
{
    internal static class NativeMethods
    {
        // 作业对象只管理本次启动的展示窗口，守护进程退出时由系统回收该窗口。
        [StructLayout(LayoutKind.Sequential)]
        private struct BasicLimit
        {
            internal long ProcessTime, JobTime;
            internal uint Flags;
            internal UIntPtr MinimumWorkingSet, MaximumWorkingSet;
            internal uint ActiveProcessLimit;
            internal UIntPtr Affinity;
            internal uint PriorityClass, SchedulingClass;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct IoCounters
        {
            internal ulong ReadOperations, WriteOperations, OtherOperations, ReadBytes, WriteBytes, OtherBytes;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct ExtendedLimit
        {
            internal BasicLimit Basic;
            internal IoCounters Io;
            internal UIntPtr ProcessMemory, JobMemory, PeakProcessMemory, PeakJobMemory;
        }

        /// <summary>创建 Windows 作业对象；参数为系统保留值；返回由安全句柄管理的对象。</summary>
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern SafeFileHandle CreateJobObject(IntPtr attributes, string name);

        /// <summary>设置作业限制；参数为句柄、信息类别及结构；返回系统调用成功状态。</summary>
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool SetInformationJobObject(SafeFileHandle job, int kind, ref ExtendedLimit info, uint size);

        /// <summary>将子进程纳入作业；参数为作业和进程句柄；返回系统调用成功状态。</summary>
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool AssignProcessToJobObject(SafeFileHandle job, IntPtr process);

        /// <summary>控制屏保与休眠；flags 为线程执行状态；返回先前状态，零表示失败。</summary>
        [DllImport("kernel32.dll")]
        internal static extern uint SetThreadExecutionState(uint flags);

        /// <summary>按当前显示器缩放处理窗口；无参数；返回系统设置结果，旧系统可忽略。</summary>
        [DllImport("user32.dll")]
        internal static extern bool SetProcessDpiAwarenessContext(IntPtr context);

        /// <summary>注册展示退出快捷键；window 为窗口、id 为标识、modifiers 为修饰键、key 为键值；返回注册结果。</summary>
        [DllImport("user32.dll", SetLastError = true)]
        internal static extern bool RegisterHotKey(IntPtr window, int id, uint modifiers, uint key);

        /// <summary>释放展示退出快捷键；window 为窗口、id 为注册标识；返回释放结果，不修改系统持久配置。</summary>
        [DllImport("user32.dll", SetLastError = true)]
        internal static extern bool UnregisterHotKey(IntPtr window, int id);

        /// <summary>创建自动回收子进程的作业；无参数；返回句柄，失败抛出系统错误。</summary>
        internal static SafeFileHandle CreateChildJob()
        {
            var job = CreateJobObject(IntPtr.Zero, null);
            var info = new ExtendedLimit();
            info.Basic.Flags = 0x2000;
            if (job.IsInvalid || !SetInformationJobObject(job, 9, ref info, (uint)Marshal.SizeOf(info)))
            {
                int error = Marshal.GetLastWin32Error();
                job.Dispose();
                throw new System.ComponentModel.Win32Exception(error);
            }
            return job;
        }

        /// <summary>关联展示子进程；job 为作业、process 为刚启动的进程；失败抛出系统错误。</summary>
        internal static void AttachChild(SafeFileHandle job, Process process)
        {
            if (!AssignProcessToJobObject(job, process.Handle))
                throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
        }
    }
}
