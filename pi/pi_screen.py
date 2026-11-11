#!/usr/bin/env python3
# شاشة معلومات الـ Raspberry Pi على التلفزيون (tty1) — تُحدَّث كل 3 ثوانٍ.
# للقراءة فقط: لا تنفّذ أي أوامر تغيّر شيئاً في النظام.
import json, os, shutil, socket, subprocess, sys, time

REFRESH = 3
STATE_GLOBS = ["/home/*/.iptv-monitor/state.json"]

R, G, Y, C, W, D, B, X = ("\033[31m", "\033[32m", "\033[33m", "\033[36m",
                          "\033[37m", "\033[90m", "\033[1m", "\033[0m")


def read(path, default=""):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def cpu_times():
    v = [int(x) for x in read("/proc/stat").split("\n")[0].split()[1:]]
    idle = v[3] + (v[4] if len(v) > 4 else 0)
    return idle, sum(v)


def net_bytes():
    rx = tx = 0
    for line in read("/proc/net/dev").split("\n")[2:]:
        name, _, rest = line.partition(":")
        name = name.strip()
        if not rest or name == "lo" or name.startswith(("docker", "veth", "br-")):
            continue
        f = rest.split()
        rx += int(f[0]); tx += int(f[8])
    return rx, tx


def bar(pct, width=24, warn=70, crit=90):
    pct = max(0.0, min(100.0, pct))
    n = int(round(pct / 100 * width))
    col = G if pct < warn else (Y if pct < crit else R)
    return f"{col}{'█' * n}{D}{'░' * (width - n)}{X}"


def human(b):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024 or unit == "TB":
            return f"{b:.0f} {unit}" if unit in ("B", "KB") else f"{b:.1f} {unit}"
        b /= 1024


def disk(path):
    try:
        u = shutil.disk_usage(path)
        return u.used, u.total
    except OSError:
        return None


def ip_addr():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.168.178.1", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "-"


def uptime():
    sec = int(float(read("/proc/uptime", "0").split()[0]))
    d, sec = divmod(sec, 86400)
    h, sec = divmod(sec, 3600)
    m = sec // 60
    return (f"{d}d " if d else "") + f"{h}h {m}m"


def throttled():
    if not shutil.which("vcgencmd"):
        return None
    try:
        out = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True,
                             text=True, timeout=2).stdout
        v = int(out.strip().split("=")[1], 16)
    except Exception:
        return None
    now = []
    if v & 0x1: now.append("UNDER-VOLTAGE")
    if v & 0x4: now.append("THROTTLED")
    if v & 0x8: now.append("TEMP LIMIT")
    if now:
        return f"{R}{' + '.join(now)}{X}"
    if v & 0x50000:
        return f"{Y}OK (power dropped earlier){X}"
    return f"{G}OK{X}"


def containers():
    if not shutil.which("docker"):
        return None
    try:
        out = subprocess.run(["docker", "ps", "--format", "{{.Names}}|{{.Status}}"],
                             capture_output=True, text=True, timeout=3).stdout
    except Exception:
        return None
    rows = []
    for line in out.strip().split("\n"):
        if "|" in line:
            n, s = line.split("|", 1)
            ok = "unhealthy" not in s and "Restarting" not in s
            rows.append((n, ok))
    return rows


def iptv():
    import glob
    for pattern in STATE_GLOBS:
        for path in glob.glob(pattern):
            try:
                with open(path) as f:
                    st = json.load(f)
                return st, time.time() - os.path.getmtime(path)
            except (OSError, ValueError):
                pass
    return None, 0


