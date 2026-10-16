// Terminal PowerShell natif : ConPTY, pas de Linux, SSH ou console Proxmox.
using System;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;

namespace Loco
{
    internal sealed class ProcessJob : IDisposable
    {
        IntPtr handle;
        public ProcessJob()
        {
            handle = CreateJobObject(IntPtr.Zero, null);
            if (handle == IntPtr.Zero) throw new Win32Exception();
            var limits = new ExtendedLimits(); limits.Basic.Flags = 0x2000; // KILL_ON_JOB_CLOSE
            int size = Marshal.SizeOf(typeof(ExtendedLimits)); IntPtr p = Marshal.AllocHGlobal(size);
            try { Marshal.StructureToPtr(limits, p, false); if (!SetInformationJobObject(handle, 9, p, (uint)size)) throw new Win32Exception(); }
            catch { Dispose(); throw; }
            finally { Marshal.FreeHGlobal(p); }
        }
        public void Add(IntPtr process) { if (!AssignProcessToJobObject(handle, process)) throw new Win32Exception(); }
        public void Dispose() { if (handle != IntPtr.Zero) { CloseHandle(handle); handle = IntPtr.Zero; } }
        [StructLayout(LayoutKind.Sequential)] struct BasicLimits
        {
            public long ProcessTime, JobTime; public uint Flags;
            public UIntPtr MinWorkingSet, MaxWorkingSet; public uint ActiveProcesses;
            public UIntPtr Affinity; public uint Priority, Scheduling;
        }
        [StructLayout(LayoutKind.Sequential)] struct IoCounters { public ulong Read, Write, Other, ReadBytes, WriteBytes, OtherBytes; }
        [StructLayout(LayoutKind.Sequential)] struct ExtendedLimits
        {
            public BasicLimits Basic; public IoCounters Io;
            public UIntPtr ProcessMemory, JobMemory, PeakProcessMemory, PeakJobMemory;
        }
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern IntPtr CreateJobObject(IntPtr security, string name);
        [DllImport("kernel32.dll", SetLastError = true)] static extern bool SetInformationJobObject(IntPtr job, int kind, IntPtr data, uint length);
        [DllImport("kernel32.dll", SetLastError = true)] static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
        [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
    }

