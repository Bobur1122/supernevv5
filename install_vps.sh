#!/usr/bin/env bash
set -e

echo "======================================================"
echo "🚀 AI GOLD SNIPER BOT — VPS AVTOMATIK O'RNATUVCHI"
echo "======================================================"

sudo apt-get update -y
sudo apt-get install -y python3 python3-pip python3-venv unzip

APP_DIR="$HOME/ai_gold_sniper"
mkdir -p "$APP_DIR"

# Agar skript fayllar turgan papkada ishga tushirilsa, ularni APP_DIR ga nusxalaymiz
for f in config.py schemas.py data_feed.py news_scraper.py fundamental_agent.py technical_agent.py risk_manager.py telegram_bot.py main.py requirements.txt; do
    if [ -f "./$f" ] && [ "$(realpath "./$f")" != "$(realpath "$APP_DIR/$f" 2>/dev/null)" ]; then
        cp "./$f" "$APP_DIR/"
    fi
done

cd "$APP_DIR"
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

SERVICE_FILE="/etc/systemd/system/gold-sniper.service"
sudo bash -c "cat > $SERVICE_FILE" << EOL
[Unit]
Description=AI Gold Sniper 24/7 Trading Bot (FOREXCOM:XAUUSD)
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/python $APP_DIR/main.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOL

sudo systemctl daemon-reload
sudo systemctl enable gold-sniper.service
sudo systemctl restart gold-sniper.service

echo "✅ Barcha fayllar o'rnatildi va 24/7 fon xizmati (gold-sniper.service) ishga tushirildi!"
echo "📊 Jonli loglarni ko'rish uchun: sudo journalctl -u gold-sniper.service -f"