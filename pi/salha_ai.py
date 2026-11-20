#!/usr/bin/env python3
# Salha AI على الـ Pi: وسيط بين تطبيق الآيفون و Google Gemini.
# - يتكلم بنفس لغة Ollama (/api/tags و /api/chat)، فيعمل تطبيق Salha AI كما هو.
# - المفتاح محفوظ على الـ Pi فقط (/etc/salha-ai.json) — لا يخرج إلى الهاتف.
# - يضيف لكل محادثة: الوقت الآن، حالة الـ Pi، وعند السؤال عن الأخبار: آخر أخبار آخن.
# - Gemini يبحث في Google بنفسه عن المعلومات الحديثة.
# - كل طلب يحتاج رمز الدخول (Bearer) لأن المنفذ قد يكون مفتوحاً للإنترنت.
# - صفحة الإعداد /setup (من شبكة البيت فقط): تلصق فيها مفتاح Gemini من Safari
#   وتنسخ منها رمز الدخول للتطبيق — بدون لصق أي شيء في الطرفية.
import hmac, html, ipaddress, json, os, re, secrets, socket, sys, threading, time, urllib.error, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CONF = "/etc/salha-ai.json"
PORT = 8095
MODELS = ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-pro"]
NEWS_URL = "https://raw.githubusercontent.com/iphonesalha-web/aachen-news-ios/main/data/news.json"
API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent?alt=sse"

sys.path.insert(0, "/usr/local/bin")


_conf_lock = threading.Lock()


def conf():
    try:
        with open(CONF) as f:
            c = json.load(f)
    except (OSError, ValueError):
        c = {}
    if not c.get("token"):  # أول تشغيل: نولّد رمز دخول ونحفظه
        c["token"] = secrets.token_urlsafe(18)
        save_conf(c)
    return c


def save_conf(c):
    with _conf_lock:
        tmp = CONF + ".tmp"
        old = os.umask(0o077)
        try:
            with open(tmp, "w") as f:
                json.dump(c, f)
            os.chmod(tmp, 0o600)
            os.replace(tmp, CONF)
        finally:
            os.umask(old)


def is_local(addr):
    """هل الطلب من شبكة البيت؟ (صفحة الإعداد مسموحة من هنا فقط)"""
    try:
        ip = ipaddress.ip_address(addr.split("%")[0])
        if ip.version == 6 and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        return ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        return False