    internal sealed class NativeTerminal : IDisposable
    {
        IntPtr console, process;
        readonly object consoleLock = new object();
        public FileStream Input, Output;
        public ProcessJob Job;
        public NativeTerminal(int cols, int rows)
        {
            IntPtr inRead = IntPtr.Zero, inWrite = IntPtr.Zero, outRead = IntPtr.Zero, outWrite = IntPtr.Zero, attributes = IntPtr.Zero;
            var info = new ProcessInfo();
            try
            {
                if (!CreatePipe(out inRead, out inWrite, IntPtr.Zero, 0) || !CreatePipe(out outRead, out outWrite, IntPtr.Zero, 0)) throw new Win32Exception();
                int result = CreatePseudoConsole(new Coord { X = (short)cols, Y = (short)rows }, inRead, outWrite, 0, out console);
                if (result != 0) Marshal.ThrowExceptionForHR(result);
                IntPtr size = IntPtr.Zero;
                InitializeProcThreadAttributeList(IntPtr.Zero, 1, 0, ref size);
                attributes = Marshal.AllocHGlobal(size);
                if (!InitializeProcThreadAttributeList(attributes, 1, 0, ref size) ||
                    !UpdateProcThreadAttribute(attributes, 0, (IntPtr)0x20016, console, (IntPtr)IntPtr.Size, IntPtr.Zero, IntPtr.Zero)) throw new Win32Exception();
                var startup = new StartupEx(); startup.Startup.Size = Marshal.SizeOf(typeof(StartupEx)); startup.Attributes = attributes;
                string shell = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), @"WindowsPowerShell\v1.0\powershell.exe");
                if (!CreateProcess(shell, new StringBuilder("\"" + shell + "\" -NoLogo -NoProfile"), IntPtr.Zero, IntPtr.Zero, false,
                    0x80000 | 0x4, IntPtr.Zero, Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ref startup, out info)) throw new Win32Exception();
                process = info.Process;
                Job = new ProcessJob(); Job.Add(process);
                Input = new FileStream(new SafeFileHandle(inWrite, true), FileAccess.Write, 4096, false); inWrite = IntPtr.Zero;
                Output = new FileStream(new SafeFileHandle(outRead, true), FileAccess.Read, 4096, false); outRead = IntPtr.Zero;
                if (ResumeThread(info.Thread) == uint.MaxValue) throw new Win32Exception();
            }
            catch { if (process != IntPtr.Zero) TerminateProcess(process, 1); Dispose(); throw; }
            finally
            {
                foreach (IntPtr h in new[] { inRead, inWrite, outRead, outWrite, info.Thread }) if (h != IntPtr.Zero) CloseHandle(h);
                if (attributes != IntPtr.Zero) { DeleteProcThreadAttributeList(attributes); Marshal.FreeHGlobal(attributes); }
            }
        }
        public void Resize(int cols, int rows)
        {
            lock (consoleLock) if (console != IntPtr.Zero) Marshal.ThrowExceptionForHR(ResizePseudoConsole(console, new Coord { X = (short)cols, Y = (short)rows }));
        }
        public void Dispose()
        {
            if (Job != null) { Job.Dispose(); Job = null; }
            // Le lecteur reste actif jusqu'à EOF pour laisser ConPTY vider sa sortie à la fermeture.
            lock (consoleLock) if (console != IntPtr.Zero) { ClosePseudoConsole(console); console = IntPtr.Zero; }
            if (Input != null) { Input.Dispose(); Input = null; }
            if (Output != null) { Output.Dispose(); Output = null; }
            if (process != IntPtr.Zero) { CloseHandle(process); process = IntPtr.Zero; }
        }
        [StructLayout(LayoutKind.Sequential)] struct Coord { public short X, Y; }
        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)] struct Startup
        {
            public int Size; public string Reserved, Desktop, Title;
            public int X, Y, XSize, YSize, XCountChars, YCountChars, FillAttribute, Flags;
            public short ShowWindow, ReservedSize; public IntPtr ReservedPtr, Input, Output, Error;
        }
        [StructLayout(LayoutKind.Sequential)] struct StartupEx { public Startup Startup; public IntPtr Attributes; }
        [StructLayout(LayoutKind.Sequential)] struct ProcessInfo { public IntPtr Process, Thread; public int ProcessId, ThreadId; }
        [DllImport("kernel32.dll", SetLastError = true)] static extern bool CreatePipe(out IntPtr read, out IntPtr write, IntPtr security, uint size);
        [DllImport("kernel32.dll")] static extern int CreatePseudoConsole(Coord size, IntPtr input, IntPtr output, uint flags, out IntPtr console);
        [DllImport("kernel32.dll")] static extern int ResizePseudoConsole(IntPtr console, Coord size);
        [DllImport("kernel32.dll")] static extern void ClosePseudoConsole(IntPtr console);
        [DllImport("kernel32.dll", SetLastError = true)] static extern bool InitializeProcThreadAttributeList(IntPtr list, int count, int flags, ref IntPtr size);
        [DllImport("kernel32.dll", SetLastError = true)] static extern bool UpdateProcThreadAttribute(IntPtr list, uint flags, IntPtr attribute, IntPtr value, IntPtr size, IntPtr previous, IntPtr returnedSize);
        [DllImport("kernel32.dll")] static extern void DeleteProcThreadAttributeList(IntPtr list);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern bool CreateProcess(string application, StringBuilder command, IntPtr processSecurity, IntPtr threadSecurity, bool inherit, int flags, IntPtr environment, string directory, ref StartupEx startup, out ProcessInfo process);
        [DllImport("kernel32.dll", SetLastError = true)] static extern bool TerminateProcess(IntPtr process, uint code);
        [DllImport("kernel32.dll", SetLastError = true)] static extern uint ResumeThread(IntPtr thread);
        [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
    }
}
