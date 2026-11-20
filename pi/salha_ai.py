#!/usr/bin/env python3
# Salha AI على الـ Pi: وسيط بين تطبيق الآيفون و Google Gemini.
# - يتكلم بنفس لغة Ollama (/api/tags و /api/chat)، فيعمل تطبيق Salha AI كما هو.
# - المفتاح محفوظ على الـ Pi فقط (/etc/salha-ai.json) — لا يخرج إلى الهاتف.
# - يضيف لكل محادثة: الوقت الآن، حالة الـ Pi، وعند السؤال عن الأخبار: آخر أخبار آخن.
# - Gemini يبحث في Google بنفسه عن المعلومات الحديثة.
# - كل طلب يحتاج رمز الدخول (Bearer) لأن المنفذ قد يكون مفتوحاً للإنترنت.
import hmac, json, os, re, socket, sys, threading, time, urllib.error, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CONF = "/etc/salha-ai.json"
PORT = 8095
MODELS = ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-pro"]
NEWS_URL = "https://raw.githubusercontent.com/iphonesalha-web/aachen-news-ios/main/data/news.json"
API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent?alt=sse"

sys.path.insert(0, "/usr/local/bin")


def conf():
    with open(CONF) as f:
        return json.load(f)


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

    def do_GET(self):
        if self.path.startswith("/api/version"):
            return self._json(200, {"version": "salha-ai-gemini"})
        if self.path.startswith("/api/tags"):
            if not self._authed():
                return
            return self._json(200, {"models": [{"name": m} for m in MODELS]})
        self._json(404, {"error": "not found"})

    def do_POST(self):
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
        q = "ما عاصمة ولاية شمال الراين-وستفاليا؟ أجب بكلمة."
        print("سؤال:", q)
        print("رد:", "".join(gemini_stream(c["key"], MODELS[0], build_system("", q),
                                         [{"role": "user", "content": q}], search=False)))
        return
    conf()  # يفشل مبكراً إن لم يكن الإعداد موجوداً
    try:
        srv = DualStackServer(("::", PORT), Handler)
    except OSError:  # لا IPv6 — IPv4 فقط
        ThreadingHTTPServer.daemon_threads = True
        srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Salha AI on :{PORT}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
