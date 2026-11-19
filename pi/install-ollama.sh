#!/bin/bash
# ذكاء اصطناعي محلي على الـ Pi 5: Ollama + نموذج يفهم العربية + واجهة محادثة (Open WebUI)
# تُفتح من Safari على الآيفون. كل شيء يعمل داخل البيت، بلا اشتراك ولا إرسال بيانات للخارج.
#   curl -fsSL https://raw.githubusercontent.com/iphonesalha-web/lb4k-push-q7w/main/pi/install-ollama.sh -o /tmp/ai.sh && sudo bash /tmp/ai.sh
# تشغيله مرة ثانية = تحديث Ollama والواجهة.
set -e
[ "$(id -u)" = 0 ] || { echo "شغّله مع sudo"; exit 1; }
[ "$(uname -m)" = aarch64 ] || { echo "❌ يحتاج نظام 64-bit (aarch64)"; exit 1; }

MODEL="${MODEL:-qwen2.5:3b}"     # يفهم العربية جيداً وسريع نسبياً على الـ Pi 5
PORT=3001
NAME=open-webui
IMAGE=ghcr.io/open-webui/open-webui:main

mem_gb=$(awk '/MemTotal/ {printf "%d", $2/1024/1024}' /proc/meminfo)
[ "$mem_gb" -ge 6 ] || echo "⚠️ الذاكرة ${mem_gb}GB — قد يكون بطيئاً"

echo "① تثبيت/تحديث Ollama…"
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq zstd curl >/dev/null
curl -fsSL https://ollama.com/install.sh | sh >/tmp/ollama-install.log 2>&1 || { tail -20 /tmp/ollama-install.log; exit 1; }
# متاح لكل أجهزة البيت (تطبيق الآيفون) وليس فقط من داخل الـ Pi
mkdir -p /etc/systemd/system/ollama.service.d
printf '[Service]\nEnvironment="OLLAMA_HOST=0.0.0.0:11434"\nEnvironment="OLLAMA_KEEP_ALIVE=30m"\n' > /etc/systemd/system/ollama.service.d/lan.conf
systemctl daemon-reload
systemctl enable ollama >/dev/null 2>&1
systemctl restart ollama
for i in $(seq 1 30); do curl -fsS http://127.0.0.1:11434/api/version >/dev/null 2>&1 && break; sleep 1; done
echo "   $(ollama --version 2>/dev/null | tail -1)"

echo "② تنزيل النموذج $MODEL (حوالي 2GB — قد يأخذ دقائق)…"
ollama pull "$MODEL"

echo "③ تجربة سريعة…"
START=$(date +%s)
ANS=$(timeout 180 ollama run "$MODEL" "أجب بجملة واحدة قصيرة بالعربية: ما عاصمة لبنان؟" 2>/dev/null || true)
echo "   الرد: $ANS"
echo "   (استغرق $(( $(date +%s) - START )) ثانية، أول مرة تكون أبطأ لأن النموذج يُحمَّل للذاكرة)"

echo "④ واجهة المحادثة (Open WebUI)…"
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker >/dev/null 2>&1 || true
if ss -ltn "( sport = :$PORT )" | grep -q LISTEN && ! docker ps --format '{{.Names}}' | grep -qx "$NAME"; then
  echo "❌ المنفذ $PORT مستعمل من برنامج آخر"; exit 1
fi
docker pull -q "$IMAGE" >/dev/null
docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" --restart unless-stopped --network host \
  -e PORT="$PORT" \
  -e OLLAMA_BASE_URL=http://127.0.0.1:11434 \
  -e WEBUI_NAME="Salha AI" \
  -e DEFAULT_MODELS="$MODEL" \
  -e ENABLE_OPENAI_API=false \
  -e SCARF_NO_ANALYTICS=true -e DO_NOT_TRACK=true -e ANONYMIZED_TELEMETRY=false \
  -v open-webui:/app/backend/data \
  "$IMAGE" >/dev/null
docker image prune -f >/dev/null 2>&1 || true

echo "   انتظار بدء الواجهة (أول مرة حتى دقيقتين)…"
for i in $(seq 1 60); do curl -fsS -o /dev/null "http://127.0.0.1:$PORT" 2>/dev/null && break; sleep 3; done

IP=$(hostname -I | awk '{print $1}')
echo
if curl -fsS -o /dev/null "http://127.0.0.1:$PORT"; then
  echo "✅ الذكاء الاصطناعي جاهز"
else
  echo "⏳ الواجهة ما زالت تبدأ — انتظر دقيقة ثم افتح الرابط"
fi
echo "   افتح على الآيفون: http://$IP:$PORT"
echo "   أول حساب تنشئه يصبح حساب المدير"
echo "   النموذج: $MODEL"
