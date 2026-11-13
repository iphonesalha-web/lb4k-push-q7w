#!/bin/bash
# ربط مراقب المواقع ببوت تيليجرام خاص به (حتى تكتمه وحده متى شئت)
#   curl -fsSL https://raw.githubusercontent.com/iphonesalha-web/lb4k-push-q7w/main/pi/setup-site-bot.sh -o /tmp/sb.sh && sudo bash /tmp/sb.sh
set -e
[ "$(id -u)" = 0 ] || { echo "شغّله مع sudo"; exit 1; }
CONF=/etc/site-monitor.json
[ -f "$CONF" ] || { echo "ركّب مراقب المواقع أولاً"; exit 1; }

# نفس الـ Chat ID (أنت نفس الشخص) — يؤخذ من إعداد البوت الحالي
CHAT=$(python3 -c "import json,glob; print(json.load(open(glob.glob('/home/*/.iptv-monitor/config.json')[0]))['chat'])" 2>/dev/null || true)
[ -n "$CHAT" ] || read -r -p "Chat ID: " CHAT </dev/tty

echo "الصق توكن البوت الجديد من BotFather (لن يظهر أثناء اللصق) ثم Enter:"
read -r -s TOKEN </dev/tty; echo
TOKEN=$(echo "$TOKEN" | tr -d '[:space:]')

NAME=$(curl -fsS "https://api.telegram.org/bot$TOKEN/getMe" | python3 -c "import json,sys; print(json.load(sys.stdin)['result']['username'])" 2>/dev/null) \
  || { echo "❌ التوكن غير صحيح — انسخه كاملاً من BotFather"; exit 1; }
echo "البوت: @$NAME"

OK=$(curl -fsS "https://api.telegram.org/bot$TOKEN/sendMessage" --data-urlencode "chat_id=$CHAT" \
  --data-urlencode "text=🌐 هذا بوت تنبيهات المواقع ✅ من الآن تصل تنبيهات تعطّل المواقع هنا." 2>/dev/null | grep -c '"ok":true' || true)
[ "$OK" = 1 ] || { echo "❌ لم تصل الرسالة — افتح @$NAME في تيليجرام واضغط Start (ابدأ) ثم أعد الأمر"; exit 1; }

python3 - "$CONF" "$TOKEN" "$CHAT" <<'PY'
import json, sys
p, tok, chat = sys.argv[1:]
c = json.load(open(p)); c["bot"] = tok; c["chat"] = chat
json.dump(c, open(p, "w"), ensure_ascii=False, indent=2)
PY
chmod 600 "$CONF"
curl -fsSL https://raw.githubusercontent.com/iphonesalha-web/lb4k-push-q7w/main/pi/site_monitor.py -o /usr/local/bin/site_monitor.py
echo "✅ تمّ. تنبيهات المواقع تصل الآن من @$NAME — ويمكنك كتمه وحده."
