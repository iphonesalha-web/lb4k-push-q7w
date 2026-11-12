#!/bin/bash
# إعادة تعيين اسم المستخدم وكلمة السر للوحة AdGuard Home
#   curl -fsSL https://raw.githubusercontent.com/iphonesalha-web/lb4k-push-q7w/main/pi/adguard-reset-password.sh -o /tmp/ag.sh && sudo bash /tmp/ag.sh
set -e
[ "$(id -u)" = 0 ] || { echo "شغّله مع sudo"; exit 1; }
CONF=/opt/AdGuardHome/AdGuardHome.yaml
[ -f "$CONF" ] || { echo "لم أجد AdGuard Home ($CONF)"; exit 1; }

command -v htpasswd >/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -qq apache2-utils >/dev/null

echo "الاسم الحالي في الإعدادات:"
grep -A1 '^users:' "$CONF" | grep 'name:' | sed 's/.*name: */   /' || echo "   (لا يوجد)"
echo
read -r -p "اسم المستخدم الجديد: " U </dev/tty
[ -n "$U" ] || { echo "الاسم فارغ"; exit 1; }
read -r -s -p "كلمة السر الجديدة: " P1 </dev/tty; echo
read -r -s -p "أعد كتابتها: " P2 </dev/tty; echo
[ "$P1" = "$P2" ] || { echo "❌ كلمتا السر غير متطابقتين"; exit 1; }
[ ${#P1} -ge 8 ] || { echo "❌ كلمة السر يجب أن تكون 8 أحرف على الأقل"; exit 1; }

HASH=$(htpasswd -nbB -C 10 "$U" "$P1" | cut -d: -f2-)
cp "$CONF" "$CONF.bak"
/opt/AdGuardHome/AdGuardHome -s stop >/dev/null 2>&1 || true
python3 - "$CONF" "$U" "$HASH" <<'PY'
import re, sys
path, user, h = sys.argv[1:]
s = open(path).read()
block = f"users:\n  - name: {user}\n    password: {h}\n"
new, n = re.subn(r"^users:\n(?:[ \t]+.*\n|-.*\n)*", block, s, count=1, flags=re.M)
if n == 0:
    new = block + s
open(path, "w").write(new)
PY
/opt/AdGuardHome/AdGuardHome -s start >/dev/null 2>&1
sleep 2
echo "✅ تم. ادخل على http://192.168.178.140 بالاسم: $U"