def test_key(key):
    """يرجع (True, رد) إن كان المفتاح يعمل، أو (False, سبب)."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODELS[0]}:generateContent"
    body = {"contents": [{"role": "user", "parts": [{"text": "قل: أهلاً نزار"}]}]}
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "x-goog-api-key": key})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            obj = json.loads(r.read())
        txt = "".join(p.get("text", "") for c in obj.get("candidates", [])
                      for p in (c.get("content") or {}).get("parts", []))
        return True, txt.strip() or "✓"
    except urllib.error.HTTPError as e:
        return False, api_error(e)
    except Exception as e:
        return False, f"لا يوجد اتصال بـ Google: {e}"


# ---------- السياق الإضافي ----------

def pi_status():
    try:
        import pi_health
        return pi_health.status_text()
    except Exception as e:
        return f"(تعذّرت قراءة حالة الـ Pi: {e})"


_news = {"at": 0, "text": ""}
_news_lock = threading.Lock()


def news_text():
    with _news_lock:
        if time.time() - _news["at"] < 1800 and _news["text"]:
            return _news["text"]
        try:
            with urllib.request.urlopen(NEWS_URL + f"?t={int(time.time())}", timeout=10) as r:
                data = json.loads(r.read())
            names = {"security": "أمن آخن", "community": "الجالية في آخن", "city": "مدينة آخن",
                     "germany": "ألمانيا", "papers": "صحف ألمانية", "lebanon": "لبنان", "tech": "تقنية"}
            lines = []
            for cat in names:
                items = [i for i in data.get("items", []) if i.get("cat") == cat][:6]
                for i in items:
                    lines.append(f"- [{names[cat]}] {i.get('title', '')} ({i.get('source', '')}, {str(i.get('time') or '')[:16]})")
            _news.update(at=time.time(), text="\n".join(lines))
        except Exception as e:
            _news.update(at=time.time() - 1500, text=f"(تعذّر تحميل الأخبار: {e})")
        return _news["text"]


NEWS_WORDS = re.compile(r"خبر|أخبار|اخبار|news|nachricht|heute|اليوم|حصل|صار|حوادث|شرطة|polizei", re.I)


def build_system(user_system, last_question):
    now = time.strftime("%A %d.%m.%Y %H:%M")
    parts = [
        user_system or "أنت مساعد مفيد.",
        f"التاريخ والوقت الآن عند المستخدم (ألمانيا): {now}.",
        "المستخدم نزار، يسكن في Alsdorf قرب آخن، ويملك محل SALHA AUTOTEILE لقطع غيار BMW.",
        "عندك أداة بحث Google — استعملها للأسئلة عن الأحداث والمعلومات الحديثة، ولا تخترع معلومات.",
        "إذا سُئلت عن الـ Raspberry Pi أو حرارته أو خدماته فاعتمد على هذه الحالة الحقيقية الآن:\n" + pi_status(),
    ]
    if NEWS_WORDS.search(last_question or ""):
        parts.append("آخر الأخبار التي جمعها تطبيق أخبار آخن (استعملها مع البحث):\n" + news_text())
    return "\n\n".join(parts)


# ---------- Gemini ----------

def gemini_stream(key, model, system, messages, search=True):
    contents = []
    for m in messages:
        role = "model" if m.get("role") == "assistant" else "user"
        text = m.get("content") or ""
        if not text:
            continue
        if contents and contents[-1]["role"] == role:  # Gemini يرفض دورين متتاليين لنفس الطرف
            contents[-1]["parts"][0]["text"] += "\n\n" + text
        else:
            contents.append({"role": role, "parts": [{"text": text}]})
    body = {"systemInstruction": {"parts": [{"text": system}]}, "contents": contents}
    if search:
        body["tools"] = [{"google_search": {}}]
    req = urllib.request.Request(API.format(model=model), data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "x-goog-api-key": key})
    with urllib.request.urlopen(req, timeout=120) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            try:
                obj = json.loads(line[5:].strip())
            except ValueError:
                continue
            for c in obj.get("candidates", []):
                for p in (c.get("content") or {}).get("parts", []):
                    if p.get("text") and not p.get("thought"):
                        yield p["text"]


def api_error(e):
    try:
        msg = json.loads(e.read()).get("error", {}).get("message", "")
    except Exception:
        msg = ""
    if e.code == 429:
        return "تم تجاوز الحد المجاني لـ Gemini مؤقتاً — جرّب بعد قليل أو اختر gemini-2.5-flash-lite."
    if e.code in (400, 403) and "API key" in msg:
        return "مفتاح Gemini غير صالح — أعد إعداد المفتاح على الـ Pi."
    return f"خطأ من Gemini (HTTP {e.code}) {msg[:200]}"


# ---------- صفحة الإعداد ----------

SETUP_HTML = """<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Salha AI — الإعداد</title>
<style>
body{font-family:-apple-system,system-ui,sans-serif;background:#f4f2fb;color:#1d1630;margin:0;padding:24px 16px}
@media(prefers-color-scheme:dark){body{background:#120d1f;color:#eee}.card{background:#1e1631!important}input{background:#120d1f;color:#eee}}
.card{background:#fff;border-radius:18px;padding:20px;max-width:520px;margin:0 auto 16px;box-shadow:0 2px 10px rgba(0,0,0,.06)}
h1{font-size:24px;margin:0 0 4px}h2{font-size:17px;margin:0 0 10px}.muted{color:#888;font-size:14px;line-height:1.6}
input{width:100%;box-sizing:border-box;font-size:16px;padding:12px;border:1px solid #ccc;border-radius:12px;direction:ltr}
button{width:100%;margin-top:12px;font-size:17px;font-weight:700;padding:13px;border:0;border-radius:12px;background:#7c3aed;color:#fff}
.ok{color:#16a34a;font-weight:700}.bad{color:#dc2626;font-weight:700}
code{display:block;direction:ltr;text-align:left;background:#00000010;padding:12px;border-radius:10px;font-size:15px;word-break:break-all}
</style></head><body>
<div class="card"><h1>✨ Salha AI</h1><div class="muted">إعداد الذكاء الاصطناعي على الـ Raspberry Pi</div></div>
{msg}
<div class="card"><h2>① مفتاح Gemini</h2>
<div class="muted">الحالة: {keystate}<br>خذ المفتاح من <b>aistudio.google.com</b> ← Get API key، ثم الصقه هنا.</div>
<form method="post" action="/setup"><input name="key" type="password" placeholder="AIza…" autocomplete="off" autocapitalize="off" spellcheck="false">
<button type="submit">حفظ واختبار المفتاح</button></form></div>
<div class="card"><h2>② رمز الدخول للتطبيق</h2>
<div class="muted">انسخه والصقه في تطبيق Salha AI ← الإعدادات ← رمز الدخول.</div>
<code id="t">{token}</code><button type="button" onclick="navigator.clipboard.writeText(document.getElementById('t').innerText);this.innerText='✅ تم النسخ'">نسخ الرمز</button></div>
<div class="card"><h2>③ عناوين التطبيق</h2><div class="muted">في البيت:</div><code>{home}</code>
<div class="muted" style="margin-top:10px">من خارج البيت (يحتاج فتح المنفذ {port} في الـ FRITZ!Box):</div><code>{remote}</code></div>
</body></html>"""


def lan_addresses():
    v4 = v6 = ""
    try:
        out = os.popen("hostname -I").read().split()
        v4 = next((a for a in out if "." in a), "")
        v6 = next((a for a in out if ":" in a and not a.lower().startswith(("fd", "fe80"))), "")
    except Exception:
        pass
    return (f"http://{v4}:{PORT}" if v4 else "—"), (f"http://[{v6}]:{PORT}" if v6 else "—")


# ---------- خادم بلغة Ollama ----------

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def _authed(self):
        c = conf()
        token = c.get("token", "")
        got = (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
        if token and hmac.compare_digest(got, token):
            return c
        self._json(401, {"error": "رمز الدخول غير صحيح — ضعه في إعدادات التطبيق"})
        return None

    def _json(self, code, obj):
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _setup_page(self, msg=""):
        c = conf()
        home, remote = lan_addresses()
        keystate = ('<span class="ok">✅ مضبوط</span>' if c.get("key")
                    else '<span class="bad">❌ غير مضبوط بعد</span>')
        page = (SETUP_HTML.replace("{msg}", msg).replace("{keystate}", keystate)
                .replace("{token}", html.escape(c["token"])).replace("{home}", home)
                .replace("{remote}", remote).replace("{port}", str(PORT)))
        data = page.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/setup"):
            if not is_local(self.client_address[0]):
                return self._json(403, {"error": "صفحة الإعداد من شبكة البيت فقط"})
            return self._setup_page()
        if self.path.startswith("/api/version"):
            return self._json(200, {"version": "salha-ai-gemini"})
        if self.path.startswith("/api/tags"):
            if not self._authed():
                return
            return self._json(200, {"models": [{"name": m} for m in MODELS]})
        self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path.startswith("/setup"):
            if not is_local(self.client_address[0]):
                return self._json(403, {"error": "صفحة الإعداد من شبكة البيت فقط"})
            n = int(self.headers.get("Content-Length") or 0)
            form = urllib.parse.parse_qs(self.rfile.read(min(n, 4096)).decode("utf-8", "replace"))
            raw = (form.get("key") or [""])[0]
            raw = re.sub(r"\x1b\[[0-9;]*[~A-Za-z]|\[20[01]~", "", raw)  # رموز لصق من بعض التطبيقات
            m = re.search(r"AIza[0-9A-Za-z_-]{35}", raw)
            key = m.group(0) if m else re.sub(r"[^0-9A-Za-z_-]", "", raw)
            if "AIza" in key:
                key = key[key.index("AIza"):]
            if len(key) < 30:
                return self._setup_page('<div class="card bad">❌ المفتاح غير كامل — انسخه مرة ثانية</div>')
            ok, info = test_key(key)
            if not ok:
                return self._setup_page(f'<div class="card bad">❌ {html.escape(info)}</div>')
            c = conf()
            c["key"] = key
            save_conf(c)
            return self._setup_page(f'<div class="card ok">✅ تم حفظ المفتاح — رد Gemini: «{html.escape(info[:120])}»</div>')
        if not self.path.startswith("/api/chat"):
            return self._json(404, {"error": "not found"})
        c = self._authed()
        if not c:
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            req = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._json(400, {"error": "طلب غير صالح"})
        msgs = req.get("messages") or []
        user_system = "\n".join(m.get("content", "") for m in msgs if m.get("role") == "system")
        chat = [m for m in msgs if m.get("role") in ("user", "assistant")]
        last_q = next((m.get("content", "") for m in reversed(chat) if m.get("role") == "user"), "")
        model = req.get("model") if req.get("model") in MODELS else MODELS[0]
        if not c.get("key"):
            return self._json(503, {"error": "مفتاح Gemini غير مضبوط — افتح صفحة الإعداد على الـ Pi"})
        system = build_system(user_system, last_q)

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Connection", "close")
        self.end_headers()

        def chunk(obj):
            data = (json.dumps(obj, ensure_ascii=False) + "\n").encode()
            self.wfile.write(b"%X\r\n%s\r\n" % (len(data), data))
            self.wfile.flush()

        try:
            got = False
            for search in (True, False):  # إن فشل البحث (حصة) نعيد بدونه
                try:
                    for t in gemini_stream(c["key"], model, system, chat, search=search):
                        got = True
                        chunk({"model": model, "message": {"role": "assistant", "content": t}, "done": False})
                    break
                except urllib.error.HTTPError as e:
                    if search and not got and e.code in (400, 429):
                        continue
                    raise
            chunk({"model": model, "message": {"role": "assistant", "content": ""}, "done": True})
        except urllib.error.HTTPError as e:
            chunk({"error": api_error(e)})
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as e:
            try:
                chunk({"error": f"خطأ: {e}"})
            except Exception:
                return
        try:
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except Exception:
            pass
        self.close_connection = True


class DualStackServer(ThreadingHTTPServer):
    address_family = socket.AF_INET6
    daemon_threads = True

    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)  # IPv4 + IPv6 معاً
        super().server_bind()


def main():
    if "test" in sys.argv:
        c = conf()
        if not c.get("key"):
            print("لا يوجد مفتاح بعد")
            return
        q = "ما عاصمة ولاية شمال الراين-وستفاليا؟ أجب بكلمة."
        print("سؤال:", q)
        print("رد:", "".join(gemini_stream(c["key"], MODELS[0], build_system("", q),
                                         [{"role": "user", "content": q}], search=False)))
        return
    conf()  # يولّد رمز الدخول عند أول تشغيل
    try:
        srv = DualStackServer(("::", PORT), Handler)
    except OSError:  # لا IPv6 — IPv4 فقط
        ThreadingHTTPServer.daemon_threads = True
        srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Salha AI on :{PORT}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
