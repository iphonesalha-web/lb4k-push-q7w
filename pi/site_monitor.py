#!/usr/bin/env python3
# مراقب المواقع المشهورة — يرسل تنبيهاً على تيليجرام عندما يتعطّل موقع أو خدمة، ورسالة عند عودتها.
#
# مصدران:
#  1) صفحات الحالة الرسمية (Claude، GitHub، Cloudflare، Discord، Firebase، Apple):
#     الشركة نفسها تعلن المشكلة، حتى الجزئية (مثل: تسجيل الدخول لا يعمل).
#  2) فحص مباشر من البيت (YouTube، WhatsApp، eBay، PayPal، Microsoft 365 …):
#     يعتبر الموقع متعطلاً فقط إن لم يرد 3 مرات متتالية (~6 دقائق)، وإنترنت البيت سليم،
#     وأغلب المواقع الأخرى تعمل — حتى لا يصل تنبيه كاذب بسبب انقطاع عندنا.
#
# التشغيل: كل دقيقتين (systemd timer).  يدوياً:  sudo python3 site_monitor.py status
import glob, json, os, re, socket, ssl, sys, time, urllib.error, urllib.parse, urllib.request

CONF = "/etc/site-monitor.json"
STATE = "/var/lib/site-monitor/state.json"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

DEFAULT_CONF = {
    "statuspages": {
        "Claude": "https://status.claude.com",
        "GitHub": "https://www.githubstatus.com",
        "Cloudflare": "https://www.cloudflarestatus.com",
        "Discord": "https://discordstatus.com",
    },
    "firebase": True,
    "apple": True,
    "http": {
        "Google": "https://www.google.com",
        "YouTube": "https://www.youtube.com",
        "WhatsApp": "https://web.whatsapp.com",
        "Instagram": "https://www.instagram.com",
        "Facebook": "https://www.facebook.com",
        "TikTok": "https://www.tiktok.com",
        "Netflix": "https://www.netflix.com",
        "Amazon": "https://www.amazon.de",
        "eBay": "https://www.ebay.de",
        "PayPal": "https://www.paypal.com",
        "Microsoft 365": "https://www.office.com",
        "Outlook": "https://outlook.live.com",
        "Strato": "https://www.strato.de",
        "Telekom": "https://www.telekom.de",
        "X (Twitter)": "https://x.com",
    },
    "http_fail_needed": 3,
}


# ───────────────────────── أدوات

def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_state(st):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, ensure_ascii=False)
    os.replace(tmp, STATE)


def telegram_conf():
    for p in glob.glob("/home/*/.iptv-monitor/config.json"):
        c = load_json(p, {})
        if c.get("bot") and c.get("chat"):
            return c
    return None


def send(text):
    c = telegram_conf()
    if not c:
        print(text)
        return True
    try:
        data = urllib.parse.urlencode({"chat_id": c["chat"], "text": text,
                                       "disable_web_page_preview": "true"}).encode()
        urllib.request.urlopen(f"https://api.telegram.org/bot{c['bot']}/sendMessage",
                               data=data, timeout=20).read()
        return True
    except Exception as e:
        print("telegram failed:", e, file=sys.stderr)
        return False


