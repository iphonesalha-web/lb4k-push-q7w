#!/bin/bash
# حارس الـ Pi — تنبيهات تيليجرام (الحرارة، الكهرباء، الهارد، الذاكرة، الخدمات) + أمر /status
#   تثبيت:  curl -fsSL https://iphonesalha-web.github.io/lb4k-push-q7w/pi/install-pi-health.sh | sudo bash
#   إلغاء:  curl -fsSL https://iphonesalha-web.github.io/lb4k-push-q7w/pi/install-pi-health.sh | sudo bash -s remove
set -e
[ "$(id -u)" = 0 ] || { echo "شغّله مع sudo"; exit 1; }

if [ "$1" = "remove" ]; then
  systemctl disable --now pi-health.service 2>/dev/null || true
  rm -f /etc/systemd/system/pi-health.service /usr/local/bin/pi_health.py
  systemctl daemon-reload
  echo "✅ أُزيل حارس الـ Pi."
  exit 0
fi

if ! ls /home/*/.iptv-monitor/config.json >/dev/null 2>&1; then
  echo "⚠️ لم أجد إعداد البوت (~/.iptv-monitor/config.json). ركّب مراقب السيرفرات أولاً."
  exit 1
fi

B=https://iphonesalha-web.github.io/lb4k-push-q7w/pi
curl -fsSL $B/pi_screen.py -o /usr/local/bin/pi_screen.py
curl -fsSL $B/pi_health.py -o /usr/local/bin/pi_health.py
chmod 755 /usr/local/bin/pi_screen.py /usr/local/bin/pi_health.py

cat > /etc/systemd/system/pi-health.service <<EOF
[Unit]
Description=Pi health alerts on Telegram
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 -u /usr/local/bin/pi_health.py
Restart=always
RestartSec=20

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
/usr/bin/python3 /usr/local/bin/pi_health.py test
systemctl enable pi-health.service >/dev/null 2>&1
systemctl restart pi-health.service
echo "✅ حارس الـ Pi يعمل. اكتب للبوت في تيليجرام: /status"
