#!/usr/bin/env python3
# شاشة معلومات ملوّنة للـ Raspberry Pi على التلفزيون (HDMI) — ترسم مباشرة على /dev/fb0
# بدون سطح مكتب. إن لم يتوفر الرسم (لا framebuffer أو لا Pillow) تعود للشاشة النصية.
# للقراءة فقط: لا تنفّذ أي أوامر تغيّر شيئاً في النظام.
import math, os, sys, time
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pi_screen as ps  # دوال قراءة المعلومات المشتركة مع الشاشة النصية

REFRESH = 2
HIST = 150  # 5 دقائق

BG = (9, 14, 26)
CARD = (19, 28, 46)
LINE = (38, 52, 78)
TXT = (230, 236, 245)
SUB = (130, 146, 172)
CYAN = (0, 190, 255)
BLUE = (46, 125, 246)
GREEN = (46, 204, 113)
YELLOW = (255, 196, 0)
ORANGE = (255, 140, 40)
RED = (255, 77, 77)
PURPLE = (170, 110, 255)

FONT_DIRS = ["/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu"]


def font_path(bold):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    for d in FONT_DIRS:
        p = os.path.join(d, name)
        if os.path.exists(p):
            return p
    return None


class Fb:
    def __init__(self, dev="/dev/fb0"):
        base = "/sys/class/graphics/" + os.path.basename(dev)
        self.w, self.h = [int(x) for x in ps.read(base + "/virtual_size").split(",")]
        self.bpp = int(ps.read(base + "/bits_per_pixel"))
        stride = ps.read(base + "/stride")
        self.stride = int(stride) if stride.isdigit() else self.w * self.bpp // 8
        if self.bpp not in (16, 32) or self.w < 320:
            raise RuntimeError(f"unsupported framebuffer {self.w}x{self.h}@{self.bpp}")
        self.f = open(dev, "r+b", buffering=0)

    @staticmethod
    def rgb565(img):
        # بعض نسخ Pillow لا تملك محوّل "BGR;16" — نحسبه بأنفسنا (little-endian)
        from PIL import Image, ImageChops
        r, g, b = img.split()
        hi = ImageChops.add(r.point(lambda v: v & 0xF8), g.point(lambda v: v >> 5))
        lo = ImageChops.add(g.point(lambda v: ((v >> 2) & 7) << 5), b.point(lambda v: v >> 3))
        return Image.merge("LA", (lo, hi)).tobytes()

    def show(self, img):
        if img.size != (self.w, self.h):
            img = img.resize((self.w, self.h))
        if self.bpp == 32:
            data = img.convert("RGBA").tobytes("raw", "BGRA")
        else:
            data = self.rgb565(img.convert("RGB"))
        row = self.w * self.bpp // 8
        self.f.seek(0)
        if row == self.stride:
            self.f.write(data)
        else:
            pad = b"\0" * (self.stride - row)
            self.f.write(b"".join(data[i:i + row] + pad for i in range(0, len(data), row)))


