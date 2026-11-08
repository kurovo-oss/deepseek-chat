using System;
using System.Collections;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Globalization;
using System.Windows.Forms;

namespace DafeiyuNative
{
    internal static class Theme
    {
        public static readonly Color Bg = Color.FromArgb(5, 17, 31);
        public static readonly Color Side = Color.FromArgb(7, 24, 40);
        public static readonly Color Card = Color.FromArgb(10, 32, 52);
        public static readonly Color Card2 = Color.FromArgb(8, 26, 43);
        public static readonly Color Line = Color.FromArgb(31, 68, 94);
        public static readonly Color Ink = Color.FromArgb(234, 246, 255);
        public static readonly Color Muted = Color.FromArgb(143, 179, 204);
        public static readonly Color Faint = Color.FromArgb(95, 130, 154);
        public static readonly Color Cyan = Color.FromArgb(90, 215, 255);
        public static readonly Color CyanSoft = Color.FromArgb(185, 240, 255);
        public static readonly Color Green = Color.FromArgb(115, 230, 177);
        public static readonly Color Amber = Color.FromArgb(255, 196, 107);
        public static readonly Color Rose = Color.FromArgb(255, 126, 157);

        public static Font Ui(float size)
        {
            return new Font("Microsoft YaHei UI", size, FontStyle.Regular, GraphicsUnit.Point);
        }

        public static Font UiBold(float size)
        {
            return new Font("Microsoft YaHei UI", size, FontStyle.Bold, GraphicsUnit.Point);
        }

        public static Font Mono(float size)
        {
            return new Font("Cascadia Mono", size, FontStyle.Regular, GraphicsUnit.Point);
        }
    }

