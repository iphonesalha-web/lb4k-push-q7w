#!/bin/bash
# Salha AI (Gemini) على الـ Pi — وسيط لتطبيق الآيفون، المفتاح يبقى على الـ Pi.
#   curl -fsSL https://raw.githubusercontent.com/iphonesalha-web/lb4k-push-q7w/main/pi/install-salha-ai.sh -o /tmp/sai.sh && sudo bash /tmp/sai.sh
# تشغيله مرة ثانية = تحديث البرنامج (المفتاح والرمز يبقيان). لتغيير المفتاح: sudo bash /tmp/sai.sh key
set -e
[ "$(id -u)" = 0 ] || { echo "شغّله مع sudo"; exit 1; }
B=https://raw.githubusercontent.com/iphonesalha-web/lb4k-push-q7w/main/pi
CONF=/etc/salha-ai.json
PORT=8095

for f in salha_ai.py pi_screen.py pi_health.py; do
  curl -fsSL "$B/$f?t=$(date +%s)" -o /usr/local/bin/$f
done
chmod 755 /usr/local/bin/salha_ai.py

if [ ! -f "$CONF" ] || [ "$1" = key ]; then
  echo
  echo "الصق مفتاح Gemini (من aistudio.google.com ← Get API key) ثم Enter."
  echo "(لن يظهر على الشاشة)"
  # بعض تطبيقات SSH تقسم النص الملصوق على عدة أسطر أو تضيف رموز لصق —
  # نقرأ حتى يكتمل مفتاح بشكل AIza… (39 حرفاً)
  RAW=""; KEY=""
  for i in 1 2 3 4 5; do
    IFS= read -r -s LINE </dev/tty || break
    RAW="$RAW$LINE"
    KEY=$(printf '%s' "$RAW" | grep -oE 'AIza[0-9A-Za-z_-]{35}' | head -1)
    [ -n "$KEY" ] && break
    echo "   (تابع اللصق أو اضغط Enter مرة أخرى)"
  done
  echo
  [ -n "$KEY" ] || KEY=$(printf '%s' "$RAW" | sed 's/\x1b\[[0-9;]*~//g' | tr -cd 'A-Za-z0-9_-')
  [ ${#KEY} -ge 30 ] || { echo "❌ لم أستلم المفتاح كاملاً — جرّب مرة ثانية"; exit 1; }
  echo "   استلمت مفتاحاً طوله ${#KEY} حرفاً (يبدأ بـ ${KEY:0:4}…)"
  OLD_TOKEN=$(python3 -c "import json;print(json.load(open('$CONF')).get('token',''))" 2>/dev/null || true)
  TOKEN=${OLD_TOKEN:-$(python3 -c "import secrets;print(secrets.token_urlsafe(18))")}
  umask 077
  python3 - "$CONF" "$KEY" "$TOKEN" <<'PY'
import json, sys
p, k, t = sys.argv[1:]
json.dump({"key": k, "token": t}, open(p, "w"))
PY
  chmod 600 "$CONF"
fi

echo "اختبار المفتاح…"
python3 /usr/local/bin/salha_ai.py test || { echo "❌ فشل الاختبار — تأكد من المفتاح: sudo bash /tmp/sai.sh key"; exit 1; }

cat > /etc/systemd/system/salha-ai.service <<EOF
[Unit]
Description=Salha AI (Gemini proxy)
After=network-online.target
Wants=network-online.target
[Service]
ExecStart=/usr/bin/python3 -u /usr/local/bin/salha_ai.py
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable salha-ai >/dev/null 2>&1
systemctl restart salha-ai
sleep 2

IP=$(hostname -I | awk '{print $1}')
IP6=$(ip -6 addr show scope global | awk '/inet6/ && $2 !~ /^fd/ {print $2}' | cut -d/ -f1 | head -1)
TOKEN=$(python3 -c "import json;print(json.load(open('$CONF'))['token'])")
echo
if systemctl is-active --quiet salha-ai; then echo "✅ Salha AI يعمل"; else echo "❌ الخدمة لا تعمل"; journalctl -u salha-ai -n 15 --no-pager; exit 1; fi
echo
echo "في تطبيق Salha AI ← الإعدادات:"
echo "  العنوان في البيت:   http://$IP:$PORT"
[ -n "$IP6" ] && echo "  العنوان من الخارج:  http://[$IP6]:$PORT"
echo "  رمز الدخول:         $TOKEN"
echo
echo "(رمز الدخول خاص بك — اكتبه في التطبيق فقط ولا ترسله لأحد)"
