#!/usr/bin/env python3
"""
مراقب سيرفرات LIBANON 4K — يعمل على الـ Raspberry Pi كل دقيقتين (cron).

لكل حساب (Neo / Dino):
  - يفحص تسجيل الدخول عبر player_api.php على كل دومين.
  - يفحص أن البث نفسه يعمل (ينزّل أول جزء من قناة حية).
  - ينبّه على تيليجرام عند: توقف السيرفر، عمل الدومين الاحتياطي فقط،
    API يعمل والبث لا، الحساب موقوف، اقتراب انتهاء الاشتراك.
  - ورسالة ثانية عند العودة للعمل مع مدة التوقف.

لا يُرسل تنبيه إلا بعد فشلين متتاليين (4 دقائق) — حتى لا تزعجك انقطاعات لحظية.

الاستعمال:
  iptv_monitor.py          فحص واحد (هذا ما يشغّله cron)
  iptv_monitor.py test     يرسل تقريراً فورياً بحالة كل السيرفرات
  iptv_monitor.py status   يطبع الحالة في الطرفية
"""
import json, os, sys, time, urllib.request, urllib.parse, socket

DIR = os.path.expanduser("~/.iptv-monitor")
CONF = os.path.join(DIR, "config.json")
STATE = os.path.join(DIR, "state.json")
UA = "Mozilla/5.0 (Linux; Android 11; TV) LibanonMonitor/1.0"
FAILS_BEFORE_ALERT = 2


