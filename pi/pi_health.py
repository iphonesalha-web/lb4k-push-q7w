#!/usr/bin/env python3
# حارس الـ Raspberry Pi: يفحص الحرارة والكهرباء والهارد والذاكرة والخدمات كل دقيقة،
# ويرسل تنبيهاً على تيليجرام عند المشكلة ورسالة عند زوالها.
# ويردّ على الأوامر: /status (أو «حالة») و /help — فقط من نفس الـ Chat ID.
# يستعمل نفس البوت المحفوظ لمراقب السيرفرات (~/.iptv-monitor/config.json).
import glob, json, os, sys, time, urllib.parse, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pi_screen as ps

STATE = "/var/lib/pi-health/state.json"
CHECK_EVERY = 60

# الحدود
TEMP_ALERT, TEMP_CLEAR = 75.0, 68.0
DISK_ALERT = 90.0
RAM_ALERT = 92.0
LOAD_ALERT = 6.0           # 4 أنوية — حمل 6 لمدة طويلة يعني شيئاً عالقاً
MONITOR_STALE = 15 * 60    # مراقب السيرفرات لم يفحص منذ 15 دقيقة

# كم فحصاً متتالياً قبل التنبيه (لتجنّب التنبيهات الكاذبة)
NEED = {"temp": 2, "power": 1, "hdd_mount": 2, "disk_/": 2, "disk_/mnt/hdd": 2,
        "ram": 3, "load": 5, "monitor": 1}


def load_conf():
    for p in glob.glob("/home/*/.iptv-monitor/config.json") + glob.glob("/root/.iptv-monitor/config.json"):
        try:
            with open(p) as f:
                c = json.load(f)
            if c.get("bot") and c.get("chat"):
                return c
        except (OSError, ValueError):
            pass
    return None


def load_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(st):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, ensure_ascii=False)
    os.replace(tmp, STATE)


def api(conf, method, params, timeout=20):
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(f"https://api.telegram.org/bot{conf['bot']}/{method}",
                                data=data, timeout=timeout) as r:
        return json.loads(r.read())


def send(conf, text):
    try:
        api(conf, "sendMessage", {"chat_id": conf["chat"], "text": text})
        return True
    except Exception as e:
        print("telegram failed:", e, file=sys.stderr, flush=True)
        return False


def dur(sec):
    sec = int(sec)
    if sec < 3600:
        return f"{max(sec // 60, 1)} دقيقة"
    h, m = divmod(sec // 60, 60)
    return f"{h} ساعة" + (f" و{m} دقيقة" if m else "")


def temp():
    t = ps.read("/sys/class/thermal/thermal_zone0/temp")
    return int(t) / 1000 if t.lstrip("-").isdigit() else None


def ram_pct():
    mem = {}
    for line in ps.read("/proc/meminfo").split("\n"):
        k, _, v = line.partition(":")
        if v:
            mem[k] = int(v.split()[0])
    t = mem.get("MemTotal", 0)
    return (t - mem.get("MemAvailable", t)) / t * 100 if t else 0


def throttle_now():
    import shutil, subprocess
    if not shutil.which("vcgencmd"):
        return None
    try:
        out = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=3).stdout
        v = int(out.strip().split("=")[1], 16)
    except Exception:
        return None
    w = []
    if v & 0x1: w.append("الكهرباء ضعيفة (Under-voltage) — الشاحن أو الكابل")
    if v & 0x4: w.append("المعالج أبطأ من المعتاد (Throttled)")
    if v & 0x8: w.append("وصل حد الحرارة")
    return w


def problems():
    """يعيد قاموس {المفتاح: نص المشكلة} للمشاكل الموجودة الآن."""
    p = {}
    t = temp()
    if t is not None and t >= TEMP_ALERT:
        p["temp"] = f"🌡️ حرارة الـ Pi مرتفعة: {t:.1f}°"
    w = throttle_now()
    if w:
        p["power"] = "⚡ " + "، ".join(w)
    if not os.path.ismount("/mnt/hdd"):
        p["hdd_mount"] = "💽 الهارد ديسك غير موصول (/mnt/hdd)"
    for path, name in (("/", "كرت SD"), ("/mnt/hdd", "الهارد ديسك")):
        if path != "/" and "hdd_mount" in p:
            continue
        d = ps.disk(path)
        if d and d[0] / d[1] * 100 >= DISK_ALERT:
            p["disk_" + path] = (f"📦 {name} شبه ممتلئ: {d[0] / d[1] * 100:.0f}% "
                                 f"(فارغ {ps.human(d[1] - d[0])})")
    r = ram_pct()
    if r >= RAM_ALERT:
        p["ram"] = f"🧠 الذاكرة شبه ممتلئة: {r:.0f}%"
    load5 = float(ps.read("/proc/loadavg", "0 0 0").split()[1])
    if load5 >= LOAD_ALERT:
        p["load"] = f"🔥 المعالج مضغوط منذ دقائق (load {load5:.1f})"
    st, age = ps.iptv()
    if st is not None and age > MONITOR_STALE:
        p["monitor"] = f"🛰️ مراقب سيرفرات LIBANON 4K متوقف — آخر فحص قبل {dur(age)}"
    cs = ps.containers()
    for name, ok in cs or []:
        if not ok:
            p["svc_" + name] = f"🧩 الخدمة {name} لا تعمل كما يجب"
    return p