class Painter:
    W, H = 1920, 1080

    def __init__(self):
        from PIL import Image, ImageDraw, ImageFont, features
        self.Image, self.ImageDraw, self.ImageFont = Image, ImageDraw, ImageFont
        self.arabic = features.check("raqm")  # تشكيل الحروف العربية متوفر؟
        self.fonts = {}
        self.cpu_h = deque(maxlen=HIST)
        self.temp_h = deque(maxlen=HIST)
        self.rx_h = deque(maxlen=HIST)
        self.tx_h = deque(maxlen=HIST)

    def font(self, size, bold=False):
        key = (size, bold)
        if key not in self.fonts:
            p = font_path(bold)
            self.fonts[key] = (self.ImageFont.truetype(p, size) if p
                               else self.ImageFont.load_default(size))
        return self.fonts[key]

    def t(self, ar, en):
        return ar if self.arabic else en

    def text(self, d, xy, s, size, color=TXT, bold=False, anchor="la"):
        d.text(xy, s, font=self.font(size, bold), fill=color, anchor=anchor)

    def card(self, d, box, title=None, right=None):
        d.rounded_rectangle(box, radius=22, fill=CARD, outline=LINE, width=2)
        x0, y0, x1, _ = box
        if title:
            # العنوان على اليمين بالعربي، وعلى اليسار بالإنجليزي
            if self.arabic:
                self.text(d, (x1 - 28, y0 + 22), title, 30, SUB, True, "ra")
                if right:
                    self.text(d, (x0 + 28, y0 + 24), right[0], 26, right[1], False, "la")
            else:
                self.text(d, (x0 + 28, y0 + 22), title, 30, SUB, True, "la")
                if right:
                    self.text(d, (x1 - 28, y0 + 24), right[0], 26, right[1], False, "ra")

    @staticmethod
    def level(pct, warn, crit):
        return GREEN if pct < warn else (YELLOW if pct < crit else RED)

    def gauge(self, d, box, label, pct, value, sub, color):
        x0, y0, x1, y1 = box
        self.card(d, box)
        cx, cy, r = (x0 + x1) // 2, y0 + 165, 115
        bb = (cx - r, cy - r, cx + r, cy + r)
        d.arc(bb, 135, 405, fill=LINE, width=26)
        end = 135 + 270 * max(0.0, min(1.0, pct / 100))
        if end > 136:
            d.arc(bb, 135, end, fill=color, width=26)
            # طرف دائري للقوس
            for ang in (135, end):
                a = math.radians(ang)
                px, py = cx + (r - 13) * math.cos(a), cy + (r - 13) * math.sin(a)
                d.ellipse((px - 13, py - 13, px + 13, py + 13), fill=color)
        self.text(d, (cx, cy), value, 60 if len(value) <= 4 else 50, TXT, True, "mm")
        self.text(d, (cx, cy + 112), sub, 24, SUB, False, "mm")
        self.text(d, (cx, y1 - 36), label, 32, TXT, True, "mm")

    def chart(self, d, box, series, vmax=None, fill=True):
        x0, y0, x1, y1 = box
        for i in range(1, 4):
            y = y0 + (y1 - y0) * i // 4
            d.line((x0, y, x1, y), fill=LINE, width=1)
        top = vmax or max([max(s, default=0) for s, _ in series] + [1])
        for data, color in series:
            if len(data) < 2:
                continue
            step = (x1 - x0) / (HIST - 1)
            off = HIST - len(data)
            pts = [(x0 + (off + i) * step, y1 - (y1 - y0) * min(v / top, 1)) for i, v in enumerate(data)]
            if fill:
                dim = tuple(int(c * 0.28 + b * 0.72) for c, b in zip(color, CARD))
                d.polygon(pts + [(pts[-1][0], y1), (pts[0][0], y1)], fill=dim)
            d.line(pts, fill=color, width=4, joint="curve")

    def render(self, cpu, rx, tx):
        img = self.Image.new("RGB", (self.W, self.H), BG)
        d = self.ImageDraw.Draw(img)
        A = self.arabic

        # ── الرأس
        self.text(d, (60, 42), "SALHA PI", 64, CYAN, True)
        self.text(d, (60, 118), f"{ps.socket.gethostname()}   ·   {ps.ip_addr()}", 28, SUB)
        self.text(d, (1860, 34), time.strftime("%H:%M:%S"), 72, TXT, True, "ra")
        days = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]
        day = days[time.localtime().tm_wday] if A else time.strftime("%A")
        self.text(d, (1860, 120), f"{day}  {time.strftime('%d.%m.%Y')}", 30, SUB, False, "ra")

        # ── العدادات
        t = ps.read("/sys/class/thermal/thermal_zone0/temp")
        temp = int(t) / 1000 if t.lstrip("-").isdigit() else None
        self.cpu_h.append(cpu)
        if temp is not None:
            self.temp_h.append(temp)
        self.rx_h.append(rx)
        self.tx_h.append(tx)

        mem = {}
        for line in ps.read("/proc/meminfo").split("\n"):
            k, _, v = line.partition(":")
            if v:
                mem[k] = int(v.split()[0]) * 1024
        mt = mem.get("MemTotal", 1)
        mu = mt - mem.get("MemAvailable", 0)
        freq = ps.read("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq")
        freq = f"{int(freq) // 1000} MHz" if freq.isdigit() else ""
        hdd = ps.disk("/mnt/hdd") if os.path.ismount("/mnt/hdd") else None

        gw, gy, gap = 435, 180, 20
        boxes = [(60 + i * (gw + gap), gy, 60 + i * (gw + gap) + gw, gy + 360) for i in range(4)]
        self.gauge(d, boxes[0], self.t("المعالج", "CPU"), cpu, f"{cpu:.0f}%", freq,
                   self.level(cpu, 70, 90))
        if temp is not None:
            tp = (temp - 30) / 55 * 100
            self.gauge(d, boxes[1], self.t("الحرارة", "TEMP"), tp, f"{temp:.1f}°",
                       self.t("درجة مئوية", "Celsius"), GREEN if temp < 60 else (ORANGE if temp < 75 else RED))
        else:
            self.gauge(d, boxes[1], self.t("الحرارة", "TEMP"), 0, "–", "", SUB)
        rp = mu / mt * 100
        self.gauge(d, boxes[2], self.t("الذاكرة", "RAM"), rp, f"{rp:.0f}%",
                   f"{ps.human(mu)} / {ps.human(mt)}", self.level(rp, 80, 92))
        if hdd:
            hp = hdd[0] / hdd[1] * 100
            self.gauge(d, boxes[3], self.t("الهارد ديسك", "HDD"), hp, f"{hp:.0f}%",
                       f"{ps.human(hdd[0])} / {ps.human(hdd[1])}", self.level(hp, 80, 92) if hp > 1 else BLUE)
        else:
            self.gauge(d, boxes[3], self.t("الهارد ديسك", "HDD"), 0, "!", self.t("غير موصول", "not mounted"), RED)

        # ── رسم المعالج والحرارة
        box = (60, 560, 1220, 800)
        self.card(d, box, self.t("المعالج والحرارة — آخر 5 دقائق", "CPU & TEMP — last 5 min"))
        self.chart(d, (90, 620, 1190, 780), [(list(self.cpu_h), CYAN)], vmax=100)
        if self.temp_h:
            self.chart(d, (90, 620, 1190, 780), [([max(0, (v - 30) / 55 * 100) for v in self.temp_h], ORANGE)],
                       vmax=100, fill=False)
        lx = box[0] + 30 if A else box[2] - 330
        for i, (lab, col) in enumerate(((self.t("المعالج", "CPU"), CYAN), (self.t("الحرارة", "TEMP"), ORANGE))):
            x = lx + i * 160
            d.ellipse((x, box[1] + 30, x + 18, box[1] + 48), fill=col)
            self.text(d, (x + 28, box[1] + 26), lab, 22, SUB)

        # ── الشبكة
        box = (1240, 560, 1860, 800)
        self.card(d, box, self.t("الشبكة", "NETWORK"))
        self.text(d, (1270, 640), "↓ " + ps.human(rx) + "/s", 36, GREEN, True)
        self.text(d, (1270, 690), "↑ " + ps.human(tx) + "/s", 36, PURPLE, True)
        self.chart(d, (1560, 625, 1835, 780), [(list(self.rx_h), GREEN), (list(self.tx_h), PURPLE)])

        # ── سيرفرات IPTV
        box = (60, 820, 1220, 1040)
        st, age = ps.iptv()
        right = None
        if st is not None and age > 600:
            right = (self.t(f"آخر فحص قبل {int(age // 60)} دقيقة", f"last check {int(age // 60)} min ago"), RED)
        elif st is not None:
            right = (self.t(f"آخر فحص قبل {int(age)} ث", f"checked {int(age)}s ago"), SUB)
        self.card(d, box, self.t("سيرفرات LIBANON 4K", "LIBANON 4K SERVERS"), right)
        y = 890
        if not st:
            self.text(d, (90, y), self.t("المراقب غير مركّب", "monitor not installed"), 30, SUB)
        for name, s in list((st or {}).items())[:3]:
            if not isinstance(s, dict):
                continue
            ok = not s.get("fails") and age <= 600
            col = GREEN if ok else (RED if s.get("fails") else YELLOW)
            d.ellipse((90, y + 6, 116, y + 32), fill=col)
            url = (s.get("last_ok_url") or "").split("//")[-1]
            exp = s.get("exp")
            exp = time.strftime("%d.%m.%Y", time.localtime(int(exp))) if str(exp).isdigit() else "–"
            self.text(d, (135, y), name, 34, TXT, True)
            self.text(d, (260, y + 3), url, 30, SUB)
            self.text(d, (560, y + 3), f"{s.get('last_ms') or '–'} ms", 30, TXT)
            self.text(d, (710, y + 3), f"{self.t('اتصالات', 'conn')} {s.get('cons', '–')}", 30, TXT)
            self.text(d, (1190, y + 3), f"{self.t('حتى', 'until')} {exp}", 30, SUB, False, "ra")
            y += 52

        # ── النظام
        box = (1240, 820, 1860, 1040)
        self.card(d, box, self.t("النظام", "SYSTEM"))
        sd = ps.disk("/")
        th = ps.throttled() or ""
        power_ok = "OK" in th and "earlier" not in th
        rows = [
            (self.t("التشغيل", "Uptime"), ps.uptime(), TXT),
            ("SD", f"{ps.human(sd[0])} / {ps.human(sd[1])}" if sd else "–", TXT),
            (self.t("الكهرباء", "Power"), ("OK" if power_ok else ("!" if th else "–")),
             GREEN if power_ok else (RED if th else SUB)),
        ]
        cs = ps.containers()
        if cs:
            up = sum(1 for _, ok in cs if ok)
            rows.append((self.t("الخدمات", "Services"), f"{up}/{len(cs)}", GREEN if up == len(cs) else RED))
        y = 885
        for k, v, c in rows[:4]:
            if A:
                self.text(d, (1830, y), k, 28, SUB, False, "ra")
                self.text(d, (1270, y), v, 28, c, True, "la")
            else:
                self.text(d, (1270, y), k, 28, SUB)
                self.text(d, (1830, y), v, 28, c, True, "ra")
            y += 38
        return img