def load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def save(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def http(url, timeout=12, limit=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(limit) if limit else r.read()


def telegram(conf, text):
    data = urllib.parse.urlencode({"chat_id": conf["chat"], "text": text}).encode()
    try:
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{conf['bot']}/sendMessage", data=data, timeout=20
        ).read()
        return True
    except Exception as e:
        print("telegram failed:", e, file=sys.stderr)
        return False


def check_api(url, user, pw):
    """يعيد (ok, info, error)."""
    q = urllib.parse.urlencode({"username": user, "password": pw})
    t0 = time.time()
    try:
        raw = http(f"{url.rstrip('/')}/player_api.php?{q}", timeout=12)
        ms = int((time.time() - t0) * 1000)
        j = json.loads(raw.decode("utf-8", "replace"))
        ui = j.get("user_info") or {}
        if str(ui.get("auth")) != "1":
            return False, {"ms": ms, "auth": False}, "الحساب مرفوض (auth=0)"
        return True, {"ms": ms, "auth": True, "status": ui.get("status"),
                      "exp": ui.get("exp_date"), "active": ui.get("active_cons"),
                      "max": ui.get("max_connections")}, None
    except socket.timeout:
        return False, {}, "انتهت المهلة"
    except urllib.error.HTTPError as e:
        return False, {}, f"HTTP {e.code}"
    except urllib.error.URLError as e:
        return False, {}, f"لا يمكن الوصول ({e.reason})"
    except Exception as e:
        return False, {}, str(e)[:120]


def check_stream(url, user, pw, state_acc):
    """ينزّل أول 256KB من قناة حية. يعيد (ok, error)."""
    sid = state_acc.get("stream_id")
    if not sid or time.time() - state_acc.get("stream_id_at", 0) > 86400:
        try:
            q = urllib.parse.urlencode({"username": user, "password": pw, "action": "get_live_streams"})
            lst = json.loads(http(f"{url.rstrip('/')}/player_api.php?{q}", timeout=25))
            if not lst:
                return False, "قائمة القنوات فارغة"
            # نفضّل قناة لبنانية معروفة، وإلا أول قناة
            pick = next((c for c in lst if "MTV" in str(c.get("name", "")).upper()), lst[0])
            sid = pick.get("stream_id")
            state_acc["stream_id"] = sid
            state_acc["stream_name"] = pick.get("name")
            state_acc["stream_id_at"] = time.time()
        except Exception as e:
            return False, f"تعذّر جلب القنوات ({str(e)[:60]})"
    try:
        t0 = time.time()
        data = http(f"{url.rstrip('/')}/live/{user}/{pw}/{sid}.ts", timeout=15, limit=256 * 1024)
        if len(data) < 32 * 1024:
            state_acc.pop("stream_id", None)  # ربما حُذفت القناة — نختار غيرها في المرة القادمة
            return False, f"البث لا يصل (وصل {len(data)//1024}KB فقط)"
        state_acc["stream_ms"] = int((time.time() - t0) * 1000)
        return True, None
    except Exception as e:
        state_acc.pop("stream_id", None)
        return False, f"البث لا يعمل ({str(e)[:60]})"


def dur(sec):
    sec = int(sec)
    if sec < 3600:
        return f"{max(1, sec // 60)} دقيقة"
    return f"{sec // 3600} ساعة و{(sec % 3600) // 60} دقيقة"


def run(mode="cron"):
    conf = load(CONF, None)
    if not conf:
        print("لا يوجد إعداد — شغّل install.sh أولاً")
        return 1
    state = load(STATE, {})
    now = time.time()
    report = []

    for acc in conf["accounts"]:
        name, user, pw, urls = acc["name"], acc["user"], acc["pass"], acc["urls"]
        st = state.setdefault(name, {})
        results = [(u,) + check_api(u, user, pw) for u in urls]
        working = [r for r in results if r[1]]
        problems = {}

        if not working:
            errs = "، ".join(f"{u.split('//')[-1]}: {e}" for u, _, _, e in results)
            problems["down"] = f"⛔ {name} متوقف بالكامل\n{errs}"
        else:
            url, _, info, _ = working[0]
            if not results[0][1] and len(urls) > 1:
                problems["main_down"] = (f"⚠️ {name}: الدومين الأساسي {urls[0].split('//')[-1]} لا يعمل "
                                         f"({results[0][3]})\nالاحتياطي {url.split('//')[-1]} يعمل — "
                                         f"التطبيق يبدّل تلقائياً.")
            if info.get("status") and info["status"] != "Active":
                problems["inactive"] = f"⛔ {name}: حالة الحساب {info['status']}"
            # فحص البث يستهلك اتصالاً من الحساب: نتخطاه إن كانت الاتصالات ممتلئة
            # (العائلة تشاهد) — ولا نكرره أكثر من مرة كل 10 دقائق.
            try:
                full = int(info.get("active") or 0) >= int(info.get("max") or 99)
            except ValueError:
                full = False
            due = (mode != "cron" or now - st.get("stream_checked_at", 0) >= 600
                   or "stream" in st.get("fails", {}))
            if full:
                st["stream_note"] = "تُخطّي فحص البث — كل الاتصالات مستعملة"
            elif due:
                st["stream_checked_at"] = now
                ok, err = check_stream(url, user, pw, st)
                st["stream_ok"] = ok
                st["stream_note"] = None
                if not ok:
                    st["stream_err"] = err
            if not full and st.get("stream_ok") is False:
                problems["stream"] = f"📺 {name}: تسجيل الدخول يعمل لكن البث لا ({st.get('stream_err')})"
            exp = info.get("exp")
            if exp and str(exp).isdigit():
                left = int(exp) - now
                if left < 3 * 86400:
                    day = time.strftime("%d.%m.%Y", time.localtime(int(exp)))
                    problems["expiry"] = f"⏳ {name}: الاشتراك ينتهي خلال {dur(max(left, 60))} ({day})"
            st["last_ok_url"] = url
            st["last_ms"] = info.get("ms")
            st["exp"] = exp
            st["cons"] = f"{info.get('active')}/{info.get('max')}"

        # منطق التنبيه: فشلان متتاليان قبل الإرسال، ورسالة عند الزوال
        fails = st.setdefault("fails", {})
        alerted = st.setdefault("alerted", {})
        for key, msg in problems.items():
            fails[key] = fails.get(key, 0) + 1
            need = 1 if key == "expiry" else FAILS_BEFORE_ALERT
            if fails[key] >= need and key not in alerted:
                if mode == "cron" and telegram(conf, msg):
                    alerted[key] = now
        for key in list(alerted):
            if key not in problems:
                since = alerted.pop(key)
                if key != "expiry" and mode == "cron":
                    telegram(conf, f"✅ {name} يعمل من جديد ({key}) — توقف تقريباً {dur(now - since)}")
        for key in list(fails):
            if key not in problems:
                fails.pop(key)

        line = f"{'✅' if not problems else '⚠️'} {name}"
        if working:
            line += f" — {working[0][0].split('//')[-1]} ({working[0][2].get('ms')}ms)"
            if st.get("stream_note"):
                line += f"\n   البث: {st['stream_note']}"
            elif st.get("stream_name"):
                line += f"\n   البث: {'يعمل' if 'stream' not in problems else 'لا يعمل'} ({st.get('stream_name')})"
            if st.get("exp") and str(st["exp"]).isdigit():
                line += f"\n   ينتهي: {time.strftime('%d.%m.%Y', time.localtime(int(st['exp'])))}"
            line += f"\n   الاتصالات: {st.get('cons')}"
        for msg in problems.values():
            line += "\n   " + msg.replace("\n", " ")
        report.append(line)

    save(STATE, state)
    text = "📡 حالة سيرفرات LIBANON 4K\n\n" + "\n\n".join(report)
    if mode == "test":
        telegram(conf, text)
    if mode in ("test", "status"):
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1] if len(sys.argv) > 1 else "cron"))