def fetch(url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def dur(sec):
    m = max(int(sec // 60), 1)
    if m < 60:
        return f"{m} دقيقة"
    h, m = divmod(m, 60)
    return f"{h} ساعة" + (f" و{m} دقيقة" if m else "")


def home_ok():
    for ip in ("1.1.1.1", "8.8.8.8"):
        try:
            socket.create_connection((ip, 443), timeout=5).close()
            return True
        except OSError:
            pass
    return False


# ───────────────────────── المصدر 1: صفحات الحالة الرسمية → {مفتاح: (اسم الخدمة, نص المشكلة, رابط)}

IMPACT_AR = {"minor": "جزئية", "major": "كبيرة", "critical": "حرجة"}


def check_statuspage(name, base):
    out = {}
    j = json.loads(fetch(base.rstrip("/") + "/api/v2/summary.json"))
    for inc in j.get("incidents", []):
        if inc.get("status") in ("resolved", "postmortem"):
            continue
        impact = inc.get("impact", "none")
        if impact == "none":
            continue
        key = f"sp:{name}:{inc.get('id')}"
        out[key] = (name, f"{inc.get('name', '')} (مشكلة {IMPACT_AR.get(impact, impact)})",
                    inc.get("shortlink") or base)
    return out


def check_firebase():
    out = {}
    for inc in json.loads(fetch("https://status.firebase.google.com/incidents.json")):
        if inc.get("end") or inc.get("severity") == "low":
            continue
        prods = ", ".join(p.get("title", "") for p in inc.get("affected_products", []))[:80]
        key = f"fb:{inc.get('id')}"
        out[key] = ("Firebase", f"{prods}: {inc.get('external_desc', '')[:150]}",
                    "https://status.firebase.google.com")
    return out


def check_apple():
    out = {}
    raw = fetch("https://www.apple.com/support/systemstatus/data/system_status_en_US.js")
    raw = raw[raw.index("{"): raw.rindex("}") + 1]   # إزالة غلاف JSONP
    for svc in json.loads(raw).get("services", []):
        for ev in svc.get("events", []):
            if ev.get("eventStatus") != "ongoing":
                continue
            if ev.get("statusType") == "Maintenance":
                continue
            key = f"ap:{svc.get('serviceName')}:{ev.get('epochStartDate', ev.get('startDate'))}"
            out[key] = (f"Apple {svc.get('serviceName')}",
                        (ev.get("message") or ev.get("usersAffected") or ev.get("statusType", ""))[:150],
                        "https://www.apple.com/support/systemstatus/")
    return out


# ───────────────────────── المصدر 2: فحص مباشر

def http_ok(url):
    """True = يعمل. أي رد من السيرفر (حتى 403/429) يعني أنه حيّ — فقط 5xx أو لا رد = متعطل."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA}, method="GET")
        with urllib.request.urlopen(req, timeout=12) as r:
            r.read(2048)
        return True, ""
    except urllib.error.HTTPError as e:
        return (e.code < 500), f"HTTP {e.code}"
    except (urllib.error.URLError, socket.timeout, ssl.SSLError, ConnectionError, OSError) as e:
        reason = getattr(e, "reason", e)
        txt = str(reason)
        if "timed out" in txt:
            txt = "لا يرد (مهلة)"
        elif "Name or service not known" in txt or "nodename" in txt or "Temporary failure" in txt:
            txt = "العنوان غير معروف (DNS)"
        elif "refused" in txt:
            txt = "رفض الاتصال"
        return False, txt[:80]


# ───────────────────────── التشغيل

def run(report_only=False):
    conf = DEFAULT_CONF | load_json(CONF, {})
    st = load_state()
    now = time.time()
    alerts, recov = [], []

    # 1) صفحات الحالة
    official = {}
    sources = [(n, (lambda n=n, u=u: check_statuspage(n, u))) for n, u in conf["statuspages"].items()]
    if conf.get("firebase"):
        sources.append(("Firebase", check_firebase))
    if conf.get("apple"):
        sources.append(("Apple", check_apple))
    src_ok = {}
    for name, fn in sources:
        try:
            official.update(fn())
            src_ok[name] = True
        except Exception as e:
            src_ok[name] = False
            print(f"{name}: status page failed: {e}", file=sys.stderr)

    known = st.setdefault("official", {})
    for key, (svc, text, link) in official.items():
        if key not in known:
            known[key] = {"svc": svc, "text": text, "since": now}
            alerts.append(f"🟠 {svc}: مشكلة معلنة رسمياً\n{text}\n{link}")
    for key in list(known):
        svc = known[key]["svc"]
        src = "Apple" if key.startswith("ap:") else "Firebase" if key.startswith("fb:") else svc
        if key not in official and src_ok.get(src, False):
            since = known.pop(key)["since"]
            recov.append(f"🟢 {svc}: انتهت المشكلة المعلنة ({dur(now - since)})")

    # 2) فحص مباشر
    results = {name: http_ok(url) for name, url in conf["http"].items()}
    ok_ratio = sum(1 for ok, _ in results.values() if ok) / max(len(results), 1)
    local_trouble = ok_ratio < 0.6 or not home_ok()
    hs = st.setdefault("http", {})
    need = int(conf.get("http_fail_needed", 3))
    for name, (ok, err) in results.items():
        h = hs.setdefault(name, {"fails": 0})
        if ok:
            if h.get("alerted"):
                recov.append(f"🟢 {name} يعمل من جديد (توقف حوالي {dur(now - h['since'])})")
            hs[name] = {"fails": 0}
            continue
        if local_trouble:
            continue           # المشكلة عندنا لا عندهم — لا نعدّها
        h["fails"] = h.get("fails", 0) + 1
        h.setdefault("since", now)
        h["err"] = err
        if h["fails"] >= need and not h.get("alerted"):
            h["alerted"] = True
            alerts.append(f"🔴 {name} متعطل — {err}\n(فُحص {h['fails']} مرات من البيت، والمواقع الأخرى تعمل)")

    st["last_run"] = now
    st["last_http"] = {n: ok for n, (ok, _) in results.items()}
    st["local_trouble"] = local_trouble
    save_state(st)

    if not report_only:
        for m in alerts + recov:
            send(m)
    return st


def load_state():
    return load_json(STATE, {})


def status_text(st=None):
    st = st or load_state()
    if not st.get("last_run"):
        return "🌐 مراقب المواقع لم يعمل بعد."
    lines = ["🌐 حالة المواقع", ""]
    off = st.get("official", {})
    if off:
        for v in off.values():
            lines.append(f"🟠 {v['svc']}: {v['text']}")
    down = [n for n, h in st.get("http", {}).items() if h.get("alerted")]
    for n in down:
        lines.append(f"🔴 {n}: متعطل منذ {dur(time.time() - st['http'][n]['since'])}")
    up = [n for n, ok in st.get("last_http", {}).items() if ok]
    if not off and not down:
        lines.append("✅ كل المواقع والخدمات تعمل")
    lines.append("")
    lines.append("يعمل: " + "، ".join(up))
    if st.get("local_trouble"):
        lines.append("⚠️ آخر فحص: مشكلة في إنترنت البيت")
    lines.append(f"(آخر فحص قبل {int((time.time() - st['last_run']) // 60)} دقيقة)")
    return "\n".join(lines)


if __name__ == "__main__":
    if "status" in sys.argv:
        st = load_state()
        print(status_text(st if st.get("last_run") else run(report_only=True)))
    elif "test" in sys.argv:
        print("sent" if send("🌐 مراقب المواقع يعمل ✅\n\n" + status_text(run(report_only=True))) else "failed")
    else:
        run()
