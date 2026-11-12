#!/bin/bash
# تثبيت مراقب سيرفرات LIBANON 4K على الـ Raspberry Pi
set -e
DIR="$HOME/.iptv-monitor"
mkdir -p "$DIR"
curl -fsSL https://raw.githubusercontent.com/iphonesalha-web/lb4k-push-q7w/main/pi/iptv_monitor.py -o "$DIR/iptv_monitor.py"
chmod 700 "$DIR/iptv_monitor.py"

echo "=== مراقب سيرفرات LIBANON 4K ==="
read -rp "توكن بوت تيليجرام: " BOT </dev/tty
read -rp "Chat ID [7859811941]: " CHAT </dev/tty; CHAT=${CHAT:-7859811941}

ACCOUNTS="[]"
add() {
  local name="$1" urls="$2"
  read -rp "اسم المستخدم لـ $name (اتركه فارغاً للتخطي): " U </dev/tty
  [ -z "$U" ] && return
  read -rsp "كلمة السر لـ $name: " P </dev/tty; echo
  ACCOUNTS=$(python3 -c 'import json,sys; a=json.loads(sys.argv[1]); a.append({"name":sys.argv[2],"user":sys.argv[3],"pass":sys.argv[4],"urls":sys.argv[5].split()}); print(json.dumps(a))' \
    "$ACCOUNTS" "$name" "$U" "$P" "$urls")
}
add "Neo" "http://libanon4k.eu http://pro.libanon4k.eu http://cf.libanon4k.eu http://apps.libanon4k.eu http://pro.business-cloud-neo.com"
add "Dino" "http://maripor.com"

python3 -c 'import json,sys; json.dump({"bot":sys.argv[1],"chat":sys.argv[2],"accounts":json.loads(sys.argv[3])}, open(sys.argv[4],"w"), ensure_ascii=False, indent=1)' \
  "$BOT" "$CHAT" "$ACCOUNTS" "$DIR/config.json"
chmod 600 "$DIR/config.json"

( crontab -l 2>/dev/null | grep -v iptv_monitor ; echo "*/2 * * * * /usr/bin/python3 $DIR/iptv_monitor.py >> $DIR/log.txt 2>&1" ) | crontab -
echo
echo "✅ تم التثبيت — الفحص كل دقيقتين."
echo "يُرسل الآن تقرير تجريبي إلى تيليجرام…"
python3 "$DIR/iptv_monitor.py" test
