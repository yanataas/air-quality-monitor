#!/bin/bash
# Скрипт автоматического резервного копирования данных
# Запускается по расписанию из crontab

# =========================================
# Настройки
# =========================================
PROJECT_DIR="/home/pi/air-quality-monitor"
BACKUP_DIR="$PROJECT_DIR/backups"
DATABASE="$PROJECT_DIR/air_quality.db"
LOGS="$PROJECT_DIR/air_quality.log"
RETENTION_DAYS=30  # Храним бэкапы 30 дней
MAX_BACKUPS=50     # Максимальное количество бэкапов

# =========================================
# Создание папки для бэкапов
# =========================================
mkdir -p "$BACKUP_DIR"

# =========================================
# Функция логирования
# =========================================
log_message() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$BACKUP_DIR/backup.log"
    echo "$1"
}

# =========================================
# Проверка наличия базы данных
# =========================================
if [ ! -f "$DATABASE" ]; then
    log_message "❌ Database not found: $DATABASE"
    exit 1
fi

# =========================================
# Создание бэкапа
# =========================================
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/air_quality_$DATE"

log_message "🔄 Starting backup..."

# Бэкап базы данных
if cp "$DATABASE" "$BACKUP_FILE.db" 2>/dev/null; then
    # Сжатие
    gzip "$BACKUP_FILE.db"
    log_message "✅ Database backup created: air_quality_$DATE.db.gz"
    
    # Получаем размер файла
    SIZE=$(du -h "$BACKUP_FILE.db.gz" | cut -f1)
    log_message "📊 Backup size: $SIZE"
else
    log_message "❌ Failed to copy database"
fi

# Бэкап логов (если есть)
if [ -f "$LOGS" ]; then
    cp "$LOGS" "$BACKUP_DIR/air_quality_$DATE.log"
    gzip "$BACKUP_DIR/air_quality_$DATE.log"
    log_message "✅ Logs backup created"
fi

# =========================================
# Создание метаданных бэкапа
# =========================================
cat > "$BACKUP_DIR/backup_$DATE.info" << EOF
Backup Information
==================
Date: $(date)
Database: air_quality.db
Size: $(du -h "$BACKUP_FILE.db.gz" | cut -f1)
Records: $(sqlite3 "$DATABASE" "SELECT COUNT(*) FROM hourly_data;" 2>/dev/null || echo "N/A")
Uptime: $(uptime)
Hostname: $(hostname)
EOF

log_message "✅ Backup info created"

# =========================================
# Очистка старых бэкапов
# =========================================
log_message "🧹 Cleaning old backups..."

# Удаление по дате (старше RETENTION_DAYS)
OLD_FILES=$(find "$BACKUP_DIR" -name "air_quality_*.gz" -type f -mtime +$RETENTION_DAYS)
if [ -n "$OLD_FILES" ]; then
    echo "$OLD_FILES" | while read file; do
        rm -f "$file"
        log_message "  Removed: $(basename "$file")"
    done
fi

# Ограничение по количеству
BACKUP_COUNT=$(find "$BACKUP_DIR" -name "air_quality_*.gz" -type f | wc -l)
if [ "$BACKUP_COUNT" -gt "$MAX_BACKUPS" ]; then
    TO_DELETE=$((BACKUP_COUNT - MAX_BACKUPS))
    find "$BACKUP_DIR" -name "air_quality_*.gz" -type f -printf '%T@ %p\n' | \
        sort -n | head -n "$TO_DELETE" | cut -d' ' -f2- | while read file; do
        rm -f "$file"
        log_message "  Removed (limit): $(basename "$file")"
    done
fi

# Удаление старых info файлов
find "$BACKUP_DIR" -name "backup_*.info" -type f -mtime +$RETENTION_DAYS -delete

# =========================================
# Итог
# =========================================
CURRENT_COUNT=$(find "$BACKUP_DIR" -name "air_quality_*.gz" -type f | wc -l)
TOTAL_SIZE=$(du -sh "$BACKUP_DIR" | cut -f1)

log_message "================================="
log_message "✅ Backup completed successfully!"
log_message "📊 Total backups: $CURRENT_COUNT"
log_message "💾 Total size: $TOTAL_SIZE"
log_message "📁 Backup directory: $BACKUP_DIR"
log_message "================================="

# =========================================
# Отправка уведомления (опционально)
# =========================================
# Если нужно отправить email (требуется настроить sendmail)
# echo "Backup completed at $(date)" | mail -s "Air Quality Backup" user@example.com

# Если нужно скопировать на USB (опционально)
# USB_MOUNT="/mnt/usb"
# if mountpoint -q "$USB_MOUNT"; then
#     cp "$BACKUP_FILE.db.gz" "$USB_MOUNT/"
#     log_message "✅ Copied to USB"
# fi

exit 0
