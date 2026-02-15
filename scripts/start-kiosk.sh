#!/bin/bash
# Скрипт запуска киоск-режима для сенсорного экрана
# Запускается автоматически при старте системы

# =========================================
# Настройки
# =========================================
PROJECT_DIR="/home/pi/air-quality-monitor"
DISPLAY=:0
export DISPLAY
export XAUTHORITY="/home/pi/.Xauthority"

# =========================================
# Функция логирования
# =========================================
log_message() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$PROJECT_DIR/kiosk.log"
    echo "$1"
}

log_message "🚀 Starting kiosk mode..."

# =========================================
# Ожидание загрузки системы
# =========================================
log_message "⏳ Waiting for system to stabilize..."
sleep 15

# =========================================
# Проверка наличия X сервера
# =========================================
if ! pgrep -x "Xorg" > /dev/null; then
    log_message "❌ X server not running"
    exit 1
fi

# =========================================
# Запуск Flask сервера
# =========================================
log_message "🔄 Starting Flask server..."
cd "$PROJECT_DIR"

# Проверка, не запущен ли уже сервер
if pgrep -f "python3.*server.py" > /dev/null; then
    log_message "⚠️ Server already running"
else
    # Запуск сервера
    python3 server.py >> "$PROJECT_DIR/server.log" 2>&1 &
    SERVER_PID=$!
    log_message "✅ Server started with PID: $SERVER_PID"
fi

# =========================================
# Ожидание запуска сервера
# =========================================
log_message "⏳ Waiting for server to be ready..."
sleep 10

# =========================================
# Проверка доступности сервера
# =========================================
MAX_RETRIES=30
RETRY_COUNT=0
while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
    if curl -s http://localhost:5000 > /dev/null; then
        log_message "✅ Server is responding"
        break
    fi
    RETRY_COUNT=$((RETRY_COUNT + 1))
    sleep 2
done

if [ $RETRY_COUNT -eq $MAX_RETRIES ]; then
    log_message "⚠️ Server not responding after $MAX_RETRIES attempts"
fi

# =========================================
# Настройка дисплея
# =========================================
log_message "🖥️ Configuring display..."

# Отключаем энергосбережение
xset s off
xset -dpms
xset s noblank

# Настройка сенсорного экрана (если нужно)
# Калибровка touchscreen
if [ -f "/usr/bin/xinput_calibrator" ]; then
    /usr/bin/xinput_calibrator --output-filename /etc/X11/xorg.conf.d/99-calibration.conf
fi

# =========================================
# Очистка кэша браузера
# =========================================
log_message "🧹 Cleaning browser cache..."
rm -rf /home/pi/.cache/chromium/*
rm -rf /home/pi/.config/chromium/Default/Cache/*

# =========================================
# Запуск браузера в киоск-режиме
# =========================================
log_message "🌐 Starting Chromium in kiosk mode..."

# Убиваем старые процессы браузера
pkill -f chromium

# Запускаем браузер
chromium-browser \
    --noerrdialogs \
    --disable-infobars \
    --kiosk \
    --incognito \
    --disable-pinch \
    --overscroll-history-navigation=0 \
    --disable-features=TranslateUI \
    --disable-translate \
    --disk-cache-dir=/dev/null \
    --media-cache-dir=/dev/null \
    --disable-session-crashed-bubble \
    --disable-restore-session-state \
    --disable-sync \
    --disable-notifications \
    --disable-popup-blocking \
    --ignore-certificate-errors \
    --no-first-run \
    --no-default-browser-check \
    --disable-gpu \
    --disable-software-rasterizer \
    --disable-dev-shm-usage \
    --start-maximized \
    --window-position=0,0 \
    --force-device-scale-factor=1 \
    http://localhost:5000 &

BROWSER_PID=$!
log_message "✅ Browser started with PID: $BROWSER_PID"

# =========================================
# Мониторинг процессов
# =========================================
log_message "👀 Starting process monitor..."

while true; do
    # Проверка браузера
    if ! kill -0 $BROWSER_PID 2>/dev/null; then
        log_message "⚠️ Browser crashed, restarting..."
        
        # Перезапуск браузера
        chromium-browser \
            --noerrdialogs \
            --disable-infobars \
            --kiosk \
            --incognito \
            http://localhost:5000 &
        BROWSER_PID=$!
        log_message "✅ Browser restarted with PID: $BROWSER_PID"
    fi
    
    # Проверка сервера
    if ! pgrep -f "python3.*server.py" > /dev/null; then
        log_message "⚠️ Server crashed, restarting..."
        cd "$PROJECT_DIR"
        python3 server.py >> "$PROJECT_DIR/server.log" 2>&1 &
        log_message "✅ Server restarted"
    fi
    
    # Проверка соединения с Arduino
    if [ -c "/dev/ttyUSB0" ] || [ -c "/dev/ttyACM0" ]; then
        # Arduino подключен
        if [ ! -f "/tmp/arduino_connected" ]; then
            touch "/tmp/arduino_connected"
            log_message "✅ Arduino connected"
        fi
    else
        # Arduino отключен
        if [ -f "/tmp/arduino_connected" ]; then
            rm -f "/tmp/arduino_connected"
            log_message "⚠️ Arduino disconnected"
        fi
    fi
    
    sleep 30
done &

# =========================================
# Обработка сигналов
# =========================================
cleanup() {
    log_message "🛑 Shutting down kiosk..."
    pkill -f chromium
    pkill -f "python3.*server.py"
    exit 0
}

trap cleanup SIGTERM SIGINT SIGHUP

# =========================================
# Держим скрипт запущенным
# =========================================
log_message "✅ Kiosk mode started successfully"
log_message "📝 Logs: $PROJECT_DIR/kiosk.log"
log_message "================================="

# Ждем завершения браузера
wait $BROWSER_PID
