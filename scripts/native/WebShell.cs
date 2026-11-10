using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net.Sockets;
using System.Text;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

[assembly: System.Reflection.AssemblyTitle("大肥鱼")]
[assembly: System.Reflection.AssemblyDescription("大肥鱼 QQ 陪伴 · 原生 Windows 外壳")]
[assembly: System.Reflection.AssemblyProduct("大肥鱼")]
[assembly: System.Reflection.AssemblyCompany("Dafeiyu")]
[assembly: System.Reflection.AssemblyVersion("2.0.0.0")]
[assembly: System.Reflection.AssemblyFileVersion("2.0.0.0")]

namespace DafeiyuNative
{
    internal static class Program
    {
        [System.Runtime.InteropServices.DllImport("user32.dll")]
        private static extern bool SetProcessDPIAware();

        [STAThread]
        private static void Main(string[] args)
        {
            try { SetProcessDPIAware(); } catch { }
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            bool shot = args != null && Array.IndexOf(args, "--screenshot") >= 0;
            Application.Run(new ShellForm(shot));
        }
    }

    internal sealed class ShellForm : Form
    {
        private readonly string _root;
        private readonly int _port;
        private readonly bool _shot;
        private WebView2 _web;
        private System.Windows.Forms.Timer _timer;
        private NotifyIcon _tray;
        private bool _ready;
        private bool _navigating;
        private bool _backendStarted;
        private bool _stoppedByUser;
        private bool _timeoutShown;
        private DateTime _startedAt;
        private DateTime _nextRetry = DateTime.MinValue;
        private bool _capturing;
        private ulong _loadingNavId = ulong.MaxValue;
        private string _loadingKey = "";

        public ShellForm(bool shot)
        {
            _root = FindRoot();
            _port = ReadUiPort(_root);
            _shot = shot;

            Text = "大肥鱼 · 她今天怎么样";
            BackColor = Color.FromArgb(255, 247, 250);
            ForeColor = Color.FromArgb(74, 35, 52);
            Font = new Font("Microsoft YaHei UI", 9F);
            AutoScaleMode = AutoScaleMode.None;
            StartPosition = FormStartPosition.CenterScreen;
            MinimumSize = new Size(1100, 700);
            ClientSize = new Size(1280, 800);
            try { Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch { }

            _web = new WebView2();
            _web.Dock = DockStyle.Fill;
            _web.BackColor = Color.FromArgb(255, 247, 250);
            try { _web.DefaultBackgroundColor = Color.FromArgb(255, 247, 250); } catch { }
            Controls.Add(_web);

            SetupTray();
            Shown += OnShown;
            FormClosing += delegate
            {
                if (_timer != null) _timer.Stop();
                if (_tray != null)
                {
                    _tray.Visible = false;
                    _tray.Dispose();
                }
            };
        }

        private async void OnShown(object sender, EventArgs e)
        {
            try
            {
                string userData = Path.Combine(_root, "data", "webview2");
                Directory.CreateDirectory(userData);
                CoreWebView2Environment environment = await CoreWebView2Environment.CreateAsync(null, userData, null);
                await _web.EnsureCoreWebView2Async(environment);
                ConfigureWebView();
                Log("webview ready");

                _web.CoreWebView2.NavigateToString(LoadingHtml("正在启动", "协议端和机器人准备中……"));
                _web.CoreWebView2.NavigationCompleted += OnNavigationCompleted;
                Log("loading html");

                _timer = new System.Windows.Forms.Timer();
                _timer.Interval = 700;
                _timer.Tick += delegate { Tick(); };
                _timer.Start();
                Tick();
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "WebView2 初始化失败：\n" + ex.Message +
                    "\n\n这台机器需要 Microsoft Edge WebView2 Runtime。",
                    "大肥鱼", MessageBoxButtons.OK, MessageBoxIcon.Error);
                Close();
            }
        }

        private void ConfigureWebView()
        {
            CoreWebView2Settings settings = _web.CoreWebView2.Settings;
            settings.AreDevToolsEnabled = false;
            settings.AreDefaultContextMenusEnabled = false;
            settings.IsStatusBarEnabled = false;
            settings.AreBrowserAcceleratorKeysEnabled = false;
            settings.IsZoomControlEnabled = false;
            settings.IsPasswordAutosaveEnabled = false;
            settings.IsGeneralAutofillEnabled = false;

            _web.CoreWebView2.NewWindowRequested += delegate(object sender, CoreWebView2NewWindowRequestedEventArgs e)
            {
                e.Handled = true;
                OpenExternal(e.Uri);
            };
            _web.CoreWebView2.NavigationStarting += delegate(object sender, CoreWebView2NavigationStartingEventArgs e)
            {
                string shown = e.Uri;
                int query = shown.IndexOf('?');
                if (query >= 0) shown = shown.Substring(0, query);
                Log(e.Uri.StartsWith("data:", StringComparison.OrdinalIgnoreCase) ? "start data" : "start " + shown);
                if (e.Uri.StartsWith("data:", StringComparison.OrdinalIgnoreCase))
                {
                    _loadingNavId = e.NavigationId;
                    return;
                }
                if (e.Uri == "about:blank") return;
                if (e.Uri.StartsWith("http://127.0.0.1:", StringComparison.OrdinalIgnoreCase)) return;
                e.Cancel = true;
                OpenExternal(e.Uri);
            };
            _web.CoreWebView2.ProcessFailed += delegate
            {
                if (!IsDisposed) BeginInvoke(new Action(delegate
                {
                    ShowLoading("界面进程退出了", "点托盘菜单「刷新」或重新打开大肥鱼.exe。");
                }));
            };
        }

