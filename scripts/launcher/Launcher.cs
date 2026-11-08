using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net.Sockets;
using System.Reflection;
using System.Text;
using System.Windows.Forms;

[assembly: AssemblyTitle("大肥鱼")]
[assembly: AssemblyDescription("大肥鱼 QQ 陪伴 · 一键启动器")]
[assembly: AssemblyProduct("大肥鱼")]
[assembly: AssemblyCompany("Dafeiyu")]
[assembly: AssemblyVersion("1.0.0.0")]
[assembly: AssemblyFileVersion("1.0.0.0")]

namespace DafeiyuLauncher
{
    internal static class Program
    {
        [System.Runtime.InteropServices.DllImport("user32.dll")]
        private static extern bool SetProcessDPIAware();

        [STAThread]
        private static void Main()
        {
            try { SetProcessDPIAware(); } catch { }
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new LauncherForm());
        }
    }

    internal sealed class LauncherForm : Form
    {
        private static readonly Color Bg = Color.FromArgb(5, 17, 31);
        private static readonly Color Card = Color.FromArgb(10, 31, 50);
        private static readonly Color Line = Color.FromArgb(30, 69, 96);
        private static readonly Color Ink = Color.FromArgb(234, 246, 255);
        private static readonly Color Muted = Color.FromArgb(143, 179, 204);
        private static readonly Color Cyan = Color.FromArgb(90, 215, 255);
        private static readonly Color Green = Color.FromArgb(115, 230, 177);
        private static readonly Color Amber = Color.FromArgb(255, 196, 107);
        private static readonly Color Rose = Color.FromArgb(255, 126, 157);

        private readonly string _root;
        private readonly int _uiPort;
        private readonly Label _dot;
        private readonly Label _status;
        private readonly Button _openButton;
        private readonly Button _startButton;
        private readonly Button _stopButton;
        private readonly Button _logButton;
        private readonly Timer _timer;
        private DateTime _startedAt;
        private bool _ready;
        private bool _opened;
        private bool _startedOnce;

        public LauncherForm()
        {
            _root = FindRoot();
            _uiPort = ReadUiPort(_root);

            Text = "大肥鱼 · 启动器";
            StartPosition = FormStartPosition.CenterScreen;
            FormBorderStyle = FormBorderStyle.FixedSingle;
            MaximizeBox = false;
            AutoScaleMode = AutoScaleMode.None;
            BackColor = Bg;
            ForeColor = Ink;
            Font = new Font("Microsoft YaHei UI", 9F, FontStyle.Regular, GraphicsUnit.Point);

            PictureBox art = new PictureBox();
            art.Left = 20;
            art.Top = 18;
            art.Width = 104;
            art.Height = 138;
            art.SizeMode = PictureBoxSizeMode.Zoom;
            art.BackColor = Color.Transparent;
            try { art.Image = Image.FromFile(Path.Combine(_root, "qqbot", "ui", "assets", "dafeiyu.png")); }
            catch { }
            Controls.Add(art);

            Label title = new Label();
            title.Left = 142;
            title.Top = 24;
            title.Width = 300;
            title.Height = 36;
            title.Text = "大肥鱼";
            title.ForeColor = Ink;
            title.Font = new Font("Microsoft YaHei UI", 20F, FontStyle.Bold, GraphicsUnit.Point);
            Controls.Add(title);

            Label subtitle = new Label();
            subtitle.Left = 145;
            subtitle.Top = 64;
            subtitle.Width = 300;
            subtitle.Height = 22;
            subtitle.Text = "QQ 陪伴 · 一键启动";
            subtitle.ForeColor = Cyan;
            subtitle.Font = new Font("Microsoft YaHei UI", 9F, FontStyle.Regular, GraphicsUnit.Point);
            Controls.Add(subtitle);

            Label note = new Label();
            note.Left = 145;
            note.Top = 94;
            note.Width = 300;
            note.Height = 52;
            note.Text = "协议端、机器人和控制台会一起起来。\n数据都留在项目目录，不写 C 盘。";
            note.ForeColor = Muted;
            note.Font = new Font("Microsoft YaHei UI", 8.5F, FontStyle.Regular, GraphicsUnit.Point);
            Controls.Add(note);

            Panel rule = new Panel();
            rule.Left = 20;
            rule.Top = 168;
            rule.Width = 424;
            rule.Height = 1;
            rule.BackColor = Line;
            Controls.Add(rule);

            _dot = new Label();
            _dot.Left = 20;
            _dot.Top = 184;
            _dot.Width = 18;
            _dot.Height = 20;
            _dot.Text = "●";
            _dot.ForeColor = Amber;
            _dot.Font = new Font("Microsoft YaHei UI", 10F, FontStyle.Regular, GraphicsUnit.Point);
            Controls.Add(_dot);

            _status = new Label();
            _status.Left = 42;
            _status.Top = 183;
            _status.Width = 402;
            _status.Height = 42;
            _status.Text = "正在检查运行状态……";
            _status.ForeColor = Ink;
            _status.Font = new Font("Microsoft YaHei UI", 9F, FontStyle.Regular, GraphicsUnit.Point);
            Controls.Add(_status);

            _openButton = MakeButton("打开控制台", Cyan, Color.FromArgb(4, 32, 44), 20, 236, 146, 36);
            _openButton.Enabled = false;
            _openButton.Click += delegate { OpenConsole(); };
            Controls.Add(_openButton);

            _startButton = MakeButton("启动", Card, Ink, 174, 236, 84, 36);
            _startButton.Click += delegate { StartSupervisor(); };
            Controls.Add(_startButton);

            _stopButton = MakeButton("停止机器人", Color.FromArgb(42, 20, 32), Rose, 266, 236, 178, 36);
            _stopButton.Enabled = false;
            _stopButton.Click += delegate { StopBot(); };
            Controls.Add(_stopButton);

            _logButton = MakeButton("看日志", Card, Muted, 20, 286, 84, 28);
            _logButton.Font = new Font("Microsoft YaHei UI", 8F, FontStyle.Regular, GraphicsUnit.Point);
            _logButton.Click += delegate { OpenLogs(); };
            Controls.Add(_logButton);

            Label foot = new Label();
            foot.Left = 116;
            foot.Top = 292;
            foot.Width = 330;
            foot.Height = 20;
            foot.Text = "关闭窗口不会停止机器人，停止请点上面的按钮";
            foot.ForeColor = Muted;
            foot.Font = new Font("Microsoft YaHei UI", 8F, FontStyle.Regular, GraphicsUnit.Point);
            Controls.Add(foot);

            float scale = 1f;
            using (Graphics graphics = CreateGraphics())
            {
                scale = graphics.DpiX / 96f;
                if (scale < 1f) scale = 1f;
            }
            ClientSize = new Size((int)(468 * scale), (int)(344 * scale));

            _timer = new Timer();
            _timer.Interval = 1000;
            _timer.Tick += delegate { Check(); };
            Shown += delegate { Initialize(); };
            FormClosed += delegate { _timer.Stop(); _timer.Dispose(); };
        }

        private static Button MakeButton(string text, Color back, Color fore, int x, int y, int w, int h)
        {
            Button button = new Button();
            button.Text = text;
            button.Left = x;
            button.Top = y;
            button.Width = w;
            button.Height = h;
            button.FlatStyle = FlatStyle.Flat;
            button.FlatAppearance.BorderSize = 1;
            button.FlatAppearance.BorderColor = Line;
            button.BackColor = back;
            button.ForeColor = fore;
            button.Font = new Font("Microsoft YaHei UI", 9F, FontStyle.Bold, GraphicsUnit.Point);
            button.Cursor = Cursors.Hand;
            return button;
        }

        private void Initialize()
        {
            if (!File.Exists(Path.Combine(_root, "tools", "python", "python.exe")))
            {
                SetStatus("找不到 tools\\python\\python.exe。请把启动器放在项目根目录。", Rose);
                _startButton.Enabled = false;
                return;
            }
            if (!File.Exists(Path.Combine(_root, "scripts", "supervise.ps1")))
            {
                SetStatus("找不到 scripts\\supervise.ps1。请把启动器放在项目根目录。", Rose);
                _startButton.Enabled = false;
                return;
            }

            if (IsPortOpen(_uiPort))
            {
                SetReady();
            }
            else
            {
                StartSupervisor();
            }
            _timer.Start();
        }

        private void StartSupervisor()
        {
            string script = Path.Combine(_root, "scripts", "supervise.ps1");
            if (!File.Exists(script))
            {
                SetStatus("找不到启动脚本 scripts\\supervise.ps1。", Rose);
                return;
            }

            _startedAt = DateTime.Now;
            _startedOnce = true;
            _opened = false;
            _openButton.Enabled = false;
            _stopButton.Enabled = true;
            _startButton.Enabled = false;
            SetStatus("正在启动协议端和机器人，首次可能需要 20–40 秒……", Amber);

            try
            {
                ProcessStartInfo info = new ProcessStartInfo();
                info.FileName = "powershell.exe";
                info.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"" + script + "\"";
                info.WorkingDirectory = _root;
                info.UseShellExecute = false;
                info.CreateNoWindow = true;
                info.WindowStyle = ProcessWindowStyle.Hidden;
                Process.Start(info);
            }
            catch (Exception ex)
            {
                SetStatus("启动失败：" + ex.Message, Rose);
                _startButton.Enabled = true;
            }
        }

        private void StopBot()
        {
            string script = Path.Combine(_root, "scripts", "stop-bot.ps1");
            if (!File.Exists(script))
            {
                SetStatus("找不到 scripts\\stop-bot.ps1。", Rose);
                return;
            }

            _stopButton.Enabled = false;
            SetStatus("正在停止机器人……", Amber);
            try
            {
                ProcessStartInfo info = new ProcessStartInfo();
                info.FileName = "powershell.exe";
                info.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"" + script + "\"";
                info.WorkingDirectory = _root;
                info.UseShellExecute = false;
                info.CreateNoWindow = true;
                info.WindowStyle = ProcessWindowStyle.Hidden;
                using (Process process = Process.Start(info))
                {
                    process.WaitForExit(15000);
                }
            }
            catch (Exception ex)
            {
                SetStatus("停止失败：" + ex.Message, Rose);
                _stopButton.Enabled = true;
                return;
            }

            _ready = false;
            _opened = false;
            _openButton.Enabled = false;
            _stopButton.Enabled = false;
            _startButton.Enabled = true;
            SetStatus("机器人已停止（NapCat 仍保持登录）。点“启动”可以再开。", Muted);
        }

        private void Check()
        {
            if (IsPortOpen(_uiPort))
            {
                if (!_ready) SetReady();
                return;
            }

            if (_ready)
            {
                _ready = false;
                _openButton.Enabled = false;
                _stopButton.Enabled = true;
                _startButton.Enabled = true;
                SetStatus("控制台端口已断开，机器人可能正在重启……", Amber);
            }

            if (_startedOnce)
            {
                double seconds = (DateTime.Now - _startedAt).TotalSeconds;
                if (seconds > 90)
                {
                    SetStatus("启动较慢，点“看日志”查看 logs\\bot.log。", Amber);
                }
                else if (seconds > 3)
                {
                    SetStatus("正在启动协议端和机器人…… " + ((int)seconds) + "s", Amber);
                }
            }
        }

        private void SetReady()
        {
            _ready = true;
            _openButton.Enabled = true;
            _stopButton.Enabled = true;
            _startButton.Enabled = false;
            SetStatus("控制台已就绪，正在打开浏览器……", Green);
            if (!_opened)
            {
                _opened = true;
                OpenConsole();
            }
        }

        private void SetStatus(string text, Color color)
        {
            _status.Text = text;
            _dot.ForeColor = color;
        }

        private void OpenConsole()
        {
            string token = "";
            try
            {
                string tokenPath = Path.Combine(_root, "data", "ui-token");
                if (File.Exists(tokenPath)) token = File.ReadAllText(tokenPath, Encoding.ASCII).Trim();
            }
            catch { }

            string url = "http://127.0.0.1:" + _uiPort + "/";
            if (token.Length > 0) url += "?token=" + Uri.EscapeDataString(token);
            try
            {
                ProcessStartInfo info = new ProcessStartInfo(url);
                info.UseShellExecute = true;
                Process.Start(info);
            }
            catch (Exception ex)
            {
                SetStatus("打开浏览器失败：" + ex.Message, Rose);
            }
        }

        private void OpenLogs()
        {
            string path = Path.Combine(_root, "logs", "bot.log");
            try
            {
                if (File.Exists(path)) Process.Start("notepad.exe", "\"" + path + "\"");
                else Process.Start("explorer.exe", "\"" + Path.Combine(_root, "logs") + "\"");
            }
            catch (Exception ex)
            {
                SetStatus("打开日志失败：" + ex.Message, Rose);
            }
        }

        private static bool IsPortOpen(int port)
        {
            try
            {
                using (TcpClient client = new TcpClient())
                {
                    IAsyncResult result = client.BeginConnect("127.0.0.1", port, null, null);
                    bool ok = result.AsyncWaitHandle.WaitOne(250);
                    if (!ok) return false;
                    client.EndConnect(result);
                    return true;
                }
            }
            catch
            {
                return false;
            }
        }

        private static int ReadUiPort(string root)
        {
            try
            {
                string env = Path.Combine(root, ".env");
                if (!File.Exists(env)) return 8090;
                string[] lines = File.ReadAllLines(env);
                foreach (string raw in lines)
                {
                    string line = raw.Trim();
                    if (line.StartsWith("UI_PORT=", StringComparison.OrdinalIgnoreCase))
                    {
                        int port;
                        if (int.TryParse(line.Substring("UI_PORT=".Length).Trim(), out port) && port > 0 && port < 65536)
                            return port;
                    }
                }
            }
            catch { }
            return 8090;
        }

        private static string FindRoot()
        {
            DirectoryInfo dir = new DirectoryInfo(AppDomain.CurrentDomain.BaseDirectory);
            for (int i = 0; i < 5 && dir != null; i++)
            {
                if (File.Exists(Path.Combine(dir.FullName, "qqbot", "main.py")))
                    return dir.FullName;
                dir = dir.Parent;
            }
            return AppDomain.CurrentDomain.BaseDirectory;
        }
    }
}


