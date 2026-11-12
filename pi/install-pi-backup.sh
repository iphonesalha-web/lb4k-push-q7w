#!/bin/bash
# نسخة احتياطية أسبوعية للـ Pi على الهارد ديسك (كل أحد 03:30)
#   تثبيت:  curl -fsSL https://raw.githubusercontent.com/iphonesalha-web/lb4k-push-q7w/main/pi/install-pi-backup.sh | sudo bash
#   إلغاء:  curl -fsSL https://raw.githubusercontent.com/iphonesalha-web/lb4k-push-q7w/main/pi/install-pi-backup.sh | sudo bash -s remove
set -e
[ "$(id -u)" = 0 ] || { echo "شغّله مع sudo"; exit 1; }

if [ "$1" = "remove" ]; then
  systemctl disable --now pi-backup.timer 2>/dev/null || true
  rm -f /etc/systemd/system/pi-backup.{service,timer} /usr/local/sbin/pi-backup
  systemctl daemon-reload
  echo "✅ أُلغيت النسخ الاحتياطية التلقائية (النسخ الموجودة على الهارد باقية)."
  exit 0
fi

echo "… تنزيل الأدوات اللازمة"
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq rsync dosfstools fdisk >/dev/null

curl -fsSL https://raw.githubusercontent.com/iphonesalha-web/lb4k-push-q7w/main/pi/pi-backup.sh -o /usr/local/sbin/pi-backup
chmod 755 /usr/local/sbin/pi-backup

cat > /etc/systemd/system/pi-backup.service <<'EOF'
[Unit]
Description=Weekly Pi backup image to HDD
RequiresMountsFor=/mnt/hdd

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/pi-backup
Nice=10
IOSchedulingClass=idle
EOF

cat > /etc/systemd/system/pi-backup.timer <<'EOF'
[Unit]
Description=Weekly Pi backup (Sunday 03:30)

[Timer]
OnCalendar=Sun *-*-* 03:30:00
Persistent=true
RandomizedDelaySec=10m

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now pi-backup.timer >/dev/null 2>&1
echo "✅ النسخ الأسبوعي مفعّل — كل أحد الساعة 03:30 تقريباً."
echo "   الموعد القادم: $(systemctl show pi-backup.timer -p NextElapseUSecRealtime --value)"

# أول نسخة الآن في الخلفية — تصلك رسالة تيليجرام عند الانتهاء (5–15 دقيقة)
systemctl start --no-block pi-backup.service
echo "⏳ بدأت أول نسخة الآن في الخلفية. تصلك رسالة على تيليجرام عند الانتهاء."
echo "   لمتابعتها:  tail -f /var/log/pi-backup.log"
