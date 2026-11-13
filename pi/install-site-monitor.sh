#!/bin/bash
# مراقب المواقع المشهورة — تنبيه تيليجرام عند تعطّل موقع/خدمة + أمر /sites في البوت
#   تثبيت:  curl -fsSL https://raw.githubusercontent.com/iphonesalha-web/lb4k-push-q7w/main/pi/install-site-monitor.sh | sudo bash
#   إلغاء:  curl -fsSL https://raw.githubusercontent.com/iphonesalha-web/lb4k-push-q7w/main/pi/install-site-monitor.sh | sudo bash -s remove
set -e
[ "$(id -u)" = 0 ] || { echo "شغّله مع sudo"; exit 1; }

if [ "$1" = "remove" ]; then
  systemctl disable --now site-monitor.timer 2>/dev/null || true
  rm -f /etc/systemd/system/site-monitor.{service,timer} /usr/local/bin/site_monitor.py
  systemctl daemon-reload
  echo "✅ أُزيل مراقب المواقع."
  exit 0
fi

B=https://raw.githubusercontent.com/iphonesalha-web/lb4k-push-q7w/main/pi
curl -fsSL $B/site_monitor.py -o /usr/local/bin/site_monitor.py
curl -fsSL $B/pi_health.py    -o /usr/local/bin/pi_health.py      # لأمر /sites
curl -fsSL $B/pi_screen.py    -o /usr/local/bin/pi_screen.py
chmod 755 /usr/local/bin/site_monitor.py /usr/local/bin/pi_health.py

# قائمة المواقع قابلة للتعديل:  sudo nano /etc/site-monitor.json
[ -f /etc/site-monitor.json ] || python3 -c "
import json, sys; sys.path.insert(0, '/usr/local/bin'); import site_monitor as m
json.dump(m.DEFAULT_CONF, open('/etc/site-monitor.json', 'w'), ensure_ascii=False, indent=2)"

cat > /etc/systemd/system/site-monitor.service <<'U'
[Unit]
Description=Popular sites outage monitor
After=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /usr/local/bin/site_monitor.py
TimeoutStartSec=170
U

cat > /etc/systemd/system/site-monitor.timer <<'U'
[Unit]
Description=Popular sites outage monitor (every 2 min)

[Timer]
OnBootSec=2min
OnUnitActiveSec=2min

[Install]
WantedBy=timers.target
U

systemctl daemon-reload
echo "… أول فحص (حوالي 20 ثانية)"
python3 /usr/local/bin/site_monitor.py test
systemctl enable --now site-monitor.timer >/dev/null 2>&1
systemctl restart pi-health.service 2>/dev/null || true
echo "✅ مراقب المواقع يعمل — فحص كل دقيقتين. اكتب للبوت: /sites"
