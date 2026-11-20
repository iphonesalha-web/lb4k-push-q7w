#!/usr/bin/env python3
# Salha AI على الـ Pi: وسيط بين تطبيق الآيفون و Google Gemini.
# - يتكلم بنفس لغة Ollama (/api/tags و /api/chat)، فيعمل تطبيق Salha AI كما هو.
# - المفتاح محفوظ على الـ Pi فقط (/etc/salha-ai.json) — لا يخرج إلى الهاتف.
# - يضيف لكل محادثة: الوقت الآن، حالة الـ Pi، وعند السؤال عن الأخبار: آخر أخبار آخن.
# - Gemini يبحث في Google بنفسه عن المعلومات الحديثة.
# - OpenRouter (DeepSeek :free) يُستعمل أولاً إن وُضع مفتاحه، وGemini احتياط.
# - كل طلب يحتاج رمز الدخول (Bearer) لأن المنفذ قد يكون مفتوحاً للإنترنت.
# - صفحة الإعداد /setup (من شبكة البيت فقط): تلصق فيها مفتاح Gemini من Safari
#   وتنسخ منها رمز الدخول للتطبيق — بدون لصق أي شيء في الطرفية.
import hmac, html, ipaddress, json, os, re, secrets, socket, sys, threading, time, urllib.error, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CONF = "/etc/salha-ai.json"
PORT = 8095
DEFAULT_MODEL = "gemini-3.8-flash"   # Google يغيّر أسماء النماذج — القائمة الحقيقية تُقرأ من حسابك
_models = {"at": 0, "list": []}


def model_key(name):
    """ترتيب: الأحدث أولاً، و flash قبل pro قبل lite."""
    nums = [int(x) for x in re.findall(r"\d+", name)[:2]] + [0, 0]
    kind = 0 if name.endswith("-flash") else (1 if "pro" in name else 2)
    return (-nums[0], -nums[1], kind, name)


def available_models(key, prefer="conf"):
    """النماذج المتاحة لمفتاحك فعلاً (تُحدَّث كل 12 ساعة)، والنموذج الذي نجح أولاً."""
    names = _list_models(key)
    if prefer == "conf":
        try:
            with open(CONF) as f:
                good = json.load(f).get("model")
        except (OSError, ValueError):
            good = None
        if good and good in names:
            names = [good] + [n for n in names if n != good]
    return names


def _list_models(key):
    if key and time.time() - _models["at"] < 12 * 3600 and _models["list"]:
        return list(_models["list"])
    names = []
    if key:
        try:
            req = urllib.request.Request("https://generativelanguage.googleapis.com/v1beta/models?pageSize=200",
                                         headers={"x-goog-api-key": key})
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read())
            for m in data.get("models", []):
                n = m.get("name", "").removeprefix("models/")
                if ("generateContent" in m.get("supportedGenerationMethods", [])
                        and n.startswith("gemini-")
                        and not re.search(r"embed|tts|image|live|audio|vision|exp|preview|latest|\d{3,}", n)):
                    names.append(n)
        except Exception as e:
            print("models list failed:", e, file=sys.stderr, flush=True)
    names = sorted(set(names), key=model_key)[:10]
    if not names:
        names = [DEFAULT_MODEL]
    _models.update(at=time.time() if len(names) > 1 or key else 0, list=names)
    return list(names)


MODELS = [DEFAULT_MODEL]  # للتوافق — الاستعمال الفعلي عبر available_models()
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


def _try_model(key, model):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {"contents": [{"role": "user", "parts": [{"text": "قل: أهلاً نزار"}]}]}
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "x-goog-api-key": key})
    with urllib.request.urlopen(req, timeout=60) as r:
        obj = json.loads(r.read())
    txt = "".join(p.get("text", "") for c in obj.get("candidates", [])
                  for p in (c.get("content") or {}).get("parts", []))
    return txt.strip() or "✓"