    internal class Card : Panel
    {
        public Card()
        {
            SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint |
                     ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw, true);
            BackColor = Theme.Card;
            Padding = new Padding(18);
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            base.OnPaint(e);
            e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
            using (Pen pen = new Pen(Theme.Line))
            {
                e.Graphics.DrawRectangle(pen, 0, 0, Width - 1, Height - 1);
            }
            using (Pen glow = new Pen(Color.FromArgb(70, 90, 215, 255)))
            {
                e.Graphics.DrawLine(glow, 1, 1, Width - 2, 1);
            }
        }
    }

    internal class FlatButton : Button
    {
        private readonly Color _back;
        private readonly Color _hover;
        private readonly Color _fore;

        public FlatButton(string text, bool primary)
        {
            Text = text;
            FlatStyle = FlatStyle.Flat;
            FlatAppearance.BorderSize = 1;
            UseVisualStyleBackColor = false;
            Cursor = Cursors.Hand;
            Font = Theme.UiBold(9.5F);
            Height = 36;
            if (primary)
            {
                _back = Theme.Cyan;
                _hover = Color.FromArgb(150, 235, 255);
                _fore = Color.FromArgb(4, 32, 44);
                FlatAppearance.BorderColor = Color.FromArgb(185, 240, 255);
            }
            else
            {
                _back = Theme.Card2;
                _hover = Color.FromArgb(20, 56, 82);
                _fore = Theme.Ink;
                FlatAppearance.BorderColor = Theme.Line;
            }
            BackColor = _back;
            ForeColor = _fore;
            MouseEnter += delegate { if (Enabled) BackColor = _hover; };
            MouseLeave += delegate { BackColor = _back; };
            EnabledChanged += delegate { BackColor = Enabled ? _back : Color.FromArgb(24, 43, 58); };
        }
    }

    internal class NavItem : Button
    {
        private bool _active;

        public NavItem(string text)
        {
            Text = text;
            Height = 42;
            FlatStyle = FlatStyle.Flat;
            FlatAppearance.BorderSize = 0;
            UseVisualStyleBackColor = false;
            TextAlign = ContentAlignment.MiddleLeft;
            Padding = new Padding(18, 0, 0, 0);
            Font = Theme.Ui(10F);
            Cursor = Cursors.Hand;
            SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint |
                     ControlStyles.OptimizedDoubleBuffer, true);
            Apply();
            MouseEnter += delegate { if (!_active) BackColor = Color.FromArgb(14, 45, 67); };
            MouseLeave += delegate { Apply(); };
        }

        public bool Active
        {
            get { return _active; }
            set
            {
                _active = value;
                Apply();
            }
        }

        private void Apply()
        {
            BackColor = _active ? Color.FromArgb(17, 48, 70) : Color.Transparent;
            ForeColor = _active ? Theme.Ink : Color.FromArgb(169, 199, 219);
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            base.OnPaint(e);
            if (_active)
            {
                using (SolidBrush brush = new SolidBrush(Theme.Cyan))
                {
                    e.Graphics.FillRectangle(brush, 0, 5, 3, Height - 10);
                }
            }
        }
    }

    internal class MoodBar : Control
    {
        public string Caption = "";
        public double Value;
        public Color Accent = Theme.Cyan;

        public MoodBar()
        {
            Height = 32;
            SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint |
                     ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw, true);
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
            e.Graphics.Clear(BackColor);
            TextRenderer.DrawText(e.Graphics, Caption, Theme.Ui(9F), new Rectangle(0, 4, 44, 24),
                Theme.Muted, TextFormatFlags.VerticalCenter | TextFormatFlags.Left);

            int left = 48;
            int right = Width - 38;
            if (right < left + 10) return;
            Rectangle track = new Rectangle(left, 12, right - left, 7);
            using (GraphicsPath path = RoundRect(track, 4))
            using (SolidBrush back = new SolidBrush(Color.FromArgb(21, 54, 79)))
            {
                e.Graphics.FillPath(back, path);
            }
            int width = (int)Math.Round((right - left) * Math.Max(0, Math.Min(1, Value)));
            if (width > 2)
            {
                Rectangle fill = new Rectangle(left, 12, width, 7);
                using (GraphicsPath path = RoundRect(fill, 4))
                using (LinearGradientBrush brush = new LinearGradientBrush(fill, Accent, Theme.CyanSoft, 0F))
                {
                    e.Graphics.FillPath(brush, path);
                }
            }
            TextRenderer.DrawText(e.Graphics, Value.ToString("0.00"), Theme.Ui(8.5F),
                new Rectangle(Width - 34, 4, 34, 24), Theme.Ink,
                TextFormatFlags.VerticalCenter | TextFormatFlags.Right);
        }

        private static GraphicsPath RoundRect(Rectangle rect, int radius)
        {
            GraphicsPath path = new GraphicsPath();
            int d = radius * 2;
            if (rect.Width <= d || rect.Height <= d)
            {
                path.AddRectangle(rect);
                return path;
            }
            path.AddArc(rect.Left, rect.Top, d, d, 180, 90);
            path.AddArc(rect.Right - d, rect.Top, d, d, 270, 90);
            path.AddArc(rect.Right - d, rect.Bottom - d, d, d, 0, 90);
            path.AddArc(rect.Left, rect.Bottom - d, d, d, 90, 90);
            path.CloseFigure();
            return path;
        }
    }

    internal static class J
    {
        public static Dictionary<string, object> Obj(object value)
        {
            Dictionary<string, object> result = value as Dictionary<string, object>;
            return result ?? new Dictionary<string, object>();
        }

        public static List<object> Arr(object value)
        {
            List<object> result = new List<object>();
            if (value == null) return result;
            object[] array = value as object[];
            if (array != null)
            {
                result.AddRange(array);
                return result;
            }
            IEnumerable enumerable = value as IEnumerable;
            if (enumerable != null && !(value is string))
            {
                foreach (object item in enumerable) result.Add(item);
            }
            return result;
        }

        public static string Str(object value, string fallback)
        {
            if (value == null) return fallback;
            string text = value as string;
            return text == null ? fallback : text;
        }

        public static double Num(object value, double fallback)
        {
            if (value == null) return fallback;
            try { return Convert.ToDouble(value, CultureInfo.InvariantCulture); }
            catch { return fallback; }
        }

        public static int Int(object value, int fallback)
        {
            return (int)Math.Round(Num(value, fallback));
        }

        public static bool Bool(object value, bool fallback)
        {
            if (value == null) return fallback;
            if (value is bool) return (bool)value;
            try { return Convert.ToBoolean(value, CultureInfo.InvariantCulture); }
            catch { return fallback; }
        }
    }
}