        private void OnNavigationCompleted(object sender, CoreWebView2NavigationCompletedEventArgs e)
        {
            if (e.NavigationId == _loadingNavId)
            {
                Log("loading done success=" + e.IsSuccess);
                return;
            }
            _navigating = false;
            Log("done success=" + e.IsSuccess + " status=" + e.WebErrorStatus);
            if (e.IsSuccess)
            {
                _ready = true;
                _loadingKey = "";
                if (_shot && !_capturing)
                {
                    _capturing = true;
                    System.Windows.Forms.Timer shot = new System.Windows.Forms.Timer();
                    shot.Interval = 4500;
                    shot.Tick += async delegate
                    {
                        shot.Stop();
                        shot.Dispose();
                        await CaptureAndClose();
                    };
                    shot.Start();
                }
                return;
            }
            _ready = false;
            _nextRetry = DateTime.UtcNow.AddSeconds(4);
            ShowLoading("控制台加载失败", "正在重试……");
        }

        private void Tick()
        {
            if (_stoppedByUser)
            {
                if (PortOpen(_port))
                {
                    ShowLoading("正在停止", "等机器人退出……");
                }
                else
                {
                    ShowLoading("已停止", "从托盘菜单「启动机器人」可以重新开始。");
                }
                return;
            }

            if (_navigating || _ready)
            {
                if (_ready && !PortOpen(_port))
                {
                    _ready = false;
                    _backendStarted = false;
                    ShowLoading("连接中断", "正在重新拉起机器人……");
                }
                return;
            }

            if (DateTime.UtcNow < _nextRetry) return;

            if (!PortOpen(_port))
            {
                if (!_backendStarted)
                {
                    if (StartBackend())
                    {
                        _backendStarted = true;
                        _startedAt = DateTime.Now;
                        _timeoutShown = false;
                        ShowLoading("正在启动", "协议端和机器人准备中……");
                    }
                }
                else if (!_timeoutShown && (DateTime.Now - _startedAt).TotalSeconds > 75)
                {
                    _timeoutShown = true;
                    ShowLoading("启动较慢", "可以看 logs\\bot.log，或从托盘菜单停止后重试。");
                }
                return;
            }

            string token = ReadToken();
            if (token.Length == 0) return;
            _navigating = true;
            Log("navigate token=" + token.Length);
            string url = "http://127.0.0.1:" + _port + "/?token=" + Uri.EscapeDataString(token);
            if (_shot) url += "&screenshot=1";
            _web.CoreWebView2.Navigate(url);
        }

        private bool StartBackend()
        {
            string script = Path.Combine(_root, "scripts", "supervise.ps1");
            if (!File.Exists(script)) return false;
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
                return true;
            }
            catch
            {
                return false;
            }
        }