def test_key(key):
    """يجرّب كل النماذج المتاحة بالترتيب.
    يرجع (True, رد, النموذج) لأول نموذج يعمل، أو ("busy", رسالة, النموذج) إذا كان نموذج مجاني
    متاحاً لكنه مزدحم الآن (503) — المفتاح صحيح ويُحفظ، أو (False, الأخطاء, None)."""
    _models["at"] = 0  # مفتاح جديد = قائمة جديدة
    errors, busy = [], None
    for model in available_models(key, prefer=None):
        for attempt in range(2):
            try:
                return True, _try_model(key, model), model
            except urllib.error.HTTPError as e:
                info = api_error(e)
                if e.code in (400, 403) and "API key" in info:
                    return False, info, None      # المفتاح نفسه مرفوض
                if e.code >= 500 and attempt == 0:
                    time.sleep(2)
                    continue
                if e.code >= 500 and not busy:
                    busy = model
                errors.append(f"{model}: {info}")
                break
            except (TimeoutError, socket.timeout) as e:   # بطيء/مزدحم: المفتاح سليم، نجرّب التالي
                if not busy:
                    busy = model
                errors.append(f"{model}: بطيء جداً الآن (timeout)")
                break
            except urllib.error.URLError as e:
                if isinstance(e.reason, (TimeoutError, socket.timeout)):
                    if not busy:
                        busy = model
                    errors.append(f"{model}: بطيء جداً الآن (timeout)")
                    break
                return False, f"لا يوجد اتصال بـ Google: {e}", None
            except Exception as e:
                return False, f"لا يوجد اتصال بـ Google: {e}", None
    if busy:
        return "busy", " | ".join(errors[:5]), busy
    return False, " | ".join(errors[:5]) or "لا توجد نماذج متاحة", None


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


def build_system(user_system, last_question, search=True):
    now = time.strftime("%A %d.%m.%Y %H:%M")
    parts = [
        user_system or "أنت مساعد مفيد.",
        f"التاريخ والوقت الآن عند المستخدم (ألمانيا): {now}.",
        "المستخدم نزار، يسكن في Alsdorf قرب آخن، ويملك محل SALHA AUTOTEILE لقطع غيار BMW.",
        ("عندك أداة بحث Google — استعملها للأسئلة عن الأحداث والمعلومات الحديثة، ولا تخترع معلومات." if search
         else "ليس عندك بحث في الإنترنت: إذا لم تكن متأكداً من معلومة حديثة فقل ذلك بصراحة ولا تخترع."),
        "أجب بالعربية دائماً إلا إذا كتب المستخدم بلغة أخرى.",
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


# ---------- OpenRouter (DeepSeek المجاني) ----------

OR_BASE = "https://openrouter.ai/api/v1"
OR_DEFAULT = "openrouter/free"   # موجّه OpenRouter: يختار نموذجاً مجانياً متاحاً
_or_models = {"at": 0, "list": []}


OR_PREFER = ["deepseek", "gemma-4", "ernie", "nemotron-3-ultra", "ling", "qwen", "llama", "mistral", "dots"]
OR_SKIP = re.compile(r"decider|rerank|recraft|embed|guard|\bvl\b|-vl|omni|nano|lightning|lfm|flint|particle|coder|\b\d{1,2}b\b(?!-a)|1\.7b", re.I)


def _or_rank(mid):
    pos = next((i for i, k in enumerate(OR_PREFER) if k in mid), len(OR_PREFER))
    return (pos, mid)


def or_models(prefer=None):
    """النماذج المجانية (:free) على OpenRouter — DeepSeek أولاً، ثم غيرها كاحتياط."""
    if not (time.time() - _or_models["at"] < 6 * 3600 and _or_models["list"]):
        ids = []
        try:
            with urllib.request.urlopen(OR_BASE + "/models", timeout=15) as r:
                data = json.loads(r.read())
            for m in data.get("data", []):
                mid = m.get("id", "")
                if mid.endswith(":free") and not OR_SKIP.search(mid) and "text" in str((m.get("architecture") or {}).get("output_modalities", ["text"])):
                    ids.append(mid)
        except Exception as e:
            print("openrouter models failed:", e, file=sys.stderr, flush=True)
        ids = sorted(set(ids), key=_or_rank)[:7] + [OR_DEFAULT]
        _or_models.update(at=time.time() if len(ids) > 1 else 0, list=list(dict.fromkeys(ids)))
    names = list(_or_models["list"])
    if prefer and prefer in names:
        names = [prefer] + [n for n in names if n != prefer]
    return names


def _or_req(key, body, timeout):
    return urllib.request.urlopen(urllib.request.Request(
        OR_BASE + "/chat/completions", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key,
                 "HTTP-Referer": "https://github.com/iphonesalha-web", "X-Title": "Salha AI"}), timeout=timeout)


