#!/bin/bash
# نسخة احتياطية كاملة للـ Raspberry Pi على الهارد ديسك — صورة .img قابلة للحرق مباشرة
# على كرت SD جديد (Raspberry Pi Imager ← Use custom).
# - تُصنع من النظام وهو يعمل (rsync)، وتحمل نفس معرّفات الأقسام، فتقلع كما هي.
# - الملف "متفرّق" (sparse): يأخذ من الهارد بقدر البيانات فقط.
# - يُحتفظ بآخر KEEP نسخ، وتصل رسالة على تيليجرام عند النجاح أو الفشل.
set -euo pipefail

DEST=/mnt/hdd/pi-backups
KEEP=4
MNT=/run/pi-backup
LOG=/var/log/pi-backup.log

exec 9>/run/pi-backup.lock
flock -n 9 || { echo "نسخة احتياطية أخرى تعمل الآن"; exit 1; }
exec > >(tee -a "$LOG") 2>&1

START=$(date +%s)
STAMP=$(date +%Y-%m-%d_%H%M)
IMG="$DEST/pi-$STAMP.img"
LOOP=""

telegram() {
  local conf
  conf=$(ls /home/*/.iptv-monitor/config.json 2>/dev/null | head -1) || true
  [ -n "$conf" ] || return 0
  python3 - "$conf" "$1" <<'PY' || true
import json, sys, urllib.parse, urllib.request
c = json.load(open(sys.argv[1]))
data = urllib.parse.urlencode({"chat_id": c["chat"], "text": sys.argv[2]}).encode()
urllib.request.urlopen(f"https://api.telegram.org/bot{c['bot']}/sendMessage", data=data, timeout=20).read()
PY
}

cleanup() {
  set +e
  mountpoint -q "$MNT/boot/firmware" && umount "$MNT/boot/firmware"
  mountpoint -q "$MNT" && umount "$MNT"
  [ -n "$LOOP" ] && losetup -d "$LOOP"
}

fail() {
  echo "❌ $1"
  cleanup
  rm -f "$IMG"
  telegram "❌ فشلت النسخة الاحتياطية الأسبوعية للـ Pi
السبب: $1"
  exit 1
}
trap 'fail "خطأ غير متوقع في السطر $LINENO"' ERR

echo "=== $(date) — بدء النسخة الاحتياطية"
mountpoint -q /mnt/hdd || fail "الهارد ديسك غير موصول (/mnt/hdd)"
mkdir -p "$DEST"

# ── أقسام كرت الذاكرة الحالي
ROOT_PART=$(findmnt -no SOURCE /)
BOOT_MNT=/boot/firmware; mountpoint -q $BOOT_MNT || BOOT_MNT=/boot
BOOT_PART=$(findmnt -no SOURCE $BOOT_MNT)
DISK=/dev/$(lsblk -no PKNAME "$ROOT_PART")
[ "$(lsblk -no PKNAME "$BOOT_PART")" = "$(basename "$DISK")" ] || fail "قسم الإقلاع ليس على نفس الكرت"
PTTYPE=$(blkid -s PTTYPE -o value "$DISK")
[ "$PTTYPE" = "dos" ] || fail "نوع جدول الأقسام $PTTYPE غير مدعوم"
DISKID=$(sfdisk --disk-id "$DISK")
P1START=$(cat /sys/class/block/"$(basename "$BOOT_PART")"/start)
P1SIZE=$(cat /sys/class/block/"$(basename "$BOOT_PART")"/size)
P2START=$(( ( (P1START + P1SIZE + 8191) / 8192 ) * 8192 ))
BOOT_UUID=$(blkid -s UUID -o value "$BOOT_PART" | tr -d '-')
BOOT_LABEL=$(blkid -s LABEL -o value "$BOOT_PART" || true)
ROOT_UUID=$(blkid -s UUID -o value "$ROOT_PART")
ROOT_LABEL=$(blkid -s LABEL -o value "$ROOT_PART" || true)

# ── حجم الصورة: المستعمل + 25% (على الأقل +2 GB)
USED=$(df -B1 --output=used / | tail -1)
EXTRA=$(( USED / 4 )); [ $EXTRA -lt $((2 * 1024**3)) ] && EXTRA=$((2 * 1024**3))
ROOT_BYTES=$(( (USED + EXTRA) / 1048576 * 1048576 ))
TOTAL=$(( P2START * 512 + ROOT_BYTES ))
FREE=$(df -B1 --output=avail /mnt/hdd | tail -1)
[ $FREE -gt $(( USED * 2 )) ] || fail "لا توجد مساحة كافية على الهارد"
echo "الكرت: $DISK  المستعمل: $((USED / 1024**2)) MB  حجم الصورة: $((TOTAL / 1024**2)) MB"

# ── إنشاء الصورة بنفس معرّفات الكرت (فتقلع دون تعديل fstab أو cmdline.txt)
truncate -s "$TOTAL" "$IMG"
sfdisk -q "$IMG" <<EOF
label: dos
label-id: $DISKID
unit: sectors
start=$P1START, size=$P1SIZE, type=c
start=$P2START, type=83
EOF
LOOP=$(losetup -Pf --show "$IMG")
udevadm settle || true
mkfs.vfat -F 32 -i "$BOOT_UUID" ${BOOT_LABEL:+-n "$BOOT_LABEL"} "${LOOP}p1" >/dev/null
mkfs.ext4 -q -F -U "$ROOT_UUID" ${ROOT_LABEL:+-L "$ROOT_LABEL"} "${LOOP}p2"

mkdir -p "$MNT"
mount "${LOOP}p2" "$MNT"

# ── نسخ النظام: -x لا يدخل في الأقسام الأخرى (الهارد، /proc، /boot/firmware ...)
echo "نسخ النظام…"
rsync -aHAXx --numeric-ids --info=stats1 \
  --exclude='/swapfile' --exclude='/var/swap' \
  --exclude='/var/cache/apt/archives/*.deb' \
  --exclude='/tmp/*' --exclude='/var/tmp/*' \
  --exclude='/home/*/.cache/*' --exclude='/root/.cache/*' \
  --exclude='/lost+found' \
  / "$MNT/" || [ $? -eq 24 ]   # 24 = ملفات اختفت أثناء النسخ — طبيعي على نظام يعمل

mkdir -p "$MNT$BOOT_MNT"
mount "${LOOP}p1" "$MNT$BOOT_MNT"
echo "نسخ قسم الإقلاع…"
rsync -rt --modify-window=2 "$BOOT_MNT/" "$MNT$BOOT_MNT/"

# فحص سريع: الملفات الأساسية موجودة
for f in "$MNT/etc/fstab" "$MNT$BOOT_MNT/cmdline.txt" "$MNT$BOOT_MNT/config.txt"; do
  [ -s "$f" ] || fail "ملف أساسي ناقص في النسخة: ${f#$MNT}"
done

sync
cleanup
LOOP=""
trap - ERR

# ── الاحتفاظ بآخر KEEP نسخ فقط
ls -1t "$DEST"/pi-*.img 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f

SIZE=$(du -h "$IMG" | cut -f1)
COUNT=$(ls -1 "$DEST"/pi-*.img | wc -l)
MIN=$(( ($(date +%s) - START + 59) / 60 ))
echo "✅ تمت: $IMG ($SIZE) في $MIN دقيقة"
echo "{\"file\": \"$(basename "$IMG")\", \"time\": $(date +%s), \"size\": \"$SIZE\"}" > "$DEST/last.json"
telegram "💾 تمت النسخة الاحتياطية الأسبوعية للـ Pi ✅
الملف: $(basename "$IMG")
الحجم على الهارد: $SIZE
المدة: $MIN دقيقة
النسخ المحفوظة: $COUNT من $KEEP"
