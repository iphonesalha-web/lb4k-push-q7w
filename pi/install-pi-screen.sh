#!/bin/bash
# شاشة معلومات الـ Pi على التلفزيون (HDMI)
#   تثبيت:  curl -fsSL https://iphonesalha-web.github.io/lb4k-push-q7w/pi/install-pi-screen.sh | sudo bash
#   إلغاء:  curl -fsSL https://iphonesalha-web.github.io/lb4k-push-q7w/pi/install-pi-screen.sh | sudo bash -s remove
set -e
[ "$(id -u)" = 0 ] || { echo "شغّله مع sudo"; exit 1; }

if [ "$1" = "remove" ]; then
  systemctl disable --now pi-screen.service 2>/dev/null || true
  rm -f /etc/systemd/system/pi-screen.service /usr/local/bin/pi_screen.py
  systemctl daemon-reload
  systemctl enable --now getty@tty1.service
  echo "✅ أُزيلت الشاشة ورجعت شاشة الدخول العادية."
  exit 0
fi

curl -fsSL https://iphonesalha-web.github.io/lb4k-push-q7w/pi/pi_screen.py -o /usr/local/bin/pi_screen.py
chmod 755 /usr/local/bin/pi_screen.py

# خط كبير وواضح على التلفزيون إن وُجد
FONT=""
for f in Uni2-TerminusBold32x16 Lat15-TerminusBold32x16 Uni2-TerminusBold28x14 Lat15-TerminusBold28x14; do
  [ -e "/usr/share/consolefonts/$f.psf.gz" ] && { FONT="/usr/share/consolefonts/$f.psf.gz"; break; }
done
PRE=""
[ -n "$FONT" ] && PRE="ExecStartPre=-/usr/bin/setfont -C /dev/tty1 $FONT"

cat > /etc/systemd/system/pi-screen.service <<EOF
[Unit]
Description=Pi info screen on HDMI (tty1)
After=systemd-user-sessions.service network-online.target
Conflicts=getty@tty1.service

[Service]
Type=simple
$PRE
ExecStart=/usr/bin/python3 -u /usr/local/bin/pi_screen.py
StandardInput=tty
StandardOutput=tty
TTYPath=/dev/tty1
TTYReset=yes
TTYVHangup=yes
Environment=TERM=linux
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

# رسائل النواة لا تكتب فوق الشاشة
echo "kernel.printk = 3 4 1 3" > /etc/sysctl.d/90-pi-screen.conf
sysctl -q -p /etc/sysctl.d/90-pi-screen.conf || true

systemctl daemon-reload
systemctl disable --now getty@tty1.service 2>/dev/null || true
systemctl enable --now pi-screen.service
echo "✅ الشاشة تعمل الآن على التلفزيون (تُحدَّث كل 3 ثوانٍ)."
echo "   للدخول بلوحة مفاتيح: اضغط Ctrl+Alt+F2"
