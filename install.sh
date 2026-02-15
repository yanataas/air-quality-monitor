#!/bin/bash

# Air Quality Monitor - Installation Script for Raspberry Pi
# Запускать с правами sudo: sudo bash install.sh

echo "========================================="
echo "🌡️  Air Quality Monitor Installation"
echo "========================================="

# Проверка прав root
if [ "$EUID" -ne 0 ]; then 
   echo "❌ Please run as root (use sudo)"
   exit 1
fi

echo "📦 Updating system..."
apt update && apt upgrade -y

echo "📦 Installing dependencies..."
apt install -y python3 python3-pip python3-venv
apt install -y git curl wget
apt install -y chromium-browser xorg openbox lightdm
apt install -y sqlite3

echo "📦 Installing Python packages..."
pip3 install flask flask-socketio pyserial pandas numpy

# Создание пользователя pi если не существует
if ! id "pi" &>/dev/null; then
    useradd -m -s /bin/bash pi
    echo "✅ User 'pi' created"
fi

# Создание структуры проекта
PROJECT_DIR="/home/pi/air-quality-monitor"
mkdir -p $PROJECT_DIR/{templates,data,scripts,config,backups}

echo "📁 Copying project files..."

# Копирование файлов (предполагается, что скрипт запускается из папки с проектом)
if [ -f "server.py" ]; then
    cp server.py $PROJECT_DIR/
    echo "✅ server.py copied"
else
    echo "⚠️ server.py not found, please copy manually"
fi

if [ -f "templates/index.html" ]; then
    cp templates/index.html $PROJECT_DIR/templates/
    echo "✅ index.html copied"
else
    echo "⚠️ index.html not found, please copy manually"
fi

# Создание скрипта запуска киоска
cat > $PROJECT_DIR/scripts/start-kiosk.sh << 'EOF'
#!/bin/bash
# Киоск режим для сенсорного экрана

export DISPLAY=:0
export XAUTHORITY=/home/pi/.Xauthority

# Ждем загрузки системы
sleep 15

# Запускаем Flask сервер
cd /home/pi/air-quality-monitor
python3 server.py &

# Ждем запуска сервера
sleep 10

# Отключаем скринсейвер
xset s off
xset -dpms
xset s noblank

# Запускаем браузер в киоск-режиме
chromium-browser --noerrdialogs \
                 --disable-infobars \
                 --kiosk \
                 --incognito \
                 --disable-pinch \
                 --overscroll-history-navigation=0 \
                 --disable-features=TranslateUI \
                 --disable-translate \
                 --disk-cache-dir=/dev/null \
                 --media-cache-dir=/dev/null \
                 http://localhost:5000 &

# Держим процесс в живых
wait
EOF

chmod +x $PROJECT_DIR/scripts/start-kiosk.sh
chown -R pi:pi $PROJECT_DIR

# Создание systemd сервиса
cat > /etc/systemd/system/air-quality.service << EOF
[Unit]
Description=Air Quality Monitor
After=network.target multi-user.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/air-quality-monitor
ExecStart=/usr/bin/python3 /home/pi/air-quality-monitor/server.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# Создание сервиса для киоска
cat > /etc/systemd/system/kiosk.service << EOF
[Unit]
Description=Kiosk Mode
After=graphical.target network.target air-quality.service
Requires=graphical.target

[Service]
Type=simple
User=pi
Environment=DISPLAY=:0
ExecStart=/home/pi/air-quality-monitor/scripts/start-kiosk.sh
Restart=always
RestartSec=10

[Install]
WantedBy=graphical.target
EOF

# Скрипт для бэкапа
cat > $PROJECT_DIR/scripts/backup-data.sh << 'EOF'
#!/bin/bash
BACKUP_DIR="/home/pi/air-quality-monitor/backups"
DATE=$(date +%Y%m%d_%H%M%S)

# Создание бэкапа базы данных
cp /home/pi/air-quality-monitor/air_quality.db $BACKUP_DIR/air_quality_$DATE.db

# Сжатие
gzip $BACKUP_DIR/air_quality_$DATE.db

# Удаление старых бэкапов (старше 30 дней)
find $BACKUP_DIR -name "*.gz" -mtime +30 -delete

echo "✅ Backup created: air_quality_$DATE.db.gz"
EOF

chmod +x $PROJECT_DIR/scripts/backup-data.sh

# Настройка автозапуска
systemctl enable air-quality.service
systemctl enable kiosk.service

# Добавление задания в crontab для бэкапа
(crontab -u pi -l 2>/dev/null; echo "0 2 * * * /home/pi/air-quality-monitor/scripts/backup-data.sh") | crontab -u pi -

# Настройка прав на USB порты
usermod -a -G dialout pi

# Включение авто-логина
raspi-config nonint do_boot_behaviour B2

echo "========================================="
echo "✅ Installation complete!"
echo "========================================="
echo ""
echo "📋 Next steps:"
echo "1. Reboot your Raspberry Pi: sudo reboot"
echo "2. Connect Arduino via USB"
echo "3. The dashboard will start automatically"
echo ""
echo "📍 Project location: /home/pi/air-quality-monitor"
echo "🌐 Dashboard URL: http://localhost:5000"
echo "📊 Database: /home/pi/air-quality-monitor/air_quality.db"
echo ""
echo "🔧 Useful commands:"
echo "   - Check service: sudo systemctl status air-quality"
echo "   - View logs: sudo journalctl -u air-quality -f"
echo "   - Manual backup: /home/pi/air-quality-monitor/scripts/backup-data.sh"
echo ""
echo "========================================="