def status_text():
    t = temp()
    d_sd = ps.disk("/")
    hdd = ps.disk("/mnt/hdd") if os.path.ismount("/mnt/hdd") else None
    load = " ".join(ps.read("/proc/loadavg", "0 0 0").split()[:3])
    lines = ["📊 حالة الـ Pi", ""]
    lines.append(f"🌡️ الحرارة: {t:.1f}°" if t is not None else "🌡️ الحرارة: –")
    lines.append(f"⚙️ الحمل: {load}")
    lines.append(f"🧠 الذاكرة: {ram_pct():.0f}%")
    if d_sd:
        lines.append(f"💾 SD: {ps.human(d_sd[0])} / {ps.human(d_sd[1])}")
    lines.append(f"💽 الهارد: {ps.human(hdd[0])} / {ps.human(hdd[1])}" if hdd else "💽 الهارد: غير موصول ⚠️")
    w = throttle_now()
    if w is not None:
        lines.append("⚡ الكهرباء: " + ("سليمة ✅" if not w else "، ".join(w)))
    lines.append(f"⏱️ يعمل منذ: {ps.uptime()}")
    lines.append(f"🌐 IP: {ps.ip_addr()}")
    st, age = ps.iptv()
    if st:
        lines.append("")
        for name, s in st.items():
            if isinstance(s, dict):
                ok = not s.get("fails") and age < MONITOR_STALE
                lines.append(f"{'✅' if ok else '⚠️'} {name}: {s.get('last_ms') or '–'} ms · "
                             f"اتصالات {s.get('cons', '–')}")
        lines.append(f"(آخر فحص قبل {int(age)} ث)" if age < 120 else f"(آخر فحص قبل {dur(age)})")
    cs = ps.containers()
    if cs:
        lines.append("")
        lines.append("🧩 " + "  ".join(f"{'🟢' if ok else '🔴'} {n}" for n, ok in cs))
    probs = problems()
    lines.append("")
    lines.append("✅ لا توجد مشاكل" if not probs else "⚠️ " + " | ".join(probs.values()))
    return "\n".join(lines)


HELP = ("🤖 أوامر حارس الـ Pi:\n"
        "/status — حالة الـ Pi الآن (أو اكتب: حالة)\n"
        "/help — هذه القائمة\n\n"
        "التنبيهات تصلك تلقائياً عند: ارتفاع الحرارة فوق 75°، ضعف الكهرباء، "
        "فصل الهارد ديسك، امتلاء التخزين فوق 90%، امتلاء الذاكرة، ضغط المعالج، "
        "توقف مراقب السيرفرات، توقف خدمة، وعند إعادة تشغيل الـ Pi.")


def check(conf, st):
    now = time.time()
    probs = problems()
    fails = st.setdefault("fails", {})
    alerted = st.setdefault("alerted", {})
    for key, msg in probs.items():
        fails[key] = fails.get(key, 0) + 1
        if fails[key] >= NEED.get(key, 2) and key not in alerted:
            if send(conf, msg):
                alerted[key] = now
    for key in list(alerted):
        clear = key not in probs
        if key == "temp":  # ننتظر نزولها تحت 68° حتى لا تتكرر الرسائل حول 75°
            t = temp()
            clear = t is not None and t < TEMP_CLEAR
        if clear:
            since = alerted.pop(key)
            send(conf, f"✅ زالت المشكلة بعد {dur(now - since)}: " + {
                "temp": f"الحرارة رجعت {temp():.1f}°",
                "power": "الكهرباء سليمة",
                "hdd_mount": "الهارد ديسك موصول",
                "ram": "الذاكرة طبيعية",
                "load": "المعالج طبيعي",
                "monitor": "مراقب السيرفرات يعمل",
            }.get(key, key.replace("disk_", "التخزين ").replace("svc_", "الخدمة ")))
    for key in list(fails):
        if key not in probs:
            fails.pop(key)


def handle_updates(conf, st, wait):
    try:
        r = api(conf, "getUpdates", {"offset": st.get("offset", 0), "timeout": int(wait),
                                     "allowed_updates": json.dumps(["message"])}, timeout=wait + 15)
    except Exception as e:
        print("getUpdates failed:", e, file=sys.stderr, flush=True)
        time.sleep(min(wait, 30))
        return
    for u in r.get("result", []):
        st["offset"] = u["update_id"] + 1
        m = u.get("message") or {}
        if str(m.get("chat", {}).get("id")) != str(conf["chat"]):
            continue  # لا نردّ على أي شخص آخر
        text = (m.get("text") or "").strip().lower()
        if text.startswith("/status") or text in ("حالة", "الحالة", "status", "pi"):
            send(conf, status_text())
        elif text.startswith("/help") or text.startswith("/start") or text in ("مساعدة", "help"):
            send(conf, HELP)
    save_state(st)


def main():
    if "status" in sys.argv:
        print(status_text())
        return
    conf = load_conf()
    if not conf:
        print("لا يوجد إعداد بوت — ركّب مراقب السيرفرات أولاً", flush=True)
        time.sleep(300)
        return
    st = load_state()
    if "test" in sys.argv:
        print("sent" if send(conf, "🤖 حارس الـ Pi يعمل ✅\n\n" + status_text()) else "failed")
        return
    up = float(ps.read("/proc/uptime", "0").split()[0])
    if up < 600:
        send(conf, f"🔌 الـ Pi أعاد التشغيل (قبل {dur(up)}) — ربما انقطعت الكهرباء.\n\n" + status_text())
    last = 0
    while True:
        if time.time() - last >= CHECK_EVERY:
            last = time.time()
            try:
                check(conf, st)
            except Exception as e:
                print("check failed:", e, file=sys.stderr, flush=True)
            save_state(st)
        handle_updates(conf, st, max(1, CHECK_EVERY - (time.time() - last)))


if __name__ == "__main__":
    main()
