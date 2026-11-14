#!/bin/bash
# MeTube على الـ Pi: تنزيل فيديو/صوت من YouTube و TikTok و Instagram وغيرها (yt-dlp)
# بواجهة ويب تُفتح من Safari على الآيفون. الملفات تُحفظ على الهارد ديسك.
#   curl -fsSL https://raw.githubusercontent.com/iphonesalha-web/lb4k-push-q7w/main/pi/install-metube.sh -o /tmp/mt.sh && sudo bash /tmp/mt.sh
# تشغيله مرة ثانية = تحديث MeTube و yt-dlp لآخر نسخة.
set -e
[ "$(id -u)" = 0 ] || { echo "شغّله مع sudo"; exit 1; }

PORT=8081
NAME=metube
IMAGE=ghcr.io/alexta69/metube:latest
DL=/mnt/hdd/downloads/metube

mountpoint -q /mnt/hdd || { echo "❌ الهارد ديسك غير موصول على /mnt/hdd"; exit 1; }

if ! command -v docker >/dev/null; then
  echo "تثبيت Docker…"
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker >/dev/null 2>&1 || true

mkdir -p "$DL/video" "$DL/audio" "$DL/.state"
OWNER=$(stat -c '%U' /mnt/hdd 2>/dev/null || echo root)
[ "$OWNER" = root ] && OWNER=$(getent passwd 1000 | cut -d: -f1)
chown -R "$OWNER": "$DL"
UID_=$(id -u "$OWNER"); GID_=$(id -g "$OWNER")

if ss -ltn "( sport = :$PORT )" | grep -q LISTEN && ! docker ps --format '{{.Names}}' | grep -qx "$NAME"; then
  echo "❌ المنفذ $PORT مستعمل من برنامج آخر"; exit 1
fi

echo "تنزيل آخر نسخة من MeTube…"
docker pull -q "$IMAGE" >/dev/null
docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" --restart unless-stopped \
  -p "$PORT:8081" \
  -e UID="$UID_" -e GID="$GID_" -e UMASK=022 \
  -e DOWNLOAD_DIR=/downloads/video \
  -e AUDIO_DOWNLOAD_DIR=/downloads/audio \
  -e STATE_DIR=/downloads/.state \
  -e DEFAULT_THEME=dark \
  -e OUTPUT_TEMPLATE='%(title).120s.%(ext)s' \
  -v "$DL:/downloads" \
  "$IMAGE" >/dev/null
docker image prune -f >/dev/null 2>&1 || true

# تحديث أسبوعي تلقائي (المواقع تغيّر طريقتها باستمرار و yt-dlp يحتاج تحديثاً)
cp "$0" /usr/local/sbin/install-metube.sh 2>/dev/null || true
chmod 755 /usr/local/sbin/install-metube.sh 2>/dev/null || true
cat > /etc/systemd/system/metube-update.service <<EOF
[Unit]
Description=Update MeTube
After=docker.service network-online.target
Wants=network-online.target
[Service]
Type=oneshot
ExecStart=/bin/bash /usr/local/sbin/install-metube.sh
EOF
cat > /etc/systemd/system/metube-update.timer <<EOF
[Unit]
Description=Weekly MeTube update
[Timer]
OnCalendar=Wed 04:15
Persistent=true
[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now metube-update.timer >/dev/null

sleep 4
IP=$(hostname -I | awk '{print $1}')
if curl -fsS -o /dev/null "http://127.0.0.1:$PORT"; then
  echo
  echo "✅ MeTube يعمل"
  echo "   افتح على الآيفون: http://$IP:$PORT"
  echo "   الملفات على الهارد: $DL"
  echo "   تحديث تلقائي كل أربعاء 04:15"
else
  echo "⚠️ الحاوية بدأت لكن الصفحة لم ترد بعد — انتظر دقيقة ثم افتح http://$IP:$PORT"
  docker logs --tail 20 "$NAME" || true
fi