def render(cpu_pct, rx_rate, tx_rate):
    lines = []
    host = socket.gethostname()
    lines.append(f"  {B}{C}SALHA PI{X}  {D}·{X}  {host}"
                 f"{' ' * 18}{W}{time.strftime('%a %d.%m.%Y   %H:%M:%S')}{X}")
    lines.append(f"  {D}{'─' * 66}{X}")
    lines.append("")

    freq = read("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq")
    freq = f"{int(freq) // 1000} MHz" if freq.isdigit() else ""
    load = " ".join(read("/proc/loadavg", "0 0 0").split()[:3])
    lines.append(f"  CPU    {bar(cpu_pct)}  {cpu_pct:5.1f}%   {D}{freq}   load {load}{X}")

    t = read("/sys/class/thermal/thermal_zone0/temp")
    if t.lstrip("-").isdigit():
        temp = int(t) / 1000
        tcol = G if temp < 60 else (Y if temp < 75 else R)
        lines.append(f"  TEMP   {bar(temp / 85 * 100, warn=70, crit=88)}  {tcol}{temp:5.1f} C{X}")

    mem = {}
    for line in read("/proc/meminfo").split("\n"):
        k, _, v = line.partition(":")
        if v:
            mem[k] = int(v.split()[0]) * 1024
    if mem.get("MemTotal"):
        used = mem["MemTotal"] - mem.get("MemAvailable", 0)
        lines.append(f"  RAM    {bar(used / mem['MemTotal'] * 100, warn=80, crit=92)}  "
                     f"{human(used)} / {human(mem['MemTotal'])}")

    for label, path in (("SD  ", "/"), ("HDD ", "/mnt/hdd")):
        if path != "/" and not os.path.ismount(path):
            lines.append(f"  {label}   {R}not mounted{X}")
            continue
        d = disk(path)
        if d:
            lines.append(f"  {label}   {bar(d[0] / d[1] * 100, warn=80, crit=92)}  "
                         f"{human(d[0])} / {human(d[1])}")

    lines.append(f"  NET    {G}down {human(rx_rate)}/s{X}   {C}up {human(tx_rate)}/s{X}")
    lines.append(f"  IP     {B}{ip_addr()}{X}        UPTIME  {uptime()}")
    th = throttled()
    if th:
        lines.append(f"  POWER  {th}")

    st, age = iptv()
    if st is not None:
        lines.append("")
        stale = f"   {Y}(last check {int(age // 60)} min ago){X}" if age > 600 else ""
        lines.append(f"  {B}IPTV SERVERS{X}{stale}")
        for name, s in st.items():
            if not isinstance(s, dict):
                continue
            ok = not s.get("fails")
            mark = f"{G}● OK  {X}" if ok else f"{R}● DOWN{X}"
            url = (s.get("last_ok_url") or "").split("//")[-1]
            exp = s.get("exp")
            exp = time.strftime("%d.%m.%Y", time.localtime(int(exp))) if str(exp).isdigit() else "-"
            ms = f"{s.get('last_ms')} ms" if s.get("last_ms") else ""
            lines.append(f"   {mark} {name:<6} {url:<22} {ms:>7}   conn {s.get('cons', '-'):<5} "
                         f"{D}until {exp}{X}")

    cs = containers()
    if cs:
        lines.append("")
        lines.append(f"  {B}SERVICES{X}")
        row = "   "
        for n, ok in cs[:8]:
            row += f"{G if ok else R}●{X} {n}   "
        lines.append(row)
    return lines


def main():
    once = "--once" in sys.argv
    out = sys.stdout
    if not once:
        out.write("\033[?25l")  # إخفاء المؤشر
        try:
            subprocess.run(["setterm", "--blank", "0", "--powerdown", "0"], stdout=out, timeout=2)
        except Exception:
            pass
    idle0, tot0 = cpu_times()
    rx0, tx0 = net_bytes()
    t0 = time.time()
    time.sleep(1 if once else 0.5)
    while True:
        idle1, tot1 = cpu_times()
        rx1, tx1 = net_bytes()
        t1 = time.time()
        dt = max(t1 - t0, 0.1)
        cpu = 100 * (1 - (idle1 - idle0) / max(tot1 - tot0, 1))
        try:
            lines = render(cpu, (rx1 - rx0) / dt, (tx1 - tx0) / dt)
        except Exception as e:  # لا نترك الشاشة سوداء بسبب خطأ واحد
            lines = [f"  error: {e}"]
        out.write("\033[H\n" + "".join(l + "\033[K\n" for l in lines) + "\033[J")  # بدون وميض
        out.flush()
        if once:
            return
        idle0, tot0, rx0, tx0, t0 = idle1, tot1, rx1, tx1, t1
        time.sleep(REFRESH)


if __name__ == "__main__":
    main()