def main():
    test = "--png" in sys.argv
    try:
        p = Painter()
        fb = None if test else Fb()
    except Exception as e:
        if test:
            raise
        print(f"graphics unavailable ({e}) — text mode", flush=True)
        os.execv(sys.executable, [sys.executable, "-u", os.path.join(os.path.dirname(os.path.abspath(__file__)), "pi_screen.py")])
    if not test:
        sys.stdout.write("\033[?25l\033[H\033[2J")  # إخفاء المؤشر والنص
        sys.stdout.flush()
    idle0, tot0 = ps.cpu_times()
    rx0, tx0 = ps.net_bytes()
    t0 = time.time()
    time.sleep(1)
    while True:
        idle1, tot1 = ps.cpu_times()
        rx1, tx1 = ps.net_bytes()
        t1 = time.time()
        dt = max(t1 - t0, 0.1)
        cpu = 100 * (1 - (idle1 - idle0) / max(tot1 - tot0, 1))
        img = p.render(cpu, (rx1 - rx0) / dt, (tx1 - tx0) / dt)
        if test:
            img.save(sys.argv[sys.argv.index("--png") + 1])
            return
        try:
            fb.show(img)
        except Exception as e:  # لا نعيد التشغيل كل 3 ثوانٍ — نعود للشاشة النصية
            print(f"graphics failed ({e}) — text mode", flush=True)
            os.execv(sys.executable, [sys.executable, "-u", os.path.join(os.path.dirname(os.path.abspath(__file__)), "pi_screen.py")])
        idle0, tot0, rx0, tx0, t0 = idle1, tot1, rx1, tx1, t1
        time.sleep(max(0.2, REFRESH - (time.time() - t1)))


if __name__ == "__main__":
    main()
