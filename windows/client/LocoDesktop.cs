// Compagnon graphique non privilégié : RFB 3.8 sur un canal local privé du service loco-client.
// Aucune écoute TCP, aucun jeton loco, aucune action de mise à jour.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.IO.Pipes;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Windows.Forms;

namespace Loco
{
    internal static class DesktopProgram
    {
        [STAThread]
        public static int Main(string[] args)
        {
            if (args.Length != 2 || args[0] != "--pipe" || !Regex.IsMatch(args[1], "^loco-desktop-[0-9a-f]{64}$")) return 2;
            SetProcessDPIAware();
            try
            {
                using (var pipe = new NamedPipeClientStream(".", args[1], PipeDirection.InOut, PipeOptions.None))
                { pipe.Connect(15000); new RfbDesktop(pipe).Run(); }
                return 0;
            }
            catch (Exception) { return 1; }
        }
        [DllImport("user32.dll")] static extern bool SetProcessDPIAware();
    }
    internal sealed class RfbDesktop
    {
        readonly Stream stream;
        readonly Rectangle bounds;
        readonly int width, height;
        readonly Dictionary<uint, Input> pressed = new Dictionary<uint, Input>();
        readonly ImageCodecInfo jpeg = ImageCodecInfo.GetImageEncoders().First(c => c.MimeType == "image/jpeg");
        bool tight, rgbOrder;
        byte mouseMask;
        byte[] lastImage;
        readonly Stopwatch frameTime = Stopwatch.StartNew();
        public RfbDesktop(Stream stream)
        {
            this.stream = stream; bounds = SystemInformation.VirtualScreen;
            if (bounds.Width <= 0 || bounds.Height <= 0 || (long)bounds.Width * bounds.Height > 16777216) throw new IOException("Bureau indisponible");
            double scale = Math.Min(1, Math.Min(1920.0 / bounds.Width, 1080.0 / bounds.Height));
            width = Math.Max(1, (int)(bounds.Width * scale)); height = Math.Max(1, (int)(bounds.Height * scale));
        }
        byte[] Read(int n)
        {
            if (n < 0 || n > 1024 * 1024) throw new InvalidDataException();
            var buffer = new byte[n]; int position = 0;
            while (position < n) { int count = stream.Read(buffer, position, n - position); if (count == 0) throw new EndOfStreamException(); position += count; }
            return buffer;
        }
        void Write(byte[] data) { stream.Write(data, 0, data.Length); stream.Flush(); }
        static byte[] U16(int n) { return new[] { (byte)(n >> 8), (byte)n }; }
        static byte[] U32(uint n) { return new[] { (byte)(n >> 24), (byte)(n >> 16), (byte)(n >> 8), (byte)n }; }
        static int I16(byte[] b, int p) { return (b[p] << 8) | b[p + 1]; }
        static uint I32(byte[] b, int p) { return ((uint)b[p] << 24) | ((uint)b[p + 1] << 16) | ((uint)b[p + 2] << 8) | b[p + 3]; }
        static byte[] Join(params byte[][] parts)
        { byte[] data = new byte[parts.Sum(p => p.Length)]; int i = 0; foreach (var p in parts) { Buffer.BlockCopy(p, 0, data, i, p.Length); i += p.Length; } return data; }
        public void Run()
        {
            try
            {
                Write(Encoding.ASCII.GetBytes("RFB 003.008\n"));
                if (Encoding.ASCII.GetString(Read(12)) != "RFB 003.008\n") throw new InvalidDataException();
                Write(new byte[] { 1, 1 }); if (Read(1)[0] != 1) throw new InvalidDataException();
                Write(new byte[4]); Read(1);
                byte[] name = Encoding.UTF8.GetBytes(Environment.MachineName);
                Write(Join(U16(width), U16(height), new byte[] { 32, 24, 0, 1, 0, 255, 0, 255, 0, 255, 16, 8, 0, 0, 0, 0 }, U32((uint)name.Length), name));
                while (true)
                {
                    int kind = Read(1)[0];
                    if (kind == 0)
                    {
                        var p = Read(19);
                        if (p[3] != 32 || p[4] != 24 || p[5] != 0 || p[6] != 1 || I16(p, 7) != 255 || I16(p, 9) != 255 || I16(p, 11) != 255 ||
                            p[14] != 8 || !((p[13] == 16 && p[15] == 0) || (p[13] == 0 && p[15] == 16))) throw new InvalidDataException();
                        rgbOrder = p[13] == 0;
                    }
                    else if (kind == 2)
                    {
                        int count = I16(Read(3), 1); if (count > 128) throw new InvalidDataException();
                        var encodings = Read(count * 4); tight = false;
                        for (int i = 0; i < count; i++) if (I32(encodings, i * 4) == 7) tight = true;
                    }
                    else if (kind == 3) { Read(9); Frame(); }
                    else if (kind == 4) { var p = Read(7); Key(I32(p, 3), p[0] != 0); }
                    else if (kind == 5) { var p = Read(5); Pointer(p[0], I16(p, 1), I16(p, 3)); }
                    else if (kind == 6) { var p = Read(7); uint length = I32(p, 3); if (length > 1024 * 1024) throw new InvalidDataException(); Read((int)length); }
                    else throw new InvalidDataException();
                }
            }
            catch (EndOfStreamException) { }
            finally
            {
                foreach (var item in pressed.Values) { var input = item; input.Data.Keyboard.Flags |= 2; try { Send(input); } catch (IOException) { } }
                pressed.Clear();
                foreach (var b in new[] { new[] { 1, 4 }, new[] { 2, 64 }, new[] { 4, 16 } })
                    if ((mouseMask & b[0]) != 0) { var input = new Input(); input.Data.Mouse.Flags = (uint)b[1]; try { Send(input); } catch (IOException) { } }
            }
        }
        void Frame()
        {
            int wait = 150 - (int)frameTime.ElapsedMilliseconds; if (wait > 0) Thread.Sleep(wait); frameTime.Restart();
            IntPtr desktop = OpenInputDesktop(0, false, 1);
            if (desktop == IntPtr.Zero) throw new IOException("Session verrouillée ou écran sécurisé");
            try
            {
                var name = new StringBuilder(256); int needed;
                if (!GetUserObjectInformation(desktop, 2, name, 512, out needed) || !String.Equals(name.ToString(), "Default", StringComparison.OrdinalIgnoreCase))
                    throw new IOException("Écran sécurisé");
            }
            finally { CloseDesktop(desktop); }
            using (var source = new Bitmap(bounds.Width, bounds.Height, PixelFormat.Format32bppRgb))
            using (var target = new Bitmap(width, height, PixelFormat.Format32bppArgb))
            {
                using (var graphics = Graphics.FromImage(source)) graphics.CopyFromScreen(bounds.Location, Point.Empty, bounds.Size);
                using (var graphics = Graphics.FromImage(target)) graphics.DrawImage(source, new Rectangle(0, 0, width, height));
                byte[] data;
                if (tight)
                {
                    using (var memory = new MemoryStream())
                    using (var parameters = new EncoderParameters(1))
                    { parameters.Param[0] = new EncoderParameter(System.Drawing.Imaging.Encoder.Quality, 65L); target.Save(memory, jpeg, parameters); data = memory.ToArray(); }
                    if (data.Length >= 4194304) throw new IOException("Image trop volumineuse");
                }
                else
                {
                    var bitmap = target.LockBits(new Rectangle(0, 0, width, height), ImageLockMode.ReadOnly, PixelFormat.Format32bppArgb);
                    try
                    {
                        data = new byte[width * height * 4];
                        for (int y = 0; y < height; y++) Marshal.Copy(IntPtr.Add(bitmap.Scan0, y * bitmap.Stride), data, y * width * 4, width * 4);
                        if (rgbOrder) for (int i = 0; i < data.Length; i += 4) { byte b = data[i]; data[i] = data[i + 2]; data[i + 2] = b; }
                    }
                    finally { target.UnlockBits(bitmap); }
                }
                if (lastImage != null && data.SequenceEqual(lastImage)) { Write(new byte[4]); return; }
                lastImage = data;
                byte[] header = Join(new byte[] { 0, 0, 0, 1, 0, 0, 0, 0 }, U16(width), U16(height), U32(tight ? 7u : 0u));
                Write(header);
                if (tight)
                {
                    int size = data.Length;
                    var prefix = new List<byte> { 0x90, (byte)((size & 127) | (size > 127 ? 128 : 0)) };
                    if (size > 127) prefix.Add((byte)(((size >> 7) & 127) | (size > 16383 ? 128 : 0)));
                    if (size > 16383) prefix.Add((byte)(size >> 14));
                    Write(prefix.ToArray());
                }
                Write(data);
            }
        }
        void Pointer(byte mask, int x, int y)
        {
            long px = bounds.Left + Math.Min(width - 1, x) * (long)bounds.Width / width;
            long py = bounds.Top + Math.Min(height - 1, y) * (long)bounds.Height / height;
            var input = new Input { Type = 0 }; input.Data.Mouse.X = (int)((px - bounds.Left) * 65535 / Math.Max(1, bounds.Width - 1));
            input.Data.Mouse.Y = (int)((py - bounds.Top) * 65535 / Math.Max(1, bounds.Height - 1)); input.Data.Mouse.Flags = 0x8000 | 0x4000 | 1;
            Send(input);
            foreach (var b in new[] { new[] { 1, 2, 4 }, new[] { 2, 32, 64 }, new[] { 4, 8, 16 } })
                if (((mouseMask ^ mask) & b[0]) != 0) { input.Data.Mouse.Flags = (uint)((mask & b[0]) != 0 ? b[1] : b[2]); Send(input); }
            foreach (var b in new[] { new[] { 8, 120 }, new[] { 16, -120 } })
                if ((mask & b[0]) != 0 && (mouseMask & b[0]) == 0) { input.Data.Mouse.Flags = 0x0800; input.Data.Mouse.Data = unchecked((uint)b[1]); Send(input); }
            mouseMask = mask;
        }
        void Key(uint sym, bool down)
        {
            Input input;
            if (!down)
            {
                if (!pressed.TryGetValue(sym, out input)) return;
                input.Data.Keyboard.Flags |= 2; Send(input); pressed.Remove(sym); return;
            }
            ushort vk = 0;
            var special = new Dictionary<uint, ushort> { {0xff08,8},{0xff09,9},{0xff0d,13},{0xff1b,27},{0xff50,36},{0xff51,37},{0xff52,38},{0xff53,39},{0xff54,40},
                {0xff55,33},{0xff56,34},{0xff57,35},{0xff63,45},{0xffff,46},{0xffe1,160},{0xffe2,161},{0xffe3,162},{0xffe4,163},
                {0xffe9,164},{0xffea,165},{0xffeb,91},{0xffec,92},{0xffe5,20},{0xff7f,144},{0xff14,145} };
            if (special.ContainsKey(sym)) vk = special[sym];
            else if (sym >= 0xffbe && sym <= 0xffd5) vk = (ushort)(112 + sym - 0xffbe);
            input = new Input { Type = 1 };
            if (vk != 0) { input.Data.Keyboard.Key = vk; if (vk >= 33 && vk <= 46 || vk == 163 || vk == 165 || vk == 91 || vk == 92) input.Data.Keyboard.Flags = 1; }
            else
            {
                uint scalar = sym >= 0x01000000 ? sym - 0x01000000 : sym;
                if (scalar < 32 || scalar > 0xffff) return;
                if (pressed.ContainsKey(0xffe3) || pressed.ContainsKey(0xffe4) || pressed.ContainsKey(0xffe9) || pressed.ContainsKey(0xffea) ||
                    pressed.ContainsKey(0xffeb) || pressed.ContainsKey(0xffec))
                { short mapped = VkKeyScan((char)scalar); if (mapped == -1) return; input.Data.Keyboard.Key = (ushort)(mapped & 255); }
                else { input.Data.Keyboard.Scan = (ushort)scalar; input.Data.Keyboard.Flags = 4; }
            }
            Send(input); pressed[sym] = input;
        }
        static void Send(Input input) { if (SendInput(1, new[] { input }, Marshal.SizeOf(typeof(Input))) != 1) throw new IOException("Saisie bloquée par le bureau Windows"); }
        [StructLayout(LayoutKind.Sequential)] struct Input { public uint Type; public InputData Data; }
        [StructLayout(LayoutKind.Explicit)] struct InputData { [FieldOffset(0)] public MouseInput Mouse; [FieldOffset(0)] public KeyboardInput Keyboard; }
        [StructLayout(LayoutKind.Sequential)] struct MouseInput { public int X, Y; public uint Data, Flags, Time; public UIntPtr Extra; }
        [StructLayout(LayoutKind.Sequential)] struct KeyboardInput { public ushort Key, Scan; public uint Flags, Time; public UIntPtr Extra; }
        [DllImport("user32.dll")] static extern uint SendInput(uint count, Input[] input, int size);
        [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern short VkKeyScan(char c);
        [DllImport("user32.dll", SetLastError = true)] static extern IntPtr OpenInputDesktop(uint flags, bool inherit, uint access);
        [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern bool GetUserObjectInformation(IntPtr handle, int index, StringBuilder data, int length, out int needed);
        [DllImport("user32.dll")] static extern bool CloseDesktop(IntPtr desktop);
    }
}
