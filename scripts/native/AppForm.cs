using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using System.Windows.Forms;

[assembly: System.Reflection.AssemblyTitle("大肥鱼")]
[assembly: System.Reflection.AssemblyDescription("大肥鱼 QQ 陪伴 · 原生 Windows 控制台")]
[assembly: System.Reflection.AssemblyProduct("大肥鱼")]
[assembly: System.Reflection.AssemblyCompany("Dafeiyu")]
[assembly: System.Reflection.AssemblyVersion("1.1.0.0")]
[assembly: System.Reflection.AssemblyFileVersion("1.1.0.0")]

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
            bool dump = args != null && args.Length > 0 && args[0] == "--dump";
            bool shot = args != null && Array.IndexOf(args, "--screenshot") >= 0;
            Application.Run(new AppForm(dump, shot));
        }
    }

    internal sealed class AppForm : Form
    {
        private readonly string _root;
        private readonly int _port;
        private readonly Api _api;
        private readonly bool _dump;
        private readonly bool _screenshot;
        private System.Windows.Forms.Timer _shotTimer;
        private readonly Dictionary<string, Panel> _pages = new Dictionary<string, Panel>();
        private readonly List<NavItem> _navs = new List<NavItem>();

        private Panel _pageHost;
        private Label _pageTitle;
        private Label _crumb;
        private Label _statusPill;
        private Label _sideState;
        private Label _sideModel;
        private Label _greeting;
        private Label _moodLine;
        private Label _heroQq;
        private Label _heroPid;
        private MoodBar _missBar;
        private MoodBar _warmBar;
        private MoodBar _tiredBar;
        private Label _moodTag;
        private Label _statMessages;
        private Label _statUser;
        private Label _statBot;
        private Label _statProactive;
        private Label _quotaProactive;
        private Label _quotaOther;
        private Label _alertCount;
        private FlowLayoutPanel _alertList;
        private FlowLayoutPanel _recentList;
        private FlowLayoutPanel _chatList;
        private Label _runtimeTag;
        private CheckBox _proactiveBox;
        private CheckBox _qzoneBox;
        private CheckBox _douyinBox;
        private Button _emergency;
        private TextBox _personaText;
        private TextBox _personaNote;
        private Label _personaCount;
        private Label _personaUpdated;
        private Label _personaWarnings;
        private ListBox _versionList;
        private TextBox _qqBox;
        private TextBox _baseBox;
        private TextBox _modelBox;
        private TextBox _timeoutBox;
        private TextBox _keyBox;
        private Label _keyHint;
        private CheckBox _configProactive;
        private NumericUpDown _configProactiveMax;
        private CheckBox _configQzone;
        private CheckBox _configQzoneDry;
        private NumericUpDown _configQzoneMax;
        private CheckBox _configDouyin;
        private CheckBox _configDouyinDry;
        private NumericUpDown _configDouyinMax;
        private NumericUpDown _configDouyinShare;
        private TextBox _logBox;
        private ListView _auditList;
        private System.Windows.Forms.Timer _timer;
        private bool _busy;
        private bool _loading;
        private bool _ready;
        private bool _started;
        private bool _emergencyOn;
        private DateTime _startedAt;
        private string _current = "overview";

        public AppForm(bool dump, bool screenshot)
        {
            _root = FindRoot();
            _port = ReadUiPort(_root);
            _api = new Api(_port, _root);
            _dump = dump;
            _screenshot = screenshot;

            Text = "大肥鱼 · 控制台";
            BackColor = Theme.Bg;
            ForeColor = Theme.Ink;
            Font = Theme.Ui(9F);
            AutoScaleMode = AutoScaleMode.None;
            StartPosition = FormStartPosition.CenterScreen;
            FormBorderStyle = FormBorderStyle.Sizable;
            MaximizeBox = true;
            MinimumSize = new Size(980, 660);
            BuildUi();
            ClientSize = new Size(1180, 800);

            _timer = new System.Windows.Forms.Timer();
            _timer.Interval = 1500;
            _timer.Tick += delegate { Tick(); };
            Shown += delegate
            {
                if (_dump)
                {
                    foreach (string key in new string[] { "overview", "chat", "persona", "config", "logs" })
                    {
                        SwitchPage(key);
                        PerformLayout();
                        DumpLayout(this, key);
                    }
                    Close();
                    return;
                }
                Startup();
                if (_screenshot)
                {
                    _shotTimer = new System.Windows.Forms.Timer();
                    _shotTimer.Interval = 4500;
                    _shotTimer.Tick += delegate
                    {
                        _shotTimer.Stop();
                        CaptureSelf();
                        Close();
                    };
                    _shotTimer.Start();
                }
            };
            FormClosed += delegate { _timer.Stop(); _timer.Dispose(); };
        }

        // ---------------- layout ----------------

        private void BuildUi()
        {
            TableLayoutPanel root = new TableLayoutPanel();
            root.Dock = DockStyle.Fill;
            root.ColumnCount = 2;
            root.RowCount = 1;
            root.BackColor = Theme.Bg;
            root.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 220F));
            root.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100F));
            Controls.Add(root);

            root.Controls.Add(BuildSidebar(), 0, 0);

            Panel content = new Panel();
            content.Dock = DockStyle.Fill;
            content.BackColor = Theme.Bg;
            content.Padding = new Padding(24, 16, 24, 18);
            root.Controls.Add(content, 1, 0);

            content.Controls.Add(BuildTopbar());
            _pageHost = new Panel();
            _pageHost.Dock = DockStyle.Fill;
            _pageHost.BackColor = Theme.Bg;
            content.Controls.Add(_pageHost);
            _pageHost.BringToFront();

            AddPage("overview", BuildOverview());
            AddPage("chat", BuildChat());
            AddPage("persona", BuildPersona());
            AddPage("config", BuildConfig());
            AddPage("logs", BuildLogs());
            SwitchPage("overview");
        }

        private Panel BuildSidebar()
        {
            Panel side = new Panel();
            side.Dock = DockStyle.Fill;
            side.BackColor = Theme.Side;
            side.Padding = new Padding(16, 20, 16, 16);

            Panel brand = new Panel();
            brand.Dock = DockStyle.Top;
            brand.Height = 64;
            PictureBox avatar = new PictureBox();
            avatar.Left = 0;
            avatar.Top = 0;
            avatar.Width = 46;
            avatar.Height = 46;
            avatar.SizeMode = PictureBoxSizeMode.Zoom;
            avatar.BackColor = Color.Transparent;
            try { avatar.Image = Image.FromFile(Path.Combine(_root, "qqbot", "ui", "assets", "dafeiyu.png")); }
            catch { }
            brand.Controls.Add(avatar);
            Label name = new Label();
            name.Left = 56;
            name.Top = 3;
            name.Width = 130;
            name.Height = 24;
            name.Text = "大肥鱼";
            name.ForeColor = Theme.Ink;
            name.Font = Theme.UiBold(14F);
            brand.Controls.Add(name);
            Label sub = new Label();
            sub.Left = 57;
            sub.Top = 29;
            sub.Width = 130;
            sub.Height = 18;
            sub.Text = "只属于你的控制台";
            sub.ForeColor = Theme.Muted;
            sub.Font = Theme.Ui(8F);
            brand.Controls.Add(sub);

            FlowLayoutPanel nav = new FlowLayoutPanel();
            nav.Dock = DockStyle.Top;
            nav.Height = 252;
            nav.WrapContents = false;
            nav.FlowDirection = FlowDirection.TopDown;
            nav.BackColor = Theme.Side;
            nav.Padding = new Padding(0, 10, 0, 0);
            string[] names = { "总览", "对话", "人格", "配置", "日志" };
            string[] keys = { "overview", "chat", "persona", "config", "logs" };
            for (int i = 0; i < keys.Length; i++)
            {
                NavItem item = new NavItem(names[i]);
                item.Width = 186;
                item.Margin = new Padding(0, 0, 0, 4);
                string key = keys[i];
                item.Click += delegate { SwitchPage(key); };
                _navs.Add(item);
                nav.Controls.Add(item);
            }

            Panel bottom = new Panel();
            bottom.Dock = DockStyle.Bottom;
            bottom.Height = 96;
            bottom.BackColor = Theme.Side;
            Label label = new Label();
            label.Left = 2;
            label.Top = 4;
            label.Width = 180;
            label.Height = 18;
            label.Text = "运行状态";
            label.ForeColor = Theme.Faint;
            label.Font = Theme.Ui(7.5F);
            bottom.Controls.Add(label);
            _sideState = new Label();
            _sideState.Left = 2;
            _sideState.Top = 26;
            _sideState.Width = 180;
            _sideState.Height = 22;
            _sideState.Text = "● 连接中";
            _sideState.ForeColor = Theme.Amber;
            _sideState.Font = Theme.Ui(9F);
            bottom.Controls.Add(_sideState);
            _sideModel = new Label();
            _sideModel.Left = 2;
            _sideModel.Top = 52;
            _sideModel.Width = 180;
            _sideModel.Height = 18;
            _sideModel.Text = "模型 —";
            _sideModel.ForeColor = Theme.Muted;
            _sideModel.Font = Theme.Ui(8F);
            bottom.Controls.Add(_sideModel);
            Label credit = new Label();
            credit.Left = 2;
            credit.Top = 72;
            credit.Width = 190;
            credit.Height = 16;
            credit.Text = "YunYueSama · 署名许可 1.0";
            credit.ForeColor = Color.FromArgb(78, 112, 136);
            credit.Font = Theme.Ui(7F);
            bottom.Controls.Add(credit);
            side.Controls.Add(bottom);
            side.Controls.Add(nav);
            side.Controls.Add(brand);

            return side;
        }

        private Panel BuildTopbar()
        {
            Panel bar = new Panel();
            bar.Dock = DockStyle.Top;
            bar.Height = 68;
            bar.BackColor = Theme.Bg;

            _pageTitle = new Label();
            _pageTitle.Left = 0;
            _pageTitle.Top = 4;
            _pageTitle.Width = 650;
            _pageTitle.Height = 32;
            _pageTitle.Text = "今天也在这里";
            _pageTitle.ForeColor = Theme.Ink;
            _pageTitle.Font = Theme.UiBold(18F);
            bar.Controls.Add(_pageTitle);

            _crumb = new Label();
            _crumb.Left = 2;
            _crumb.Top = 38;
            _crumb.Width = 500;
            _crumb.Height = 18;
            _crumb.Text = "控制台 / 总览";
            _crumb.ForeColor = Theme.Faint;
            _crumb.Font = Theme.Ui(8F);
            bar.Controls.Add(_crumb);

            FlowLayoutPanel actions = new FlowLayoutPanel();
            actions.Dock = DockStyle.Right;
            actions.Width = 238;
            actions.FlowDirection = FlowDirection.RightToLeft;
            actions.WrapContents = false;
            actions.BackColor = Theme.Bg;
            actions.Padding = new Padding(0, 12, 0, 0);

            FlatButton refresh = new FlatButton("刷新", false);
            refresh.Width = 64;
            refresh.Height = 32;
            refresh.Margin = new Padding(8, 0, 0, 0);
            refresh.Click += delegate { RefreshAll(); };
            actions.Controls.Add(refresh);

            _statusPill = new Label();
            _statusPill.Width = 104;
            _statusPill.Height = 30;
            _statusPill.Text = "● 连接中";
            _statusPill.TextAlign = ContentAlignment.MiddleCenter;
            _statusPill.ForeColor = Theme.Amber;
            _statusPill.BackColor = Theme.Card2;
            _statusPill.Font = Theme.UiBold(8.5F);
            _statusPill.Margin = new Padding(0, 1, 0, 0);
            actions.Controls.Add(_statusPill);
            bar.Controls.Add(actions);
            actions.BringToFront();
            return bar;
        }

        private void AddPage(string key, Panel page)
        {
            page.Dock = DockStyle.Fill;
            page.Visible = false;
            _pageHost.Controls.Add(page);
            _pages[key] = page;
        }

        private Panel NewPage()
        {
            Panel page = new Panel();
            page.Dock = DockStyle.Fill;
            page.BackColor = Theme.Bg;
            page.Padding = new Padding(0, 2, 0, 0);
            return page;
        }

        private void SwitchPage(string key)
        {
            if (!_pages.ContainsKey(key)) return;
            _current = key;
            foreach (KeyValuePair<string, Panel> pair in _pages)
                pair.Value.Visible = pair.Key == key;
            foreach (NavItem nav in _navs)
                nav.Active = nav.Text == PageName(key);
            _pageTitle.Text = PageTitle(key);
            _crumb.Text = "控制台 / " + PageName(key);
            PerformLayout();
            if (_dump) return;
            if (key == "chat") LoadChat();
            if (key == "persona") LoadPersona();
            if (key == "config") LoadSettings();
            if (key == "logs") LoadLogs();
        }

        private static string PageName(string key)
        {
            if (key == "chat") return "对话";
            if (key == "persona") return "人格";
            if (key == "config") return "配置";
            if (key == "logs") return "日志";
            return "总览";
        }

        private static string PageTitle(string key)
        {
            if (key == "chat") return "他说过的话，都在这里";
            if (key == "persona") return "她是谁，由你写";
            if (key == "config") return "把连接和边界调好";
            if (key == "logs") return "她都做了什么";
            return "今天也在这里";
        }

        // ---------------- overview ----------------

        private Panel BuildOverview()
        {
            Panel page = NewPage();
            TableLayoutPanel grid = new TableLayoutPanel();
            grid.Dock = DockStyle.Fill;
            grid.ColumnCount = 1;
            grid.RowCount = 3;
            grid.BackColor = Theme.Bg;
            grid.RowStyles.Add(new RowStyle(SizeType.Absolute, 230F));
            grid.RowStyles.Add(new RowStyle(SizeType.Absolute, 184F));
            grid.RowStyles.Add(new RowStyle(SizeType.Percent, 100F));
            page.Controls.Add(grid);

            grid.Controls.Add(BuildHero(), 0, 0);
            grid.Controls.Add(BuildStats(), 0, 1);
            grid.Controls.Add(BuildOverviewBottom(), 0, 2);
            return page;
        }

        private Card BuildHero()
        {
            Card card = new Card();
            card.Dock = DockStyle.Fill;
            card.Margin = new Padding(0, 0, 0, 12);
            card.Padding = new Padding(24, 18, 18, 16);

            TableLayoutPanel layout = new TableLayoutPanel();
            layout.Dock = DockStyle.Fill;
            layout.ColumnCount = 2;
            layout.RowCount = 1;
            layout.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 70F));
            layout.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 30F));
            layout.BackColor = Theme.Card;
            card.Controls.Add(layout);

            FlowLayoutPanel left = new FlowLayoutPanel();
            left.Dock = DockStyle.Fill;
            left.FlowDirection = FlowDirection.TopDown;
            left.WrapContents = false;
            left.BackColor = Theme.Card;
            layout.Controls.Add(left, 0, 0);

            _greeting = new Label();
            _greeting.AutoSize = true;
            _greeting.Text = "晚上好";
            _greeting.ForeColor = Theme.Cyan;
            _greeting.Font = Theme.UiBold(9F);
            _greeting.Margin = new Padding(0, 2, 0, 8);
            left.Controls.Add(_greeting);

            Label headline = new Label();
            headline.AutoSize = true;
            headline.MaximumSize = new Size(620, 0);
            headline.Text = "我是大肥鱼，\n今天也只想围着你转。";
            headline.ForeColor = Theme.Ink;
            headline.Font = Theme.UiBold(18F);
            headline.Margin = new Padding(0, 0, 0, 6);
            left.Controls.Add(headline);

            _moodLine = new Label();
            _moodLine.AutoSize = true;
            _moodLine.MaximumSize = new Size(660, 0);
            _moodLine.Text = "正在读她的心情……";
            _moodLine.ForeColor = Color.FromArgb(175, 203, 221);
            _moodLine.Font = Theme.Ui(9F);
            _moodLine.Margin = new Padding(0, 0, 0, 12);
            left.Controls.Add(_moodLine);

            FlowLayoutPanel meta = new FlowLayoutPanel();
            meta.AutoSize = true;
            meta.FlowDirection = FlowDirection.LeftToRight;
            meta.WrapContents = true;
            meta.BackColor = Theme.Card;
            meta.Margin = new Padding(0);
            left.Controls.Add(meta);

            _heroQq = AddMeta(meta, "QQ —");
            _heroPid = AddMeta(meta, "PID —");
            AddMeta(meta, "本机 · 127.0.0.1");

            PictureBox art = new PictureBox();
            art.Dock = DockStyle.Fill;
            art.SizeMode = PictureBoxSizeMode.Zoom;
            art.BackColor = Theme.Card;
            try { art.Image = Image.FromFile(Path.Combine(_root, "qqbot", "ui", "assets", "dafeiyu.png")); }
            catch { }
            layout.Controls.Add(art, 1, 0);
            return card;
        }

        private Label AddMeta(FlowLayoutPanel parent, string text)
        {
            Label label = new Label();
            label.AutoSize = true;
            label.Text = text;
            label.ForeColor = Theme.Muted;
            label.BackColor = Theme.Card2;
            label.Font = Theme.Ui(8F);
            label.Padding = new Padding(8, 5, 8, 5);
            label.Margin = new Padding(0, 0, 6, 0);
            parent.Controls.Add(label);
            return label;
        }

        private TableLayoutPanel BuildStats()
        {
            TableLayoutPanel row = new TableLayoutPanel();
            row.Dock = DockStyle.Fill;
            row.ColumnCount = 3;
            row.RowCount = 1;
            row.BackColor = Theme.Bg;
            row.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 36F));
            row.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 33F));
            row.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 31F));
            row.Margin = new Padding(0);

            Card mood = new Card();
            mood.Dock = DockStyle.Fill;
            mood.Margin = new Padding(0, 0, 8, 12);
            mood.Padding = new Padding(16, 12, 16, 10);
            TableLayoutPanel moodGrid = new TableLayoutPanel();
            moodGrid.Dock = DockStyle.Fill;
            moodGrid.ColumnCount = 1;
            moodGrid.RowCount = 5;
            moodGrid.BackColor = Theme.Card;
            moodGrid.RowStyles.Add(new RowStyle(SizeType.Absolute, 24F));
            moodGrid.RowStyles.Add(new RowStyle(SizeType.Absolute, 32F));
            moodGrid.RowStyles.Add(new RowStyle(SizeType.Absolute, 32F));
            moodGrid.RowStyles.Add(new RowStyle(SizeType.Absolute, 32F));
            moodGrid.RowStyles.Add(new RowStyle(SizeType.Percent, 100F));
            mood.Controls.Add(moodGrid);

            Label header = new Label();
            header.Text = "她现在的心情";
            header.ForeColor = Theme.Ink;
            header.Font = Theme.UiBold(9F);
            header.Dock = DockStyle.Fill;
            moodGrid.Controls.Add(header, 0, 0);
            _missBar = new MoodBar();
            _missBar.Caption = "想念";
            _missBar.Accent = Theme.Cyan;
            _missBar.Dock = DockStyle.Fill;
            _missBar.BackColor = Theme.Card;
            moodGrid.Controls.Add(_missBar, 0, 1);
            _warmBar = new MoodBar();
            _warmBar.Caption = "亲近";
            _warmBar.Accent = Theme.Rose;
            _warmBar.Dock = DockStyle.Fill;
            _warmBar.BackColor = Theme.Card;
            moodGrid.Controls.Add(_warmBar, 0, 2);
            _tiredBar = new MoodBar();
            _tiredBar.Caption = "疲惫";
            _tiredBar.Accent = Color.FromArgb(197, 140, 255);
            _tiredBar.Dock = DockStyle.Fill;
            _tiredBar.BackColor = Theme.Card;
            moodGrid.Controls.Add(_tiredBar, 0, 3);
            _moodTag = new Label();
            _moodTag.Text = "读取中";
            _moodTag.ForeColor = Theme.Muted;
            _moodTag.Font = Theme.Ui(8F);
            _moodTag.Dock = DockStyle.Fill;
            moodGrid.Controls.Add(_moodTag, 0, 4);
            row.Controls.Add(mood, 0, 0);

            Card today = new Card();
            today.Dock = DockStyle.Fill;
            today.Margin = new Padding(0, 0, 8, 12);
            today.Padding = new Padding(16, 12, 16, 10);
            TableLayoutPanel todayGrid = new TableLayoutPanel();
            todayGrid.Dock = DockStyle.Fill;
            todayGrid.ColumnCount = 1;
            todayGrid.RowCount = 4;
            todayGrid.BackColor = Theme.Card;
            todayGrid.RowStyles.Add(new RowStyle(SizeType.Absolute, 24F));
            todayGrid.RowStyles.Add(new RowStyle(SizeType.Absolute, 62F));
            todayGrid.RowStyles.Add(new RowStyle(SizeType.Absolute, 28F));
            todayGrid.RowStyles.Add(new RowStyle(SizeType.Absolute, 28F));
            today.Controls.Add(todayGrid);
            Label todayHead = new Label();
            todayHead.Text = "今天 · 00:00 起";
            todayHead.Dock = DockStyle.Fill;
            todayHead.ForeColor = Theme.Ink;
            todayHead.Font = Theme.UiBold(9F);
            todayGrid.Controls.Add(todayHead, 0, 0);

            TableLayoutPanel stats = new TableLayoutPanel();
            stats.Dock = DockStyle.Fill;
            stats.ColumnCount = 4;
            stats.RowCount = 1;
            stats.BackColor = Theme.Card;
            for (int i = 0; i < 4; i++) stats.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 25F));
            todayGrid.Controls.Add(stats, 0, 1);
            _statMessages = AddStat(stats, "消息");
            _statUser = AddStat(stats, "他说");
            _statBot = AddStat(stats, "她回");
            _statProactive = AddStat(stats, "主动");

            _quotaProactive = new Label();
            _quotaProactive.Dock = DockStyle.Fill;
            _quotaProactive.Text = "主动额度 0 / 0";
            _quotaProactive.ForeColor = Theme.Muted;
            _quotaProactive.Font = Theme.Ui(8F);
            todayGrid.Controls.Add(_quotaProactive, 0, 2);
            _quotaOther = new Label();
            _quotaOther.Dock = DockStyle.Fill;
            _quotaOther.Text = "空间 — · 抖音 —";
            _quotaOther.ForeColor = Theme.Muted;
            _quotaOther.Font = Theme.Ui(8F);
            todayGrid.Controls.Add(_quotaOther, 0, 3);
            row.Controls.Add(today, 1, 0);

            Card alerts = new Card();
            alerts.Dock = DockStyle.Fill;
            alerts.Margin = new Padding(0, 0, 0, 12);
            alerts.Padding = new Padding(16, 12, 16, 10);
            Panel alertHead = new Panel();
            alertHead.Dock = DockStyle.Top;
            alertHead.Height = 26;
            alertHead.BackColor = Theme.Card;
            alerts.Controls.Add(alertHead);
            Label alertTitle = new Label();
            alertTitle.Text = "需要你看一眼";
            alertTitle.AutoSize = true;
            alertTitle.ForeColor = Theme.Ink;
            alertTitle.Font = Theme.UiBold(9F);
            alertHead.Controls.Add(alertTitle);
            _alertCount = new Label();
            _alertCount.Text = "0";
            _alertCount.Width = 40;
            _alertCount.Height = 18;
            _alertCount.TextAlign = ContentAlignment.MiddleRight;
            _alertCount.ForeColor = Theme.Muted;
            _alertCount.Font = Theme.Ui(8F);
            _alertCount.Dock = DockStyle.Right;
            alertHead.Controls.Add(_alertCount);

            _alertList = new FlowLayoutPanel();
            _alertList.Dock = DockStyle.Fill;
            _alertList.FlowDirection = FlowDirection.TopDown;
            _alertList.WrapContents = false;
            _alertList.AutoScroll = true;
            _alertList.BackColor = Theme.Card;
            alerts.Controls.Add(_alertList);
            _alertList.BringToFront();
            row.Controls.Add(alerts, 2, 0);
            return row;
        }

        private Label AddStat(TableLayoutPanel parent, string caption)
        {
            Panel cell = new Panel();
            cell.Dock = DockStyle.Fill;
            cell.BackColor = Theme.Card2;
            cell.Margin = new Padding(2);
            cell.MinimumSize = Size.Empty;
            cell.Width = 50;
            cell.Height = 56;
            Label number = new Label();
            number.Dock = DockStyle.Top;
            number.Height = 26;
            number.Text = "0";
            number.TextAlign = ContentAlignment.MiddleCenter;
            number.ForeColor = Theme.CyanSoft;
            number.Font = Theme.UiBold(15F);
            cell.Controls.Add(number);
            Label label = new Label();
            label.Dock = DockStyle.Fill;
            label.Text = caption;
            label.TextAlign = ContentAlignment.TopCenter;
            label.ForeColor = Theme.Muted;
            label.Font = Theme.Ui(7.5F);
            cell.Controls.Add(label);
            parent.Controls.Add(cell);
            return number;
        }

        private TableLayoutPanel BuildOverviewBottom()
        {
            TableLayoutPanel row = new TableLayoutPanel();
            row.Dock = DockStyle.Fill;
            row.ColumnCount = 2;
            row.RowCount = 1;
            row.BackColor = Theme.Bg;
            row.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 57F));
            row.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 43F));
            row.Margin = new Padding(0);

            Card recent = new Card();
            recent.Dock = DockStyle.Fill;
            recent.Margin = new Padding(0, 0, 8, 0);
            recent.Padding = new Padding(16, 12, 16, 12);
            Panel head = new Panel();
            head.Dock = DockStyle.Top;
            head.Height = 28;
            head.BackColor = Theme.Card;
            recent.Controls.Add(head);
            Label title = new Label();
            title.Text = "最近对话";
            title.AutoSize = true;
            title.ForeColor = Theme.Ink;
            title.Font = Theme.UiBold(9F);
            head.Controls.Add(title);
            _recentList = new FlowLayoutPanel();
            _recentList.Dock = DockStyle.Fill;
            _recentList.FlowDirection = FlowDirection.TopDown;
            _recentList.WrapContents = false;
            _recentList.AutoScroll = true;
            _recentList.BackColor = Theme.Card;
            recent.Controls.Add(_recentList);
            _recentList.BringToFront();
            row.Controls.Add(recent, 0, 0);

            Card control = new Card();
            control.Dock = DockStyle.Fill;
            control.Margin = new Padding(0);
            control.Padding = new Padding(16, 12, 16, 12);
            Panel controlHead = new Panel();
            controlHead.Dock = DockStyle.Top;
            controlHead.Height = 28;
            controlHead.BackColor = Theme.Card;
            control.Controls.Add(controlHead);
            Label controlTitle = new Label();
            controlTitle.Text = "即时控制";
            controlTitle.AutoSize = true;
            controlTitle.ForeColor = Theme.Ink;
            controlTitle.Font = Theme.UiBold(9F);
            controlHead.Controls.Add(controlTitle);
            _runtimeTag = new Label();
            _runtimeTag.Text = "运行中";
            _runtimeTag.Width = 60;
            _runtimeTag.Height = 18;
            _runtimeTag.TextAlign = ContentAlignment.MiddleRight;
            _runtimeTag.Dock = DockStyle.Right;
            _runtimeTag.ForeColor = Theme.Green;
            _runtimeTag.Font = Theme.Ui(8F);
            controlHead.Controls.Add(_runtimeTag);

            FlowLayoutPanel controls = new FlowLayoutPanel();
            controls.Dock = DockStyle.Fill;
            controls.FlowDirection = FlowDirection.TopDown;
            controls.WrapContents = false;
            controls.BackColor = Theme.Card;
            control.Controls.Add(controls);
            controls.BringToFront();

            _proactiveBox = AddToggle(controls, "主动消息", "到点自己来找你");
            _qzoneBox = AddToggle(controls, "QQ 空间", "发说说 / 配图");
            _douyinBox = AddToggle(controls, "抖音", "点赞 / 分享视频");
            _proactiveBox.CheckedChanged += delegate { if (!_loading) Toggle("proactive", !_proactiveBox.Checked); };
            _qzoneBox.CheckedChanged += delegate { if (!_loading) Toggle("qzone", !_qzoneBox.Checked); };
            _douyinBox.CheckedChanged += delegate { if (!_loading) Toggle("douyin", !_douyinBox.Checked); };

            _emergency = new FlatButton("一键停机", false);
            _emergency.Width = 320;
            _emergency.Height = 34;
            _emergency.Margin = new Padding(0, 8, 0, 0);
            _emergency.Click += delegate { ToggleEmergency(); };
            controls.Controls.Add(_emergency);

            FlowLayoutPanel service = new FlowLayoutPanel();
            service.Width = 340;
            service.Height = 34;
            service.FlowDirection = FlowDirection.LeftToRight;
            service.WrapContents = false;
            service.BackColor = Theme.Card;
            service.Margin = new Padding(0, 4, 0, 0);
            FlatButton stop = new FlatButton("停止机器人", false);
            stop.Width = 160;
            stop.Height = 30;
            stop.Margin = new Padding(0, 0, 4, 0);
            stop.Click += delegate { StopBackend(); };
            service.Controls.Add(stop);
            FlatButton start = new FlatButton("启动机器人", true);
            start.Width = 160;
            start.Height = 30;
            start.Margin = new Padding(0);
            start.Click += delegate { StartBackend(); };
            service.Controls.Add(start);
            controls.Controls.Add(service);

            Label note = new Label();
            note.AutoSize = true;
            note.MaximumSize = new Size(340, 0);
            note.Text = "停机后她不会回复，也不会主动做任何事；再次点击恢复。关闭窗口不会停止机器人。";
            note.ForeColor = Theme.Faint;
            note.Font = Theme.Ui(7.5F);
            note.Margin = new Padding(0, 4, 0, 0);
            controls.Controls.Add(note);
            row.Controls.Add(control, 1, 0);
            return row;
        }

        private CheckBox AddToggle(FlowLayoutPanel parent, string title, string detail)
        {
            CheckBox box = new CheckBox();
            box.Text = title + "    " + detail;
            box.Width = 340;
            box.Height = 28;
            box.ForeColor = Theme.Ink;
            box.BackColor = Theme.Card;
            box.Font = Theme.Ui(8.5F);
            box.FlatStyle = FlatStyle.Flat;
            box.Margin = new Padding(0, 1, 0, 1);
            parent.Controls.Add(box);
            return box;
        }

        // ---------------- chat ----------------

        private Panel BuildChat()
        {
            Panel page = NewPage();
            Panel head = new Panel();
            head.Dock = DockStyle.Top;
            head.Height = 44;
            head.BackColor = Theme.Bg;
            page.Controls.Add(head);
            Label title = new Label();
            title.Text = "对话";
            title.AutoSize = true;
            title.ForeColor = Theme.Ink;
            title.Font = Theme.UiBold(12F);
            head.Controls.Add(title);
            Label hint = new Label();
            hint.Text = "只读 · 最近 60 条；所有消息都在这里";
            hint.Left = 70;
            hint.Top = 4;
            hint.Width = 460;
            hint.ForeColor = Theme.Muted;
            hint.Font = Theme.Ui(8F);
            head.Controls.Add(hint);
            FlatButton reload = new FlatButton("刷新", false);
            reload.Width = 68;
            reload.Height = 30;
            reload.Anchor = AnchorStyles.Top | AnchorStyles.Right;
            reload.Left = page.Width - 76;
            reload.Top = 2;
            reload.Click += delegate { LoadChat(); };
            head.Controls.Add(reload);

            Card card = new Card();
            card.Dock = DockStyle.Fill;
            card.Padding = new Padding(14);
            page.Controls.Add(card);
            card.BringToFront();
            _chatList = new FlowLayoutPanel();
            _chatList.Dock = DockStyle.Fill;
            _chatList.FlowDirection = FlowDirection.TopDown;
            _chatList.WrapContents = false;
            _chatList.AutoScroll = true;
            _chatList.BackColor = Theme.Card;
            _chatList.Resize += delegate
            {
                foreach (Control control in _chatList.Controls)
                    control.Width = Math.Max(300, _chatList.ClientSize.Width - 28);
            };
            card.Controls.Add(_chatList);
            return page;
        }

        // ---------------- persona ----------------

        private Panel BuildPersona()
        {
            Panel page = NewPage();
            Panel head = new Panel();
            head.Dock = DockStyle.Top;
            head.Height = 46;
            head.BackColor = Theme.Bg;
            page.Controls.Add(head);
            Label title = new Label();
            title.Text = "人格";
            title.AutoSize = true;
            title.ForeColor = Theme.Ink;
            title.Font = Theme.UiBold(12F);
            head.Controls.Add(title);
            _personaCount = new Label();
            _personaCount.Left = 68;
            _personaCount.Top = 5;
            _personaCount.Width = 100;
            _personaCount.Text = "— 字";
            _personaCount.ForeColor = Theme.Muted;
            _personaCount.Font = Theme.Ui(8F);
            head.Controls.Add(_personaCount);
            _personaUpdated = new Label();
            _personaUpdated.Left = 160;
            _personaUpdated.Top = 5;
            _personaUpdated.Width = 180;
            _personaUpdated.Text = "";
            _personaUpdated.ForeColor = Theme.Faint;
            _personaUpdated.Font = Theme.Ui(8F);
            head.Controls.Add(_personaUpdated);
            FlatButton save = new FlatButton("发布", true);
            save.Width = 76;
            save.Height = 32;
            save.Anchor = AnchorStyles.Top | AnchorStyles.Right;
            save.Left = page.Width - 84;
            save.Top = 1;
            save.Click += delegate { SavePersona(); };
            head.Controls.Add(save);

            TableLayoutPanel split = new TableLayoutPanel();
            split.Dock = DockStyle.Fill;
            split.ColumnCount = 2;
            split.RowCount = 1;
            split.BackColor = Theme.Bg;
            split.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 62F));
            split.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 38F));
            page.Controls.Add(split);
            split.BringToFront();

            Card editor = new Card();
            editor.Dock = DockStyle.Fill;
            editor.Margin = new Padding(0, 0, 8, 0);
            editor.Padding = new Padding(14);
            split.Controls.Add(editor, 0, 0);
            Panel editorHead = new Panel();
            editorHead.Dock = DockStyle.Top;
            editorHead.Height = 26;
            editorHead.BackColor = Theme.Card;
            editor.Controls.Add(editorHead);
            Label editorTitle = new Label();
            editorTitle.Text = "prompts/system.md";
            editorTitle.AutoSize = true;
            editorTitle.ForeColor = Theme.Muted;
            editorTitle.Font = Theme.Mono(8.5F);
            editorHead.Controls.Add(editorTitle);
            _personaText = new TextBox();
            _personaText.Multiline = true;
            _personaText.Dock = DockStyle.Fill;
            _personaText.ScrollBars = ScrollBars.Both;
            _personaText.AcceptsTab = true;
            _personaText.WordWrap = false;
            _personaText.BackColor = Color.FromArgb(3, 14, 25);
            _personaText.ForeColor = Color.FromArgb(207, 229, 242);
            _personaText.BorderStyle = BorderStyle.FixedSingle;
            _personaText.Font = Theme.Mono(9F);
            editor.Controls.Add(_personaText);
            _personaText.BringToFront();
            Panel foot = new Panel();
            foot.Dock = DockStyle.Bottom;
            foot.Height = 52;
            foot.BackColor = Theme.Card;
            editor.Controls.Add(foot);
            _personaWarnings = new Label();
            _personaWarnings.Left = 0;
            _personaWarnings.Top = 2;
            _personaWarnings.Width = 560;
            _personaWarnings.Height = 20;
            _personaWarnings.Text = "发布前会检查安全边界。";
            _personaWarnings.ForeColor = Theme.Faint;
            _personaWarnings.Font = Theme.Ui(8F);
            foot.Controls.Add(_personaWarnings);
            _personaNote = new TextBox();
            _personaNote.Left = 0;
            _personaNote.Top = 24;
            _personaNote.Width = 430;
            _personaNote.Height = 24;
            _personaNote.BackColor = Color.FromArgb(3, 14, 25);
            _personaNote.ForeColor = Theme.Ink;
            _personaNote.BorderStyle = BorderStyle.FixedSingle;
            _personaNote.Font = Theme.Ui(8F);
            _personaNote.Text = "";
            foot.Controls.Add(_personaNote);
            Label noteHint = new Label();
            noteHint.Text = "备注（可选）";
            noteHint.Left = 438;
            noteHint.Top = 27;
            noteHint.Width = 100;
            noteHint.ForeColor = Theme.Faint;
            noteHint.Font = Theme.Ui(7.5F);
            foot.Controls.Add(noteHint);

            Card versions = new Card();
            versions.Dock = DockStyle.Fill;
            versions.Margin = new Padding(0);
            versions.Padding = new Padding(14);
            split.Controls.Add(versions, 1, 0);
            Label versionsTitle = new Label();
            versionsTitle.Dock = DockStyle.Top;
            versionsTitle.Height = 26;
            versionsTitle.Text = "历史版本";
            versionsTitle.ForeColor = Theme.Muted;
            versionsTitle.Font = Theme.UiBold(9F);
            versions.Controls.Add(versionsTitle);
            _versionList = new ListBox();
            _versionList.Dock = DockStyle.Fill;
            _versionList.BorderStyle = BorderStyle.None;
            _versionList.BackColor = Theme.Card2;
            _versionList.ForeColor = Theme.Ink;
            _versionList.Font = Theme.Ui(8.5F);
            _versionList.IntegralHeight = false;
            versions.Controls.Add(_versionList);
            _versionList.BringToFront();
            return page;
        }

        // ---------------- config ----------------

        private Panel BuildConfig()
        {
            Panel page = NewPage();
            Panel head = new Panel();
            head.Dock = DockStyle.Top;
            head.Height = 46;
            head.BackColor = Theme.Bg;
            page.Controls.Add(head);
            Label title = new Label();
            title.Text = "配置";
            title.AutoSize = true;
            title.ForeColor = Theme.Ink;
            title.Font = Theme.UiBold(12F);
            head.Controls.Add(title);
            Label hint = new Label();
            hint.Text = "保存写入 .env（自动备份 .env.bak）；模型和 QQ 需重启";
            hint.Left = 68;
            hint.Top = 5;
            hint.Width = 520;
            hint.ForeColor = Theme.Muted;
            hint.Font = Theme.Ui(8F);
            head.Controls.Add(hint);
            FlatButton save = new FlatButton("保存配置", true);
            save.Width = 90;
            save.Height = 32;
            save.Anchor = AnchorStyles.Top | AnchorStyles.Right;
            save.Left = page.Width - 98;
            save.Top = 1;
            save.Click += delegate { SaveSettings(); };
            head.Controls.Add(save);

            TableLayoutPanel grid = new TableLayoutPanel();
            grid.Dock = DockStyle.Fill;
            grid.ColumnCount = 2;
            grid.RowCount = 1;
            grid.BackColor = Theme.Bg;
            grid.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 50F));
            grid.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 50F));
            page.Controls.Add(grid);
            grid.BringToFront();

            Card connection = new Card();
            connection.Dock = DockStyle.Fill;
            connection.Margin = new Padding(0, 0, 8, 0);
            connection.Padding = new Padding(16, 12, 16, 12);
            grid.Controls.Add(connection, 0, 0);
            AddSectionTitle(connection, "连接");
            FlowLayoutPanel connForm = NewForm();
            connection.Controls.Add(connForm);
            connForm.BringToFront();
            _qqBox = AddField(connForm, "主人 QQ", 390);
            _baseBox = AddField(connForm, "模型 Base URL", 390);
            _modelBox = AddField(connForm, "Model ID", 390);
            _timeoutBox = AddField(connForm, "超时（秒）", 120);
            _keyBox = AddField(connForm, "API Key（留空保留原 key）", 390);
            _keyBox.UseSystemPasswordChar = true;
            _keyHint = new Label();
            _keyHint.AutoSize = true;
            _keyHint.Text = "key 只写不读";
            _keyHint.ForeColor = Theme.Faint;
            _keyHint.Font = Theme.Ui(7.5F);
            _keyHint.Margin = new Padding(0, 0, 0, 6);
            connForm.Controls.Add(_keyHint);

            Card behavior = new Card();
            behavior.Dock = DockStyle.Fill;
            behavior.Margin = new Padding(8, 0, 0, 0);
            behavior.Padding = new Padding(16, 12, 16, 12);
            grid.Controls.Add(behavior, 1, 0);
            AddSectionTitle(behavior, "她的动作");
            FlowLayoutPanel behaviorForm = NewForm();
            behavior.Controls.Add(behaviorForm);
            behaviorForm.BringToFront();

            _configProactive = AddCheck(behaviorForm, "主动消息");
            _configProactiveMax = AddNumber(behaviorForm, "每天上限", 50);
            _configQzone = AddCheck(behaviorForm, "QQ 空间");
            _configQzoneDry = AddCheck(behaviorForm, "空间试运行（只记录不发布）");
            _configQzoneMax = AddNumber(behaviorForm, "空间每天上限", 20);
            _configDouyin = AddCheck(behaviorForm, "抖音点赞 / 分享");
            _configDouyinDry = AddCheck(behaviorForm, "抖音试运行");
            _configDouyinMax = AddNumber(behaviorForm, "点赞 / 天", 100);
            _configDouyinShare = AddNumber(behaviorForm, "分享 / 天", 50);
            return page;
        }

        private void AddSectionTitle(Card parent, string text)
        {
            Label label = new Label();
            label.Dock = DockStyle.Top;
            label.Height = 28;
            label.Text = text;
            label.ForeColor = Theme.Ink;
            label.Font = Theme.UiBold(9.5F);
            parent.Controls.Add(label);
        }

        private FlowLayoutPanel NewForm()
        {
            FlowLayoutPanel form = new FlowLayoutPanel();
            form.Dock = DockStyle.Fill;
            form.FlowDirection = FlowDirection.TopDown;
            form.WrapContents = false;
            form.AutoScroll = true;
            form.BackColor = Theme.Card;
            form.Padding = new Padding(0, 4, 0, 0);
            return form;
        }

        private TextBox AddField(FlowLayoutPanel parent, string label, int width)
        {
            Panel row = new Panel();
            row.Width = 400;
            row.Height = 50;
            row.BackColor = Theme.Card;
            row.Margin = new Padding(0, 0, 0, 4);
            Label caption = new Label();
            caption.Text = label;
            caption.Left = 0;
            caption.Top = 0;
            caption.Width = 390;
            caption.Height = 16;
            caption.ForeColor = Theme.Muted;
            caption.Font = Theme.Ui(7.5F);
            row.Controls.Add(caption);
            TextBox input = new TextBox();
            input.Left = 0;
            input.Top = 19;
            input.Width = width;
            input.Height = 25;
            input.BackColor = Color.FromArgb(3, 14, 25);
            input.ForeColor = Theme.Ink;
            input.BorderStyle = BorderStyle.FixedSingle;
            input.Font = Theme.Ui(8.5F);
            row.Controls.Add(input);
            parent.Controls.Add(row);
            return input;
        }

        private CheckBox AddCheck(FlowLayoutPanel parent, string text)
        {
            CheckBox box = new CheckBox();
            box.Text = text;
            box.Width = 380;
            box.Height = 26;
            box.ForeColor = Theme.Ink;
            box.BackColor = Theme.Card;
            box.Font = Theme.Ui(8.5F);
            box.FlatStyle = FlatStyle.Flat;
            box.Margin = new Padding(0, 2, 0, 2);
            parent.Controls.Add(box);
            return box;
        }

        private NumericUpDown AddNumber(FlowLayoutPanel parent, string label, int max)
        {
            Panel row = new Panel();
            row.Width = 380;
            row.Height = 28;
            row.BackColor = Theme.Card;
            row.Margin = new Padding(0, 0, 0, 4);
            Label caption = new Label();
            caption.Text = label;
            caption.Left = 0;
            caption.Top = 5;
            caption.Width = 270;
            caption.ForeColor = Theme.Muted;
            caption.Font = Theme.Ui(8F);
            row.Controls.Add(caption);
            NumericUpDown number = new NumericUpDown();
            number.Left = 300;
            number.Top = 3;
            number.Width = 60;
            number.Maximum = max;
            number.Minimum = 0;
            number.BackColor = Color.FromArgb(3, 14, 25);
            number.ForeColor = Theme.Ink;
            number.BorderStyle = BorderStyle.FixedSingle;
            number.Font = Theme.Ui(8F);
            row.Controls.Add(number);
            parent.Controls.Add(row);
            return number;
        }

        // ---------------- logs ----------------

        private Panel BuildLogs()
        {
            Panel page = NewPage();
            Panel head = new Panel();
            head.Dock = DockStyle.Top;
            head.Height = 44;
            head.BackColor = Theme.Bg;
            page.Controls.Add(head);
            Label title = new Label();
            title.Text = "日志与审计";
            title.AutoSize = true;
            title.ForeColor = Theme.Ink;
            title.Font = Theme.UiBold(12F);
            head.Controls.Add(title);
            FlatButton reload = new FlatButton("刷新", false);
            reload.Width = 68;
            reload.Height = 30;
            reload.Anchor = AnchorStyles.Top | AnchorStyles.Right;
            reload.Left = page.Width - 76;
            reload.Top = 2;
            reload.Click += delegate { LoadLogs(); };
            head.Controls.Add(reload);

            TableLayoutPanel grid = new TableLayoutPanel();
            grid.Dock = DockStyle.Fill;
            grid.ColumnCount = 2;
            grid.RowCount = 1;
            grid.BackColor = Theme.Bg;
            grid.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 58F));
            grid.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 42F));
            page.Controls.Add(grid);
            grid.BringToFront();

            Card logCard = new Card();
            logCard.Dock = DockStyle.Fill;
            logCard.Margin = new Padding(0, 0, 8, 0);
            logCard.Padding = new Padding(12);
            grid.Controls.Add(logCard, 0, 0);
            Label logTitle = new Label();
            logTitle.Dock = DockStyle.Top;
            logTitle.Height = 24;
            logTitle.Text = "bot.log · 最近 200 行";
            logTitle.ForeColor = Theme.Muted;
            logTitle.Font = Theme.Ui(8F);
            logCard.Controls.Add(logTitle);
            _logBox = new TextBox();
            _logBox.Multiline = true;
            _logBox.Dock = DockStyle.Fill;
            _logBox.ScrollBars = ScrollBars.Both;
            _logBox.WordWrap = false;
            _logBox.ReadOnly = true;
            _logBox.BackColor = Color.FromArgb(2, 10, 18);
            _logBox.ForeColor = Color.FromArgb(159, 195, 215);
            _logBox.BorderStyle = BorderStyle.None;
            _logBox.Font = Theme.Mono(7.5F);
            logCard.Controls.Add(_logBox);
            _logBox.BringToFront();

            Card auditCard = new Card();
            auditCard.Dock = DockStyle.Fill;
            auditCard.Margin = new Padding(8, 0, 0, 0);
            auditCard.Padding = new Padding(12);
            grid.Controls.Add(auditCard, 1, 0);
            Label auditTitle = new Label();
            auditTitle.Dock = DockStyle.Top;
            auditTitle.Height = 24;
            auditTitle.Text = "审计 · 最近 40 条";
            auditTitle.ForeColor = Theme.Muted;
            auditTitle.Font = Theme.Ui(8F);
            auditCard.Controls.Add(auditTitle);
            _auditList = new ListView();
            _auditList.Dock = DockStyle.Fill;
            _auditList.View = View.Details;
            _auditList.FullRowSelect = true;
            _auditList.GridLines = false;
            _auditList.BorderStyle = BorderStyle.None;
            _auditList.BackColor = Theme.Card2;
            _auditList.ForeColor = Theme.Ink;
            _auditList.Font = Theme.Ui(8F);
            _auditList.Columns.Add("时间", 100);
            _auditList.Columns.Add("动作", 130);
            _auditList.Columns.Add("结果", 60);
            auditCard.Controls.Add(_auditList);
            _auditList.BringToFront();
            return page;
        }

        // ---------------- startup / polling ----------------

        private void Startup()
        {
            if (!File.Exists(Path.Combine(_root, "tools", "python", "python.exe")))
            {
                SetStatus("找不到 tools\\python\\python.exe", Theme.Rose);
                return;
            }
            if (PortOpen(_port))
            {
                _started = true;
                _startedAt = DateTime.Now;
                TryConnect();
            }
            else
            {
                StartBackend();
            }
            _timer.Start();
        }

        private void StartBackend()
        {
            if (_ready)
            {
                SetStatus("● 运行中", Theme.Green);
                return;
            }
            if (PortOpen(_port))
            {
                _started = true;
                _startedAt = DateTime.Now;
                TryConnect();
                return;
            }
            string script = Path.Combine(_root, "scripts", "supervise.ps1");
            if (!File.Exists(script))
            {
                SetStatus("找不到 scripts\\supervise.ps1", Theme.Rose);
                return;
            }
            _started = true;
            _startedAt = DateTime.Now;
            _ready = false;
            SetStatus("正在启动协议端和机器人……", Theme.Amber);
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
                SetStatus("启动失败：" + ex.Message, Theme.Rose);
            }
        }

        private void StopBackend()
        {
            string script = Path.Combine(_root, "scripts", "stop-bot.ps1");
            if (!File.Exists(script))
            {
                SetStatus("找不到 scripts\\stop-bot.ps1", Theme.Rose);
                return;
            }
            SetStatus("正在停止机器人……", Theme.Amber);
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
                SetStatus("停止失败：" + ex.Message, Theme.Rose);
                return;
            }
            _ready = false;
            _started = false;
            SetStatus("机器人已停止", Theme.Muted);
            _runtimeTag.Text = "已停止";
            _runtimeTag.ForeColor = Theme.Muted;
        }

        private void Tick()
        {
            if (!_ready)
            {
                if (PortOpen(_port))
                {
                    TryConnect();
                }
                else if (_started && (DateTime.Now - _startedAt).TotalSeconds > 90)
                {
                    SetStatus("启动较慢，查看 logs\\bot.log", Theme.Amber);
                }
                return;
            }
            RefreshStatus();
        }

        private void TryConnect()
        {
            if (_busy) return;
            _busy = true;
            ThreadPool.QueueUserWorkItem(delegate
            {
                try
                {
                    Dictionary<string, object> status = _api.Get("/api/status");
                    BeginInvoke(new Action(delegate
                    {
                        _busy = false;
                        _ready = true;
                        RenderStatus(status);
                        SetStatus("● 运行中", Theme.Green);
                        if (_current == "chat") LoadChat();
                        if (_current == "persona") LoadPersona();
                        if (_current == "config") LoadSettings();
                        if (_current == "logs") LoadLogs();
                    }));
                }
                catch (Exception ex)
                {
                    BeginInvoke(new Action(delegate
                    {
                        _busy = false;
                        if (PortOpen(_port)) SetStatus("● 连接失败：" + ex.Message, Theme.Rose);
                        else SetStatus("● 正在启动……", Theme.Amber);
                    }));
                }
            });
        }

        private void RefreshStatus()
        {
            if (_busy) return;
            _busy = true;
            ThreadPool.QueueUserWorkItem(delegate
            {
                try
                {
                    Dictionary<string, object> status = _api.Get("/api/status");
                    BeginInvoke(new Action(delegate
                    {
                        _busy = false;
                        RenderStatus(status);
                    }));
                }
                catch (Exception)
                {
                    BeginInvoke(new Action(delegate
                    {
                        _busy = false;
                        SetStatus("● 连接中断", Theme.Rose);
                        _ready = false;
                    }));
                }
            });
        }

        private void RefreshAll()
        {
            RefreshStatus();
            if (_current == "chat") LoadChat();
            if (_current == "persona") LoadPersona();
            if (_current == "config") LoadSettings();
            if (_current == "logs") LoadLogs();
        }

        private void CaptureSelf()
        {
            using (Bitmap bitmap = new Bitmap(ClientSize.Width, ClientSize.Height))
            {
                DrawToBitmap(bitmap, new Rectangle(0, 0, bitmap.Width, bitmap.Height));
                bitmap.Save(Path.Combine(_root, "docs", "native-ui.png"), System.Drawing.Imaging.ImageFormat.Png);
            }
        }

        private void DumpLayout(Control parent, string suffix)
        {
            StringBuilder builder = new StringBuilder();
            builder.AppendLine("client=" + ClientSize.Width + "x" + ClientSize.Height);
            Walk(parent, builder, 0);
            File.WriteAllText(Path.Combine(_root, "native-layout-" + suffix + ".txt"), builder.ToString(), Encoding.UTF8);
        }

        private static void Walk(Control control, StringBuilder builder, int depth)
        {
            string text = (control.Text ?? "").Replace("\r", " ").Replace("\n", " ");
            if (text.Length > 28) text = text.Substring(0, 28) + "…";
            builder.Append(new string(' ', depth * 2));
            builder.Append(control.GetType().Name);
            builder.Append(" bounds=").Append(control.Bounds.X).Append(',').Append(control.Bounds.Y)
                .Append(' ').Append(control.Bounds.Width).Append('x').Append(control.Bounds.Height);
            builder.Append(" dock=").Append(control.Dock);
            if (!control.Visible) builder.Append(" hidden");
            if (text.Length > 0) builder.Append(" text=").Append(text);
            builder.AppendLine();
            foreach (Control child in control.Controls) Walk(child, builder, depth + 1);
        }
        private void SetStatus(string text, Color color)
        {
            _statusPill.Text = text;
            _statusPill.ForeColor = color;
            _sideState.Text = text;
            _sideState.ForeColor = color;
        }

        // ---------------- render status ----------------

        private void RenderStatus(Dictionary<string, object> status)
        {
            _loading = true;
            Dictionary<string, object> mood = J.Obj(status["mood"]);
            Dictionary<string, object> values = J.Obj(mood["values"]);
            _missBar.Value = J.Num(values["miss"], 0);
            _warmBar.Value = J.Num(values["warmth"], 0);
            _tiredBar.Value = J.Num(values["tired"], 0);
            _missBar.Invalidate();
            _warmBar.Invalidate();
            _tiredBar.Invalidate();
            _moodLine.Text = J.Str(mood["line"], "心情数据还没接上。");
            _heroQq.Text = "QQ " + J.Str(status["master_qq"], "—");
            _heroPid.Text = "PID " + J.Str(status["pid"], "—");
            _sideModel.Text = "模型 " + J.Str(status["model"], "—");
            _greeting.Text = Greeting();
            _moodTag.Text = _tiredBar.Value >= 0.7 ? "有点累" : _missBar.Value >= 0.75 ? "想他" : _warmBar.Value >= 0.6 ? "暖" : "平稳";

            Dictionary<string, object> today = J.Obj(status["today"]);
            _statMessages.Text = J.Int(today["messages"], 0).ToString();
            _statUser.Text = J.Int(today["user"], 0).ToString();
            _statBot.Text = J.Int(today["bot"], 0).ToString();
            _statProactive.Text = J.Int(today["proactive"], 0).ToString();
            _quotaProactive.Text = "主动额度 " + J.Int(today["proactive"], 0) + " / " + J.Int(today["proactive_max"], 0);
            Dictionary<string, object> guard = J.Obj(status["guard"]);
            Dictionary<string, object> qzone = J.Obj(guard["qzone"]);
            Dictionary<string, object> douyin = J.Obj(guard["douyin"]);
            _quotaOther.Text = "空间 " + J.Int(qzone["used_today"], 0) + "/" + J.Int(qzone["daily_quota"], 0) +
                              " · 抖音 " + J.Int(douyin["used_today"], 0) + "/" + J.Int(douyin["daily_quota"], 0);

            List<object> alerts = J.Arr(status["alerts"]);
            _alertCount.Text = alerts.Count.ToString();
            _alertList.SuspendLayout();
            _alertList.Controls.Clear();
            if (alerts.Count == 0)
            {
                _alertList.Controls.Add(MakeSmall("暂时没有异常。", Theme.Faint));
            }
            else
            {
                foreach (object item in alerts)
                {
                    Dictionary<string, object> alert = J.Obj(item);
                    string level = J.Str(alert["level"], "warn");
                    _alertList.Controls.Add(MakeAlert(J.Str(alert["title"], ""), J.Str(alert["detail"], ""), level == "bad"));
                }
            }
            _alertList.ResumeLayout();

            List<object> messages = J.Arr(status["messages"]);
            _recentList.SuspendLayout();
            _recentList.Controls.Clear();
            int start = Math.Max(0, messages.Count - 6);
            for (int i = start; i < messages.Count; i++)
            {
                Dictionary<string, object> message = J.Obj(messages[i]);
                _recentList.Controls.Add(MakeRecent(message));
            }
            if (messages.Count == 0) _recentList.Controls.Add(MakeSmall("还没有消息。", Theme.Faint));
            _recentList.ResumeLayout();

            Dictionary<string, object> runtime = J.Obj(status["runtime"]);
            Dictionary<string, object> paused = J.Obj(runtime["paused"]);
            bool emergency = J.Bool(runtime["emergency"], false);
            _emergencyOn = emergency;
            _proactiveBox.Checked = !J.Bool(paused["proactive"], false);
            _qzoneBox.Checked = !J.Bool(paused["qzone"], false);
            _douyinBox.Checked = !J.Bool(paused["douyin"], false);
            _proactiveBox.Enabled = !emergency;
            _qzoneBox.Enabled = !emergency;
            _douyinBox.Enabled = !emergency;
            _emergency.Text = emergency ? "恢复运行" : "一键停机";
            _runtimeTag.Text = emergency ? "已停机" : "运行中";
            _runtimeTag.ForeColor = emergency ? Theme.Rose : Theme.Green;
            _loading = false;
        }

        private Label MakeSmall(string text, Color color)
        {
            Label label = new Label();
            label.AutoSize = true;
            label.MaximumSize = new Size(360, 0);
            label.Text = text;
            label.ForeColor = color;
            label.Font = Theme.Ui(8F);
            label.Margin = new Padding(0, 4, 0, 0);
            return label;
        }

        private Panel MakeAlert(string title, string detail, bool bad)
        {
            Panel row = new Panel();
            row.Width = Math.Max(220, _alertList.ClientSize.Width - 24);
            row.Height = 34;
            row.BackColor = bad ? Color.FromArgb(34, 20, 36) : Theme.Card2;
            row.Margin = new Padding(0, 0, 0, 5);
            Panel bar = new Panel();
            bar.Dock = DockStyle.Left;
            bar.Width = 3;
            bar.BackColor = bad ? Theme.Rose : Theme.Amber;
            row.Controls.Add(bar);
            Label label = new Label();
            label.Left = 9;
            label.Top = 2;
            label.Width = row.Width - 20;
            label.Height = 30;
            label.Text = title + "  " + detail;
            label.ForeColor = bad ? Color.FromArgb(255, 195, 208) : Theme.Muted;
            label.Font = Theme.Ui(7.5F);
            row.Controls.Add(label);
            return row;
        }

        private Panel MakeRecent(Dictionary<string, object> message)
        {
            bool bot = J.Str(message["role"], "") == "assistant";
            Panel row = new Panel();
            row.Width = Math.Max(300, _recentList.ClientSize.Width - 24);
            row.Height = 42;
            row.BackColor = Theme.Card2;
            row.Margin = new Padding(0, 0, 0, 5);
            Panel bar = new Panel();
            bar.Dock = DockStyle.Left;
            bar.Width = 3;
            bar.BackColor = bot ? Theme.Rose : Theme.Cyan;
            row.Controls.Add(bar);
            Label who = new Label();
            who.Left = 9;
            who.Top = 2;
            who.Width = 100;
            who.Text = (bot ? "她" : "你") + " · " + J.Str(message["origin"], "reply");
            who.ForeColor = Theme.Faint;
            who.Font = Theme.Ui(7F);
            row.Controls.Add(who);
            Label text = new Label();
            text.Left = 9;
            text.Top = 16;
            text.Width = row.Width - 20;
            text.Height = 22;
            text.Text = J.Str(message["content"], "");
            text.ForeColor = Theme.Ink;
            text.Font = Theme.Ui(8F);
            row.Controls.Add(text);
            return row;
        }

        private static string Greeting()
        {
            int hour = DateTime.Now.Hour;
            if (hour < 5) return "凌晨好";
            if (hour < 11) return "早上好";
            if (hour < 14) return "中午好";
            if (hour < 18) return "下午好";
            return "晚上好";
        }

        // ---------------- actions ----------------

        private void Toggle(string module, bool paused)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["module"] = module;
            body["paused"] = paused;
            RunPut("/api/runtime", body, ModuleName(module) + (paused ? " 已暂停" : " 已恢复"));
        }

        private void ToggleEmergency()
        {
            bool on = !_emergencyOn;
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["emergency"] = on;
            RunPut("/api/runtime", body, on ? "已停机" : "已恢复运行");
        }

        private void RunPut(string path, object body, string message)
        {
            _busy = true;
            ThreadPool.QueueUserWorkItem(delegate
            {
                try
                {
                    _api.Put(path, body);
                    BeginInvoke(new Action(delegate
                    {
                        _busy = false;
                        SetStatus("● " + message, Theme.Green);
                        RefreshStatus();
                    }));
                }
                catch (Exception ex)
                {
                    BeginInvoke(new Action(delegate
                    {
                        _busy = false;
                        SetStatus("● " + ex.Message, Theme.Rose);
                    }));
                }
            });
        }

        private static string ModuleName(string module)
        {
            if (module == "proactive") return "主动消息";
            if (module == "qzone") return "QQ 空间";
            if (module == "douyin") return "抖音";
            return module;
        }

        // ---------------- chat / persona / config / logs ----------------

        private void LoadChat()
        {
            RunGet("/api/messages?limit=60", delegate(Dictionary<string, object> data)
            {
                List<object> messages = J.Arr(data["messages"]);
                _chatList.SuspendLayout();
                _chatList.Controls.Clear();
                foreach (object item in messages)
                {
                    _chatList.Controls.Add(MakeChatBubble(J.Obj(item)));
                }
                if (messages.Count == 0) _chatList.Controls.Add(MakeSmall("还没有消息。", Theme.Faint));
                _chatList.ResumeLayout();
            });
        }

        private Panel MakeChatBubble(Dictionary<string, object> message)
        {
            bool bot = J.Str(message["role"], "") == "assistant";
            string content = J.Str(message["content"], "");
            int width = Math.Max(320, _chatList.ClientSize.Width - 28);
            Font font = Theme.Ui(9F);
            Size size = TextRenderer.MeasureText(content, font, new Size(width - 34, 2000),
                TextFormatFlags.WordBreak | TextFormatFlags.TextBoxControl);
            Panel bubble = new Panel();
            bubble.Width = width;
            bubble.Height = Math.Max(42, size.Height + 32);
            bubble.BackColor = bot ? Color.FromArgb(19, 43, 61) : Color.FromArgb(13, 34, 52);
            bubble.Margin = new Padding(bot ? 20 : 0, 0, bot ? 0 : 20, 6);
            Panel bar = new Panel();
            bar.Dock = DockStyle.Left;
            bar.Width = 3;
            bar.BackColor = bot ? Theme.Rose : Theme.Cyan;
            bubble.Controls.Add(bar);
            Label meta = new Label();
            meta.Left = 10;
            meta.Top = 3;
            meta.Width = width - 40;
            meta.Height = 16;
            meta.Text = (bot ? "大肥鱼" : "你") + " · " + J.Str(message["origin"], "reply") + " · " + Stamp(J.Num(message["created_at"], 0));
            meta.ForeColor = Theme.Faint;
            meta.Font = Theme.Ui(7F);
            bubble.Controls.Add(meta);
            Label text = new Label();
            text.Left = 10;
            text.Top = 19;
            text.Width = width - 34;
            text.Height = size.Height + 6;
            text.Text = content;
            text.ForeColor = Theme.Ink;
            text.Font = font;
            bubble.Controls.Add(text);
            return bubble;
        }

        private static string Stamp(double timestamp)
        {
            if (timestamp <= 0) return "—";
            DateTime time = new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc).AddSeconds(timestamp).ToLocalTime();
            return time.ToString("MM-dd HH:mm");
        }

        private void LoadPersona()
        {
            RunGet("/api/persona", delegate(Dictionary<string, object> data)
            {
                _personaText.Text = J.Str(data["text"], "");
                _personaCount.Text = J.Int(data["chars"], 0) + " 字";
                double updated = J.Num(data["updated_at"], 0);
                _personaUpdated.Text = updated > 0 ? "上次发布 " + Stamp(updated) : "";
                List<object> warnings = J.Arr(data["warnings"]);
                List<string> warningText = new List<string>();
                foreach (object item in warnings) warningText.Add(J.Str(item, ""));
                _personaWarnings.Text = warningText.Count == 0 ? "边界词齐全。" : "提醒：" + string.Join("；", warningText.ToArray());
                _versionList.Items.Clear();
                foreach (object item in J.Arr(data["versions"]))
                {
                    Dictionary<string, object> version = J.Obj(item);
                    _versionList.Items.Add(Stamp(J.Num(version["at"], 0)) + "  ·  " +
                        J.Int(version["chars"], 0) + " 字  ·  " + J.Str(version["note"], "无备注"));
                }
                if (_versionList.Items.Count == 0) _versionList.Items.Add("还没有版本");
            });
        }

        private void SavePersona()
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["text"] = _personaText.Text;
            body["note"] = _personaNote.Text;
            RunPut("/api/persona", body, "人格已发布，下一条生效");
            _personaNote.Text = "";
        }

        private void LoadSettings()
        {
            RunGet("/api/settings", delegate(Dictionary<string, object> data)
            {
                Dictionary<string, object> model = J.Obj(data["model"]);
                Dictionary<string, object> behavior = J.Obj(data["behavior"]);
                _qqBox.Text = J.Str(data["master_qq"], "");
                _baseBox.Text = J.Str(model["base_url"], "");
                _modelBox.Text = J.Str(model["id"], "");
                _timeoutBox.Text = J.Int(model["timeout"], 60).ToString();
                _keyBox.Text = "";
                _keyHint.Text = J.Bool(model["api_key_set"], false)
                    ? "已设置（" + J.Str(model["api_key_hint"], "") + "），留空保留"
                    : "还没设置 API Key";
                _configProactive.Checked = J.Bool(behavior["proactive_enabled"], false);
                _configProactiveMax.Value = J.Int(behavior["proactive_max_per_day"], 5);
                _configQzone.Checked = J.Bool(behavior["qzone_enabled"], false);
                _configQzoneDry.Checked = J.Bool(behavior["qzone_dry_run"], true);
                _configQzoneMax.Value = J.Int(behavior["qzone_daily_max"], 1);
                _configDouyin.Checked = J.Bool(behavior["douyin_enabled"], false);
                _configDouyinDry.Checked = J.Bool(behavior["douyin_dry_run"], true);
                _configDouyinMax.Value = J.Int(behavior["douyin_daily_max"], 5);
                _configDouyinShare.Value = J.Int(behavior["douyin_share_daily_max"], 2);
            });
        }

        private void SaveSettings()
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["master_qq"] = _qqBox.Text.Trim();
            body["model_base_url"] = _baseBox.Text.Trim();
            body["model_id"] = _modelBox.Text.Trim();
            body["request_timeout"] = ParseInt(_timeoutBox.Text, 60);
            if (_keyBox.Text.Trim().Length > 0) body["model_api_key"] = _keyBox.Text.Trim();
            body["proactive_enabled"] = _configProactive.Checked;
            body["proactive_max_per_day"] = (int)_configProactiveMax.Value;
            body["qzone_enabled"] = _configQzone.Checked;
            body["qzone_dry_run"] = _configQzoneDry.Checked;
            body["qzone_daily_max"] = (int)_configQzoneMax.Value;
            body["douyin_enabled"] = _configDouyin.Checked;
            body["douyin_dry_run"] = _configDouyinDry.Checked;
            body["douyin_daily_max"] = (int)_configDouyinMax.Value;
            body["douyin_share_daily_max"] = (int)_configDouyinShare.Value;
            RunPut("/api/settings", body, "配置已写入 .env，重启后生效");
            _keyBox.Text = "";
        }

        private static int ParseInt(string text, int fallback)
        {
            int value;
            return int.TryParse(text.Trim(), out value) ? value : fallback;
        }

        private void LoadLogs()
        {
            RunGet("/api/logs?limit=200", delegate(Dictionary<string, object> data)
            {
                List<object> lines = J.Arr(data["lines"]);
                StringBuilder builder = new StringBuilder();
                foreach (object line in lines) builder.Append(J.Str(line, ""));
                _logBox.Text = builder.ToString();
                _logBox.SelectionStart = _logBox.TextLength;
                _logBox.ScrollToCaret();
            });
            RunGet("/api/audit", delegate(Dictionary<string, object> data)
            {
                _auditList.BeginUpdate();
                _auditList.Items.Clear();
                foreach (object item in J.Arr(data["items"]))
                {
                    Dictionary<string, object> row = J.Obj(item);
                    ListViewItem view = new ListViewItem(Stamp(J.Num(row["at"], 0)));
                    view.SubItems.Add(J.Str(row["module"], "") + " · " + J.Str(row["action"], ""));
                    view.SubItems.Add(J.Str(row["result"], ""));
                    _auditList.Items.Add(view);
                }
                _auditList.EndUpdate();
            });
        }

        private void RunGet(string path, Action<Dictionary<string, object>> done)
        {
            ThreadPool.QueueUserWorkItem(delegate
            {
                try
                {
                    Dictionary<string, object> data = _api.Get(path);
                    BeginInvoke(new Action(delegate { done(data); }));
                }
                catch (Exception ex)
                {
                    BeginInvoke(new Action(delegate { SetStatus("● " + ex.Message, Theme.Rose); }));
                }
            });
        }

        // ---------------- helpers ----------------

        private bool PortOpen(int port)
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
    }
}

