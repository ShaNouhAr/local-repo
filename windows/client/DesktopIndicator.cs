// Partie privilégiée : publie un état expurgé et lance le compagnon avec le jeton de chaque utilisateur.
using System;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Text;
using System.Web.Script.Serialization;

namespace Loco
{
    internal static class DesktopIndicator
    {
        static readonly string Executable = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "loco-tray.exe");

        public static void Publish(string color, string reason)
        {
            try
            {
                var security = new DirectorySecurity();
                security.SetAccessRuleProtection(true, false);
                var system = new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null);
                security.SetOwner(system);
                foreach (var sid in new[] { WellKnownSidType.LocalSystemSid, WellKnownSidType.BuiltinAdministratorsSid, WellKnownSidType.BuiltinUsersSid })
                    security.AddAccessRule(new FileSystemAccessRule(new SecurityIdentifier(sid, null),
                        sid == WellKnownSidType.BuiltinUsersSid ? FileSystemRights.ReadAndExecute : FileSystemRights.FullControl,
                        InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit, PropagationFlags.None, AccessControlType.Allow));
                Directory.CreateDirectory(DesktopStatus.DirectoryPath, security);
                if ((File.GetAttributes(DesktopStatus.DirectoryPath) & FileAttributes.ReparsePoint) != 0) return;
                Directory.SetAccessControl(DesktopStatus.DirectoryPath, security);
                if (color != "green" && color != "red" && color != "orange") color = "grey";
                string json = new JavaScriptSerializer().Serialize(new {
                    color = color, reason = reason ?? "Informations en attente", updated_at = DateTime.UtcNow.ToString("o") });
                string tmp = Path.Combine(DesktopStatus.DirectoryPath, Guid.NewGuid().ToString("N") + ".tmp");
                try
                {
                    using (var stream = new FileStream(tmp, FileMode.CreateNew, FileAccess.Write))
                    using (var writer = new StreamWriter(stream, new UTF8Encoding(false))) writer.Write(json);
                    if (File.Exists(DesktopStatus.FilePath)) File.Delete(DesktopStatus.FilePath);
                    File.Move(tmp, DesktopStatus.FilePath);
                }
                finally { if (File.Exists(tmp)) File.Delete(tmp); }
            }
            catch (Exception e) { Agent.Log("indicateur : " + e.Message); }
        }

        public static void Unavailable(string reason)
        {
            Publish("grey", reason);
        }

        public static void StartSessions()
        {
            if (!File.Exists(Executable) || !WindowsIdentity.GetCurrent().IsSystem) return;
            IntPtr sessions;
            int count;
            if (!WTSEnumerateSessions(IntPtr.Zero, 0, 1, out sessions, out count)) return;
            try
            {
                int size = Marshal.SizeOf(typeof(Session));
                for (int i = 0; i < count; i++)
                {
                    var session = (Session)Marshal.PtrToStructure(IntPtr.Add(sessions, i * size), typeof(Session));
                    if (session.Id == 0 || session.State != 0 || Running(session.Id)) continue;
                    Start(session.Id);
                }
            }
            finally { WTSFreeMemory(sessions); }
        }

        static bool Running(int session)
        {
            foreach (var process in Process.GetProcessesByName("loco-tray"))
                using (process)
                    try { if (process.SessionId == session && String.Equals(process.MainModule.FileName, Executable, StringComparison.OrdinalIgnoreCase)) return true; }
                    catch (Exception) { }
            return false;
        }

        static void Start(int session)
        {
            IntPtr token = IntPtr.Zero, environment = IntPtr.Zero;
            try
            {
                if (!WTSQueryUserToken(session, out token)) return;
                if (!CreateEnvironmentBlock(out environment, token, false)) return;
                var startup = new Startup { Size = Marshal.SizeOf(typeof(Startup)), Desktop = @"winsta0\default" };
                ProcessInfo process;
                if (!CreateProcessAsUser(token, Executable, new StringBuilder("\"" + Executable + "\""),
                    IntPtr.Zero, IntPtr.Zero, false, 0x400, environment, Path.GetDirectoryName(Executable), ref startup, out process))
                {
                    Agent.Log("indicateur de session : " + new Win32Exception(Marshal.GetLastWin32Error()).Message);
                    return;
                }
                CloseHandle(process.Process); CloseHandle(process.Thread);
            }
            finally { if (environment != IntPtr.Zero) DestroyEnvironmentBlock(environment); if (token != IntPtr.Zero) CloseHandle(token); }
        }

        public static void StopSessions()
        {
            foreach (var process in Process.GetProcessesByName("loco-tray"))
                using (process)
                    try
                    {
                        if (String.Equals(process.MainModule.FileName, Executable, StringComparison.OrdinalIgnoreCase)) process.Kill();
                    }
                    catch (Exception) { }
        }

        [StructLayout(LayoutKind.Sequential)] struct Session { public int Id; public IntPtr Name; public int State; }
        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)] struct Startup
        {
            public int Size; public string Reserved, Desktop, Title;
            public int X, Y, XSize, YSize, XCountChars, YCountChars, FillAttribute, Flags;
            public short ShowWindow, ReservedSize; public IntPtr ReservedPtr, Input, Output, Error;
        }
        [StructLayout(LayoutKind.Sequential)] struct ProcessInfo { public IntPtr Process, Thread; public int ProcessId, ThreadId; }
        [DllImport("wtsapi32.dll", SetLastError = true)] static extern bool WTSEnumerateSessions(IntPtr server, int reserved, int version, out IntPtr sessions, out int count);
        [DllImport("wtsapi32.dll")] static extern void WTSFreeMemory(IntPtr memory);
        [DllImport("wtsapi32.dll", SetLastError = true)] static extern bool WTSQueryUserToken(int session, out IntPtr token);
        [DllImport("userenv.dll", SetLastError = true)] static extern bool CreateEnvironmentBlock(out IntPtr environment, IntPtr token, bool inherit);
        [DllImport("userenv.dll")] static extern bool DestroyEnvironmentBlock(IntPtr environment);
        [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern bool CreateProcessAsUser(IntPtr token, string application, StringBuilder command, IntPtr processAttributes, IntPtr threadAttributes, bool inherit, int flags, IntPtr environment, string directory, ref Startup startup, out ProcessInfo process);
        [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
    }
}