def or_stream(key, model, system, messages):
    msgs = [{"role": "system", "content": system}] + [
        {"role": m["role"], "content": m.get("content") or ""} for m in messages if m.get("content")]
    with _or_req(key, {"model": model, "messages": msgs, "stream": True}, 180) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:") or line == "data: [DONE]":
                continue
            try:
                obj = json.loads(line[5:].strip())
            except ValueError:
                continue
            if obj.get("error"):
                raise RuntimeError("OpenRouter: " + str(obj["error"].get("message", obj["error"]))[:200])
            for ch in obj.get("choices", []):
                t = (ch.get("delta") or {}).get("content")
                if t:
                    yield t


def or_error(e):
    try:
        msg = json.loads(e.read()).get("error", {}).get("message", "")
    except Exception:
        msg = ""
    if e.code == 401:
        return "OpenRouter رفض المفتاح (401) — انسخه مرة ثانية"
    if e.code == 429:
        return f"حد OpenRouter المجاني (429): {msg[:160]}"
    return f"خطأ من OpenRouter (HTTP {e.code}) {msg[:200]}"


def test_or_key(key):
    try:
        with urllib.request.urlopen(urllib.request.Request(
                OR_BASE + "/key", headers={"Authorization": "Bearer " + key}), timeout=20) as r:
            r.read()
    except urllib.error.HTTPError as e:
        return False, or_error(e), None
    except Exception as e:
        return False, f"لا يوجد اتصال بـ OpenRouter: {e}", None
    _or_models["at"] = 0
    errors = []
    for model in or_models()[:5]:
        try:
            with _or_req(key, {"model": model, "max_tokens": 400,
                               "messages": [{"role": "user", "content": "قل: أهلاً نزار"}]}, 60) as r:
                obj = json.loads(r.read())
            if obj.get("error"):
                errors.append(f"{model}: {str(obj['error'].get('message'))[:120]}")
                continue
            txt = "".join((c.get("message") or {}).get("content") or "" for c in obj.get("choices", []))
            return True, txt.strip() or "✓", model
        except urllib.error.HTTPError as e:
            if e.code == 401:
                return False, or_error(e), None
            errors.append(f"{model}: {or_error(e)}")
        except Exception as e:
            errors.append(f"{model}: {e}")
    # المفتاح صحيح لكن كل النماذج مشغولة الآن — نحفظه ونجرب وقت المحادثة
    return "busy", " | ".join(errors[:4]), or_models()[0]


