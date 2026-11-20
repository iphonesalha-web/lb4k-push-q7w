#!/bin/bash
# Salha AI (Google Gemini) على الـ Pi — وسيط لتطبيق الآيفون، المفتاح يبقى على الـ Pi.
# لا يطلب لصق أي شيء في الطرفية: المفتاح يُلصق في صفحة إعداد تفتحها من Safari.
#   curl -fsSL https://raw.githubusercontent.com/iphonesalha-web/lb4k-push-q7w/main/pi/install-salha-ai.sh -o /tmp/sai.sh && sudo bash /tmp/sai.sh
# تشغيله مرة ثانية = تحديث البرنامج (المفتاح والرمز يبقيان).
set -e
[ "$(id -u)" = 0 ] || { echo "شغّله مع sudo"; exit 1; }
B=https://raw.githubusercontent.com/iphonesalha-web/lb4k-push-q7w/main/pi
PORT=8095

echo "① تنزيل البرنامج…"
for f in salha_ai.py pi_screen.py pi_health.py; do
  curl -fsSL "$B/$f?t=$(date +%s)" -o /usr/local/bin/$f
done
chmod 755 /usr/local/bin/salha_ai.py

echo "② تشغيل الخدمة…"
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

for i in $(seq 1 15); do
  curl -fsS -o /dev/null "http://127.0.0.1:$PORT/api/version" 2>/dev/null && break
  sleep 1
done

IP=$(hostname -I | awk '{print $1}')
echo
if curl -fsS -o /dev/null "http://127.0.0.1:$PORT/api/version" 2>/dev/null; then
  echo "✅ Salha AI يعمل"
  echo
  echo "الآن افتح هذا الرابط من Safari على الآيفون (وأنت على WLAN البيت):"
  echo
  echo "     http://$IP:$PORT/setup"
  echo
  echo "هناك: ① الصق مفتاح Gemini  ② انسخ رمز الدخول  ③ انسخ العناوين للتطبيق"
else
  echo "❌ الخدمة لا تعمل:"
  journalctl -u salha-ai -n 20 --no-pager
  exit 1
fi