        private void StopBackend()
        {
            string script = Path.Combine(_root, "scripts", "stop-bot.ps1");
            if (!File.Exists(script))
            {
                ShowLoading("找不到停止脚本", script);
                return;
            }
            _stoppedByUser = true;
            _ready = false;
            _backendStarted = false;
            _navigating = false;
            ShowLoading("正在停止", "等机器人退出……");
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
                ShowLoading("停止失败", ex.Message);
            }
        }

        private void StartBotFromTray()
        {
            _stoppedByUser = false;
            _backendStarted = false;
            _ready = false;
            _navigating = false;
            _timeoutShown = false;
            _nextRetry = DateTime.MinValue;
            ShowLoading("正在启动", "协议端和机器人准备中……");
            if (StartBackend())
            {
                _backendStarted = true;
                _startedAt = DateTime.Now;
            }
            else
            {
                ShowLoading("启动失败", "找不到 scripts\\supervise.ps1");
            }
        }

        private void ShowLoading(string title, string detail)
        {
            if (_web == null || _web.CoreWebView2 == null) return;
            string key = title + "\n" + detail;
            if (key == _loadingKey) return;
            _loadingKey = key;
            _web.CoreWebView2.NavigateToString(LoadingHtml(title, detail));
        }

        private async Task CaptureAndClose()
        {
            try
            {
                string docs = Path.Combine(_root, "docs");
                Directory.CreateDirectory(docs);
                using (FileStream stream = File.Create(Path.Combine(docs, "native-ui.png")))
                {
                    await _web.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png, stream);
                }
            }
            catch { }
            Close();
        }

        private void SetupTray()
        {
            _tray = new NotifyIcon();
            try { _tray.Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); }
            catch { _tray.Icon = SystemIcons.Application; }
            _tray.Text = "大肥鱼";
            _tray.Visible = true;
            ContextMenuStrip menu = new ContextMenuStrip();
            menu.Items.Add("显示控制台", null, delegate { ShowShell(); });
            menu.Items.Add("刷新界面", null, delegate { if (_web != null && _web.CoreWebView2 != null) _web.CoreWebView2.Reload(); });
            menu.Items.Add(new ToolStripSeparator());
            menu.Items.Add("启动机器人", null, delegate { StartBotFromTray(); });
            menu.Items.Add("停止机器人", null, delegate { StopBackend(); });
            menu.Items.Add("打开日志", null, delegate { OpenLog(); });
            menu.Items.Add(new ToolStripSeparator());
            menu.Items.Add("退出界面", null, delegate { Close(); });
            _tray.ContextMenuStrip = menu;
            _tray.DoubleClick += delegate { ShowShell(); };
        }

        private void ShowShell()
        {
            if (WindowState == FormWindowState.Minimized) WindowState = FormWindowState.Normal;
            Show();
            Activate();
        }

        private void OpenLog()
        {
            string path = Path.Combine(_root, "logs", "bot.log");
            try
            {
                if (File.Exists(path)) Process.Start("notepad.exe", "\"" + path + "\"");
                else MessageBox.Show("还没有日志：" + path, "大肥鱼", MessageBoxButtons.OK, MessageBoxIcon.Information);
            }
            catch (Exception ex)
            {
                MessageBox.Show(ex.Message, "大肥鱼", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }

        private static void OpenExternal(string uri)
        {
            try
            {
                if (uri.StartsWith("http://", StringComparison.OrdinalIgnoreCase) ||
                    uri.StartsWith("https://", StringComparison.OrdinalIgnoreCase))
                {
                    Process.Start(new ProcessStartInfo(uri) { UseShellExecute = true });
                }
            }
            catch { }
        }

        private bool PortOpen(int port)
        {
            try
            {
                using (TcpClient client = new TcpClient())
                {
                    IAsyncResult result = client.BeginConnect("127.0.0.1", port, null, null);
                    bool ok = result.AsyncWaitHandle.WaitOne(200);
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

        private string ReadToken()
        {
            try
            {
                string path = Path.Combine(_root, "data", "ui-token");
                if (!File.Exists(path)) return "";
                return File.ReadAllText(path, Encoding.ASCII).Trim();
            }
            catch
            {
                return "";
            }
        }

        private static int ReadUiPort(string root)
        {
            try
            {
                string env = Path.Combine(root, ".env");
                if (!File.Exists(env)) return 8090;
                foreach (string raw in File.ReadAllLines(env))
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

        private static string LoadingHtml(string title, string detail)
        {
            string html =
                "<!doctype html><html><head><meta charset=\"utf-8\"><style>" +
                "html,body{height:100%;margin:0;background:#fff7fa;color:#4a2334;font-family:'Microsoft YaHei UI',sans-serif}" +
                "body{display:flex;align-items:center;justify-content:center;background:radial-gradient(800px 500px at 70% 0%,rgba(255,190,212,.6),transparent 60%),#fff7fa}" +
                ".box{text-align:center;padding:32px 42px;border:1px solid rgba(198,105,136,.22);border-radius:22px;background:rgba(255,255,255,.78);box-shadow:0 24px 70px rgba(126,58,84,.14)}" +
                ".ring{width:34px;height:34px;margin:0 auto 20px;border-radius:50%;border:2px solid rgba(198,58,96,.18);border-top-color:#c63a60;animation:spin 1s linear infinite}" +
                "@keyframes spin{to{transform:rotate(360deg)}}h1{font-size:20px;font-weight:600;margin:0 0 8px}p{font-size:12px;color:#85596a;margin:0;line-height:1.8;white-space:pre-line}" +
                "</style></head><body><div class=\"box\"><div class=\"ring\"></div><h1>" + Escape(title) + "</h1><p>" + Escape(detail) + "</p></div></body></html>";
            return html;
        }

        private static string Escape(string text)
        {
            return (text ?? "").Replace("&", "&amp;").Replace("<", "&lt;").Replace(">", "&gt;");
        }

        private void Log(string text)
        {
            try
            {
                Directory.CreateDirectory(Path.Combine(_root, "logs"));
                File.AppendAllText(Path.Combine(_root, "logs", "shell.log"),
                    DateTime.Now.ToString("HH:mm:ss.fff") + " " + text + "\r\n", Encoding.UTF8);
            }
            catch { }
        }
    }
}