def api_error(e):
    try:
        msg = json.loads(e.read()).get("error", {}).get("message", "")
    except Exception:
        msg = ""
    if e.code == 429:
        return f"الحد المجاني لهذا النموذج انتهى أو غير متاح (429): {msg[:160]}"
    if e.code in (400, 403) and "API key" in msg:
        return f"Google رفض المفتاح (HTTP {e.code}): {msg[:200]}"
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
<form method="post" action="{action}"><input name="key" type="password" placeholder="AIza…" autocomplete="off" autocapitalize="off" spellcheck="false">
<button type="submit">حفظ واختبار المفتاح</button></form></div>
<div class="card"><h2>①ب مفتاح OpenRouter — DeepSeek مجاني</h2>
<div class="muted">الحالة: {orstate}<br>افتح <b>openrouter.ai</b> ← سجّل دخول ← <b>Keys</b> ← <b>Create Key</b>، ثم الصقه هنا (يبدأ بـ sk-or-). يُستعمل أولاً، وGemini احتياط.</div>
<form method="post" action="{action}"><input type="hidden" name="action" value="orkey"><input name="key" type="password" placeholder="sk-or-…" autocomplete="off" autocapitalize="off" spellcheck="false">
<button type="submit">حفظ واختبار مفتاح OpenRouter</button></form></div>
<div class="card"><h2>② رمز الدخول للتطبيق</h2>
<div class="muted">انسخه والصقه في تطبيق Salha AI ← الإعدادات ← رمز الدخول.</div>
<code id="t">{token}</code><button type="button" onclick="navigator.clipboard.writeText(document.getElementById('t').innerText);this.innerText='✅ تم النسخ'">نسخ الرمز</button>
<form method="post" action="{action}"><input type="hidden" name="action" value="newtoken">
<button type="submit" style="background:#6b7280">🔄 إنشاء رمز جديد</button></form></div>
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

    def _setup_allowed(self):
        """من شبكة البيت دائماً؛ ومن الخارج فقط مع الرمز في الرابط (?t=…)."""
        if is_local(self.client_address[0]):
            return True
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        got = (q.get("t") or [""])[0]
        return bool(got) and hmac.compare_digest(got, conf()["token"])

    def _setup_page(self, msg=""):
        c = conf()
        home, remote = lan_addresses()
        keystate = ('<span class="ok">✅ مضبوط</span>' if c.get("key")
                    else '<span class="bad">❌ غير مضبوط بعد</span>')
        orstate = (f'<span class="ok">✅ مضبوط — {html.escape(c.get("or_model") or "")}</span>' if c.get("or_key")
                   else '<span class="bad">❌ غير مضبوط بعد</span>')
        page = (SETUP_HTML.replace("{msg}", msg).replace("{keystate}", keystate).replace("{orstate}", orstate)
                .replace("{token}", html.escape(c["token"])).replace("{home}", home)
                .replace("{remote}", remote).replace("{port}", str(PORT))
                .replace("{action}", html.escape(self.path if self.path.startswith("/setup") else "/setup")))
        data = page.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/setup"):
            if not self._setup_allowed():
                return self._json(403, {"error": "صفحة الإعداد: من شبكة البيت، أو من الخارج بالرابط الكامل مع الرمز"})
            return self._setup_page()
        if self.path.startswith("/api/version"):
            return self._json(200, {"version": "salha-ai-gemini"})
        if self.path.startswith("/api/tags"):
            if not self._authed():
                return
            c = conf()
            names = (or_models(c.get("or_model"))[:5] if c.get("or_key") else []) + \
                    (available_models(c.get("key"))[:5] if c.get("key") else [])
            return self._json(200, {"models": [{"name": m} for m in names or [DEFAULT_MODEL]]})
        self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path.startswith("/setup"):
            if not self._setup_allowed():
                return self._json(403, {"error": "صفحة الإعداد: من شبكة البيت، أو من الخارج بالرابط الكامل مع الرمز"})
            n = int(self.headers.get("Content-Length") or 0)
            form = urllib.parse.parse_qs(self.rfile.read(min(n, 4096)).decode("utf-8", "replace"))
            if (form.get("action") or [""])[0] == "newtoken":
                c = conf()
                c["token"] = secrets.token_urlsafe(18)
                save_conf(c)
                self.path = "/setup" if is_local(self.client_address[0]) else f"/setup?t={c['token']}"
                return self._setup_page('<div class="card ok">✅ رمز دخول جديد — انسخه للتطبيق (الرمز القديم لم يعد يعمل)</div>')
            raw = (form.get("key") or [""])[0]
            raw = re.sub(r"\x1b\[[0-9;]*[~A-Za-z]|\[20[01]~", "", raw)  # رموز لصق من بعض التطبيقات
            if (form.get("action") or [""])[0] == "orkey":
                m = re.search(r"sk-or-[0-9A-Za-z_-]{20,}", raw)
                key = m.group(0) if m else re.sub(r"\s+", "", raw)
                if len(key) < 20:
                    return self._setup_page('<div class="card bad">❌ المفتاح غير كامل — انسخه مرة ثانية</div>')
                ok, info, model = test_or_key(key)
                if not ok:
                    return self._setup_page(f'<div class="card bad">❌ {html.escape(info)}</div>')
                c = conf()
                c["or_key"], c["or_model"] = key, model
                save_conf(c)
                note = (f"ردّ: «{html.escape(info[:120])}»" if ok is True
                        else f"المفتاح صحيح، النماذج مشغولة الآن — جرّب المحادثة بعد قليل.<br><span class='muted'>{html.escape(info[:300])}</span>")
                return self._setup_page(f'<div class="card ok">✅ تم حفظ مفتاح OpenRouter — {html.escape(model)} {note}</div>')
            m = re.search(r"AIza[0-9A-Za-z_-]{35}", raw)
            # شكل غير معروف؟ نحذف المسافات والأسطر فقط ولا نلمس باقي الرموز
            key = m.group(0) if m else re.sub(r"\s+", "", raw)
            if len(key) < 20:
                return self._setup_page('<div class="card bad">❌ المفتاح غير كامل — انسخه مرة ثانية</div>')
            ok, info, good_model = test_key(key)
            if ok == "busy":
                c = conf()
                c["key"] = key
                c["model"] = good_model
                save_conf(c)
                return self._setup_page(
                    f'<div class="card ok">✅ المفتاح صحيح وتم حفظه — النموذج المجاني {html.escape(good_model)} '
                    f'مزدحم عند Google الآن (503)، جرّب المحادثة بعد دقائق.<br><span class="muted">{html.escape(info[:400])}</span></div>')
            if not ok:
                hint = (f"<br><span class='muted'>المفتاح الذي وصل: يبدأ بـ «{html.escape(key[:6])}…» "
                        f"وينتهي بـ «…{html.escape(key[-4:])}»، طوله {len(key)} حرفاً.</span>")
                return self._setup_page(f'<div class="card bad">❌ {html.escape(info)}{hint}</div>')
            c = conf()
            c["key"] = key
            c["model"] = good_model
            save_conf(c)
            return self._setup_page(f'<div class="card ok">✅ تم حفظ المفتاح — النموذج {html.escape(good_model)} ردّ: «{html.escape(info[:120])}»</div>')
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
        if not c.get("key") and not c.get("or_key"):
            return self._json(503, {"error": "لا يوجد مفتاح — افتح صفحة الإعداد على الـ Pi"})
        # المرشحون بالترتيب: OpenRouter (DeepSeek) أولاً إن وُجد، ثم Gemini
        cands = []
        if c.get("or_key"):
            cands += [("or", m) for m in or_models(c.get("or_model"))[:4]] + [("or", OR_DEFAULT)]
        if c.get("key"):
            cands += [("gm", m) for m in available_models(c.get("key"))[:5]]
        want = req.get("model")
        if not c.get("or_key") or "/" in str(want):   # اختيار التطبيق يُحترم ضمن نفس المزوّد الأول
            cands.sort(key=lambda x: 0 if x[1] == want else 1)

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Connection", "close")
        self.end_headers()

        def chunk(obj):
            data = (json.dumps(obj, ensure_ascii=False) + "\n").encode()
            self.wfile.write(b"%X\r\n%s\r\n" % (len(data), data))
            self.wfile.flush()

        def attempts(prov, mdl):
            if prov == "or":
                yield lambda: or_stream(c["or_key"], mdl, build_system(user_system, last_q, search=False), chat)
            else:
                for search in (True, False):
                    yield lambda search=search: gemini_stream(c["key"], mdl, build_system(user_system, last_q, search),
                                                              chat, search=search)

        got, errors, used = False, [], None
        try:
            for prov, mdl in cands:
                for run in attempts(prov, mdl):
                    try:
                        for t in run():
                            got = True
                            chunk({"model": mdl, "message": {"role": "assistant", "content": t}, "done": False})
                        used = (prov, mdl)
                        break
                    except urllib.error.HTTPError as e:
                        if got:
                            raise
                        errors.append(f"{mdl}: " + (or_error(e) if prov == "or" else api_error(e)))
                        if prov == "or" and e.code == 401:
                            break
                    except (BrokenPipeError, ConnectionResetError):
                        raise
                    except Exception as e:
                        if got:
                            raise
                        errors.append(f"{mdl}: {e}")
                if used:
                    break
            if used:
                if used[0] == "or" and c.get("or_model") != used[1]:
                    c2 = conf(); c2["or_model"] = used[1]; save_conf(c2)
                chunk({"model": used[1], "message": {"role": "assistant", "content": ""}, "done": True})
            else:
                chunk({"error": "كل النماذج مشغولة الآن، جرّب بعد دقيقة.\n" + "\n".join(errors[:4])})
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
        print("رد:", "".join(gemini_stream(c["key"], available_models(c["key"])[0], build_system("", q),
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
