#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Air Quality Monitor - Long-term Storage Version
Хранение данных до 1 года и более
"""

import serial
import json
import time
import os
import glob
from datetime import datetime, timedelta
from flask import Flask, render_template, jsonify, send_file, request
from flask_socketio import SocketIO
from collections import deque
import threading
import sqlite3
import pandas as pd
import numpy as np
from io import BytesIO
import logging
from logging.handlers import RotatingFileHandler
import gzip
import shutil

# Настройка логирования
log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
log_file = 'air_quality.log'
log_handler = RotatingFileHandler(log_file, maxBytes=10*1024*1024, backupCount=10)
log_handler.setFormatter(log_formatter)

logger = logging.getLogger('air_quality')
logger.setLevel(logging.INFO)
logger.addHandler(log_handler)
console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)
logger.addHandler(console_handler)

app = Flask(__name__)
app.config['SECRET_KEY'] = 'air-quality-secret-local'
app.config['DATA_FOLDER'] = 'data'
app.config['DATABASE'] = 'air_quality.db'
app.config['ARCHIVE_FOLDER'] = 'archive'
app.config['MAX_HISTORY_DAYS'] = 400  # Больше года
socketio = SocketIO(app, cors_allowed_origins="*", logger=False, engineio_logger=False)

# Создаем папки
os.makedirs(app.config['DATA_FOLDER'], exist_ok=True)
os.makedirs(app.config['ARCHIVE_FOLDER'], exist_ok=True)

# Очередь для хранения данных
data_history = deque(maxlen=8760)  # Весь год (24*365)
current_data = {}

system_info = {
    'start_time': datetime.now(),
    'total_raw_samples': 0,
    'total_hourly_samples': 0,
    'errors': 0,
    'database_size_mb': 0
}

def find_arduino_port():
    """Автоматическое определение порта Arduino"""
    possible_ports = []
    possible_ports += glob.glob('/dev/ttyACM*')
    possible_ports += glob.glob('/dev/ttyUSB*')
    possible_ports += glob.glob('/dev/serial/by-id/*')
    possible_ports += glob.glob('/dev/cu.usbmodem*')
    possible_ports += glob.glob('/dev/cu.usbserial*')
    possible_ports += [f'COM{i}' for i in range(1, 10)]
    
    for port in possible_ports:
        try:
            if os.path.exists(port):
                ser = serial.Serial(port, 115200, timeout=2)
                time.sleep(2)
                ser.close()
                logger.info(f"✅ Found Arduino on {port}")
                return port
        except:
            continue
    
    logger.warning("❌ Arduino not found")
    return None

SERIAL_PORT = find_arduino_port()
BAUD_RATE = 115200
SAMPLE_INTERVAL = 3600  # 1 час

# Переменные для накопления данных
accumulated_data = []
last_sample_time = None
accumulation_start_time = time.time()
arduino_connected = False

def init_database():
    """Инициализация базы данных с оптимизацией для больших данных"""
    try:
        conn = sqlite3.connect(app.config['DATABASE'])
        c = conn.cursor()
        
        # Часовые данные (основная таблица)
        c.execute('''CREATE TABLE IF NOT EXISTS hourly_data
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      timestamp DATETIME NOT NULL,
                      pm1 REAL,
                      pm25 REAL,
                      pm10 REAL,
                      temperature REAL,
                      humidity REAL,
                      aqi INTEGER,
                      quality TEXT,
                      sample_count INTEGER)''')
        
        # Индекс для быстрого поиска по дате
        c.execute('''CREATE INDEX IF NOT EXISTS idx_hourly_timestamp 
                     ON hourly_data(timestamp DESC)''')
        
        # Таблица для сырых данных (с партиционированием по месяцам)
        c.execute('''CREATE TABLE IF NOT EXISTS raw_data
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                      pm1 REAL,
                      pm25 REAL,
                      pm10 REAL,
                      temperature REAL,
                      humidity REAL)''')
        
        c.execute('''CREATE INDEX IF NOT EXISTS idx_raw_timestamp 
                     ON raw_data(timestamp)''')
        
        # Таблица для ежедневной статистики
        c.execute('''CREATE TABLE IF NOT EXISTS daily_stats
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      date DATE UNIQUE,
                      avg_pm25 REAL,
                      max_pm25 REAL,
                      min_pm25 REAL,
                      avg_temp REAL,
                      avg_humidity REAL,
                      samples_count INTEGER,
                      good_hours INTEGER,
                      moderate_hours INTEGER,
                      unhealthy_hours INTEGER)''')
        
        # Таблица метаданных
        c.execute('''CREATE TABLE IF NOT EXISTS experiment_meta
                     (id INTEGER PRIMARY KEY,
                      experiment_name TEXT DEFAULT 'Long-term Air Quality Study',
                      start_time DATETIME,
                      last_update DATETIME,
                      total_hours INTEGER DEFAULT 0,
                      total_raw_samples INTEGER DEFAULT 0,
                      database_size INTEGER DEFAULT 0,
                      sampling_interval INTEGER DEFAULT 3600,
                      device_name TEXT DEFAULT 'Raspberry Pi',
                      location TEXT DEFAULT 'University Lab',
                      notes TEXT)''')
        
        # Таблица для системных логов
        c.execute('''CREATE TABLE IF NOT EXISTS system_logs
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                      event_type TEXT,
                      message TEXT)''')
        
        # Создаем представление для последних 7 дней (для быстрого доступа)
        c.execute('''CREATE VIEW IF NOT EXISTS last_7_days AS
                     SELECT * FROM hourly_data 
                     WHERE timestamp >= datetime('now', '-7 days')
                     ORDER BY timestamp DESC''')
        
        conn.commit()
        
        # Проверяем, есть ли запись в experiment_meta
        c.execute("SELECT COUNT(*) FROM experiment_meta")
        if c.fetchone()[0] == 0:
            c.execute('''INSERT INTO experiment_meta 
                         (id, start_time, last_update, device_name) 
                         VALUES (1, ?, ?, ?)''',
                      (datetime.now().isoformat(), 
                       datetime.now().isoformat(),
                       'Raspberry Pi'))
            conn.commit()
        
        conn.close()
        logger.info("✅ Database initialized for long-term storage")
        
    except Exception as e:
        logger.error(f"❌ Database initialization error: {e}")

def calculate_daily_stats():
    """Расчет ежедневной статистики"""
    try:
        conn = sqlite3.connect(app.config['DATABASE'])
        c = conn.cursor()
        
        # Получаем последнюю дату без статистики
        c.execute('''SELECT date FROM daily_stats 
                     ORDER BY date DESC LIMIT 1''')
        last_date = c.fetchone()
        
        if last_date:
            start_date = datetime.strptime(last_date[0], '%Y-%m-%d') + timedelta(days=1)
        else:
            start_date = datetime.now() - timedelta(days=7)  # Начинаем с недели назад
        
        end_date = datetime.now()
        
        while start_date < end_date:
            date_str = start_date.strftime('%Y-%m-%d')
            next_date = (start_date + timedelta(days=1)).strftime('%Y-%m-%d')
            
            # Считаем статистику за день
            c.execute('''SELECT 
                            AVG(pm25) as avg_pm25,
                            MAX(pm25) as max_pm25,
                            MIN(pm25) as min_pm25,
                            AVG(temperature) as avg_temp,
                            AVG(humidity) as avg_humidity,
                            COUNT(*) as samples_count,
                            SUM(CASE WHEN aqi <= 50 THEN 1 ELSE 0 END) as good_hours,
                            SUM(CASE WHEN aqi > 50 AND aqi <= 100 THEN 1 ELSE 0 END) as moderate_hours,
                            SUM(CASE WHEN aqi > 100 THEN 1 ELSE 0 END) as unhealthy_hours
                        FROM hourly_data 
                        WHERE timestamp >= ? AND timestamp < ?''',
                       (date_str, next_date))
            
            stats = c.fetchone()
            
            if stats[0]:  # Если есть данные за день
                c.execute('''INSERT OR REPLACE INTO daily_stats 
                             (date, avg_pm25, max_pm25, min_pm25, avg_temp, 
                              avg_humidity, samples_count, good_hours, moderate_hours, unhealthy_hours)
                             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                          (date_str, stats[0], stats[1], stats[2], stats[3],
                           stats[4], stats[5], stats[6], stats[7], stats[8]))
                logger.info(f"📊 Daily stats calculated for {date_str}")
            
            start_date += timedelta(days=1)
        
        conn.commit()
        conn.close()
        
    except Exception as e:
        logger.error(f"Error calculating daily stats: {e}")

def archive_old_data():
    """Архивация старых данных (старше 6 месяцев)"""
    try:
        archive_date = (datetime.now() - timedelta(days=180)).isoformat()
        
        conn = sqlite3.connect(app.config['DATABASE'])
        c = conn.cursor()
        
        # Получаем старые данные
        c.execute('''SELECT * FROM hourly_data 
                     WHERE timestamp < ?''', (archive_date,))
        old_data = c.fetchall()
        
        if old_data:
            # Сохраняем в архивный файл
            archive_file = f"archive/hourly_data_{datetime.now().strftime('%Y%m')}.csv"
            df = pd.read_sql_query('''SELECT * FROM hourly_data 
                                       WHERE timestamp < ?''', conn, params=(archive_date,))
            df.to_csv(archive_file, index=False)
            
            # Сжимаем
            with open(archive_file, 'rb') as f_in:
                with gzip.open(archive_file + '.gz', 'wb') as f_out:
                    shutil.copyfileobj(f_in, f_out)
            os.remove(archive_file)
            
            # Удаляем из основной базы
            c.execute('''DELETE FROM hourly_data 
                         WHERE timestamp < ?''', (archive_date,))
            conn.commit()
            
            logger.info(f"📦 Archived {len(old_data)} records to {archive_file}.gz")
        
        conn.close()
        
    except Exception as e:
        logger.error(f"Archive error: {e}")

def save_raw_data(data):
    """Сохранение сырых данных (с ограничением)"""
    try:
        # Сохраняем только каждый 10-й сырой семпл для экономии места
        if system_info['total_raw_samples'] % 10 == 0:
            conn = sqlite3.connect(app.config['DATABASE'])
            c = conn.cursor()
            c.execute('''INSERT INTO raw_data 
                         (pm1, pm25, pm10, temperature, humidity)
                         VALUES (?, ?, ?, ?, ?)''',
                      (data.get('pm1', 0),
                       data.get('pm25', 0),
                       data.get('pm10', 0),
                       data.get('temperature', 0),
                       data.get('humidity', 0)))
            conn.commit()
            conn.close()
        
        system_info['total_raw_samples'] += 1
        
    except Exception as e:
        logger.error(f"Error saving raw data: {e}")

def save_hourly_sample(sample_data):
    """Сохранение часового образца"""
    try:
        aqi = calculate_aqi(sample_data['pm25_avg'])
        quality = get_aqi_level(aqi)
        
        conn = sqlite3.connect(app.config['DATABASE'])
        c = conn.cursor()
        
        c.execute('''INSERT INTO hourly_data 
                     (timestamp, pm1, pm25, pm10, temperature, humidity, aqi, quality, sample_count)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                  (sample_data['timestamp'],
                   round(sample_data['pm1_avg'], 2),
                   round(sample_data['pm25_avg'], 2),
                   round(sample_data['pm10_avg'], 2),
                   round(sample_data['temp_avg'], 2),
                   round(sample_data['hum_avg'], 2),
                   aqi,
                   quality,
                   sample_data['sample_count']))
        
        # Обновляем метаданные
        c.execute('''UPDATE experiment_meta 
                     SET total_hours = total_hours + 1,
                         total_raw_samples = total_raw_samples + ?,
                         last_update = ?,
                         database_size = ?
                     WHERE id = 1''',
                  (sample_data['sample_count'], 
                   datetime.now().isoformat(),
                   os.path.getsize(app.config['DATABASE'])))
        
        conn.commit()
        conn.close()
        
        system_info['total_hourly_samples'] += 1
        system_info['total_raw_samples'] += sample_data['sample_count']
        
        # Обновляем размер БД
        system_info['database_size_mb'] = os.path.getsize(app.config['DATABASE']) / (1024*1024)
        
        logger.info(f"✅ Hourly sample #{system_info['total_hourly_samples']} saved: PM2.5={sample_data['pm25_avg']:.1f}")
        
        # Рассчитываем дневную статистику каждый день в полночь
        if datetime.now().hour == 0 and datetime.now().minute < 5:
            calculate_daily_stats()
        
        # Архивируем раз в месяц
        if system_info['total_hourly_samples'] % 720 == 0:  # ~30 дней
            archive_old_data()
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Error saving sample: {e}")
        system_info['errors'] += 1
        return False

def calculate_aqi(pm25):
    """Расчет AQI"""
    if pm25 is None or pm25 < 0:
        return 0
    try:
        if pm25 <= 12.0:
            return int((50.0 / 12.0) * pm25)
        elif pm25 <= 35.4:
            return int(50 + (50.0 / 23.4) * (pm25 - 12.1))
        elif pm25 <= 55.4:
            return int(100 + (50.0 / 20.0) * (pm25 - 35.5))
        elif pm25 <= 150.4:
            return int(150 + (50.0 / 94.9) * (pm25 - 55.5))
        elif pm25 <= 250.4:
            return int(200 + (100.0 / 99.9) * (pm25 - 150.5))
        else:
            return int(300 + (200.0 / 249.9) * (min(pm25, 500.4) - 250.5))
    except:
        return 0

def get_aqi_level(aqi):
    """Определение уровня AQI"""
    if aqi <= 50:
        return "Good"
    elif aqi <= 100:
        return "Moderate"
    elif aqi <= 150:
        return "Unhealthy for Sensitive Groups"
    elif aqi <= 200:
        return "Unhealthy"
    elif aqi <= 300:
        return "Very Unhealthy"
    else:
        return "Hazardous"

def process_accumulated_data():
    """Обработка накопленных данных"""
    global accumulated_data, accumulation_start_time
    
    if not accumulated_data:
        return None
    
    sample_count = len(accumulated_data)
    valid_data = [d for d in accumulated_data if d.get('pm25', 0) > 0]
    
    if not valid_data:
        return None
    
    pm1_sum = sum(d.get('pm1', 0) for d in valid_data)
    pm25_sum = sum(d.get('pm25', 0) for d in valid_data)
    pm10_sum = sum(d.get('pm10', 0) for d in valid_data)
    temp_sum = sum(d.get('temperature', 0) for d in valid_data)
    hum_sum = sum(d.get('humidity', 0) for d in valid_data)
    
    valid_count = len(valid_data)
    
    sample_data = {
        'timestamp': datetime.now().isoformat(),
        'pm1_avg': pm1_sum / valid_count if valid_count > 0 else 0,
        'pm25_avg': pm25_sum / valid_count if valid_count > 0 else 0,
        'pm10_avg': pm10_sum / valid_count if valid_count > 0 else 0,
        'temp_avg': temp_sum / valid_count if valid_count > 0 else 0,
        'hum_avg': hum_sum / valid_count if valid_count > 0 else 0,
        'sample_count': sample_count,
        'valid_count': valid_count
    }
    
    accumulated_data = []
    accumulation_start_time = time.time()
    
    return sample_data

def read_serial_data():
    """Чтение данных с Arduino"""
    global current_data, accumulated_data, last_sample_time, accumulation_start_time
    global arduino_connected, SERIAL_PORT
    
    while True:
        try:
            if not SERIAL_PORT:
                SERIAL_PORT = find_arduino_port()
                if not SERIAL_PORT:
                    time.sleep(10)
                    continue
            
            ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=2)
            arduino_connected = True
            logger.info(f"✅ Connected to Arduino")
            
            time.sleep(2)
            ser.reset_input_buffer()
            
            raw_data_count = 0
            
            while True:
                try:
                    if ser.in_waiting > 0:
                        line = ser.readline().decode('utf-8', errors='ignore').strip()
                        
                        if line and line.startswith('{') and line.endswith('}'):
                            try:
                                data = json.loads(line)
                                raw_data_count += 1
                                
                                if data.get('pm25', 0) > 0:
                                    accumulated_data.append(data)
                                    save_raw_data(data)  # Сохраняем сырые данные
                                
                                current_data = {
                                    'pm1': data.get('pm1', 0),
                                    'pm25': data.get('pm25', 0),
                                    'pm10': data.get('pm10', 0),
                                    'temperature': data.get('temperature', 0),
                                    'humidity': data.get('humidity', 0),
                                    'timestamp': datetime.now().isoformat(),
                                    'time': datetime.now().strftime('%H:%M:%S'),
                                    'date': datetime.now().strftime('%Y-%m-%d'),
                                    'raw_count': system_info['total_raw_samples'],
                                    'accumulated': len(accumulated_data),
                                    'status': 'connected'
                                }
                                
                                socketio.emit('sensor_data', current_data)
                                
                                # Проверяем, не прошёл ли час
                                current_time = time.time()
                                if current_time - accumulation_start_time >= SAMPLE_INTERVAL:
                                    sample_data = process_accumulated_data()
                                    if sample_data:
                                        if save_hourly_sample(sample_data):
                                            aqi = calculate_aqi(sample_data['pm25_avg'])
                                            quality = get_aqi_level(aqi)
                                            
                                            dashboard_data = {
                                                'pm1': round(sample_data['pm1_avg'], 1),
                                                'pm25': round(sample_data['pm25_avg'], 1),
                                                'pm10': round(sample_data['pm10_avg'], 1),
                                                'temperature': round(sample_data['temp_avg'], 1),
                                                'humidity': round(sample_data['hum_avg'], 1),
                                                'aqi': aqi,
                                                'quality': quality,
                                                'timestamp': sample_data['timestamp'],
                                                'time': datetime.now().strftime('%H:%M:%S'),
                                                'date': datetime.now().strftime('%Y-%m-%d'),
                                                'sample_count': sample_data['sample_count'],
                                                'total_hours': system_info['total_hourly_samples']
                                            }
                                            
                                            data_history.append(dashboard_data)
                                            socketio.emit('hourly_sample', dashboard_data)
                                    
                                    last_sample_time = current_time
                                
                            except Exception as e:
                                logger.debug(f"Data parse error: {e}")
                    
                    time.sleep(0.1)
                    
                except serial.SerialException:
                    break
                except Exception as e:
                    logger.error(f"Error: {e}")
                    break
            
            ser.close()
            arduino_connected = False
            logger.warning("🔄 Connection lost")
            time.sleep(5)
            
        except Exception as e:
            logger.error(f"Connection error: {e}")
            time.sleep(10)

# ========== Flask Routes ==========

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/hourly_samples')
def get_hourly_samples():
    """Получение часовых данных с поддержкой пагинации"""
    try:
        days = request.args.get('days', default=7, type=int)
        limit = request.args.get('limit', default=168, type=int)
        
        conn = sqlite3.connect(app.config['DATABASE'])
        conn.row_factory = sqlite3.Row
        
        if days:
            query = """
            SELECT * FROM hourly_data 
            WHERE timestamp >= datetime('now', '-' || ? || ' days')
            ORDER BY timestamp DESC
            LIMIT ?
            """
            c = conn.cursor()
            c.execute(query, (days, limit))
        else:
            query = "SELECT * FROM hourly_data ORDER BY timestamp DESC LIMIT ?"
            c = conn.cursor()
            c.execute(query, (limit,))
        
        rows = c.fetchall()
        data = [dict(row) for row in rows]
        conn.close()
        
        return jsonify(data)
        
    except Exception as e:
        logger.error(f"API error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/daily_stats')
def get_daily_stats():
    """Получение ежедневной статистики"""
    try:
        days = request.args.get('days', default=30, type=int)
        
        conn = sqlite3.connect(app.config['DATABASE'])
        conn.row_factory = sqlite3.Row
        
        query = """
        SELECT * FROM daily_stats 
        WHERE date >= date('now', '-' || ? || ' days')
        ORDER BY date DESC
        """
        
        c = conn.cursor()
        c.execute(query, (days,))
        rows = c.fetchall()
        data = [dict(row) for row in rows]
        conn.close()
        
        return jsonify(data)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/statistics')
def get_statistics():
    """Полная статистика системы"""
    try:
        conn = sqlite3.connect(app.config['DATABASE'])
        
        # Общая статистика
        total_query = """
        SELECT 
            COUNT(*) as total_hours,
            MIN(timestamp) as first_sample,
            MAX(timestamp) as last_sample,
            AVG(pm25) as avg_pm25,
            MAX(pm25) as max_pm25,
            MIN(pm25) as min_pm25,
            AVG(temperature) as avg_temp,
            AVG(humidity) as avg_humidity,
            AVG(aqi) as avg_aqi
        FROM hourly_data
        """
        
        df_total = pd.read_sql_query(total_query, conn)
        
        # Статистика за последние 24 часа
        last24_query = """
        SELECT * FROM hourly_data 
        WHERE timestamp >= datetime('now', '-1 day')
        ORDER BY timestamp
        """
        
        df_24h = pd.read_sql_query(last24_query, conn)
        
        # Статистика по качеству воздуха
        quality_query = """
        SELECT 
            quality,
            COUNT(*) as count,
            AVG(pm25) as avg_pm25
        FROM hourly_data
        GROUP BY quality
        """
        
        df_quality = pd.read_sql_query(quality_query, conn)
        
        conn.close()
        
        # Информация о системе
        uptime = datetime.now() - system_info['start_time']
        
        # Размер базы данных
        db_size = os.path.getsize(app.config['DATABASE']) if os.path.exists(app.config['DATABASE']) else 0
        db_size_mb = db_size / (1024 * 1024)
        
        # Прогноз заполнения на год
        days_running = (datetime.now() - system_info['start_time']).days
        if days_running > 0:
            daily_growth = db_size_mb / max(days_running, 1)
            year_size = daily_growth * 365
        else:
            daily_growth = 0
            year_size = 0
        
        return jsonify({
            'statistics': df_total.to_dict('records')[0] if not df_total.empty else {},
            'last_24h': df_24h.to_dict('records'),
            'quality_breakdown': df_quality.to_dict('records'),
            'system_info': {
                'uptime': str(uptime).split('.')[0],
                'total_hours': system_info['total_hourly_samples'],
                'total_raw': system_info['total_raw_samples'],
                'errors': system_info['errors'],
                'arduino_connected': arduino_connected,
                'database_size_mb': round(db_size_mb, 2),
                'daily_growth_mb': round(daily_growth, 3),
                'year_estimate_mb': round(year_size, 2),
                'records_count': system_info['total_hourly_samples']
            },
            'accumulation': {
                'progress': (time.time() - accumulation_start_time) / SAMPLE_INTERVAL * 100,
                'samples_collected': len(accumulated_data)
            }
        })
        
    except Exception as e:
        logger.error(f"Statistics error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/export/<period>')
def export_data(period):
    """Экспорт данных за период"""
    try:
        periods = {
            'day': '-1 day',
            'week': '-7 days',
            'month': '-1 month',
            'year': '-1 year',
            'all': '-100 years'
        }
        
        if period not in periods:
            period = 'week'
        
        conn = sqlite3.connect(app.config['DATABASE'])
        
        query = f"""
        SELECT * FROM hourly_data 
        WHERE timestamp >= datetime('now', '{periods[period]}')
        ORDER BY timestamp
        """
        
        df = pd.read_sql_query(query, conn)
        conn.close()
        
        output = BytesIO()
        df.to_csv(output, index=False)
        output.seek(0)
        
        filename = f"air_quality_{period}_{datetime.now().strftime('%Y%m%d')}.csv"
        
        return send_file(
            output,
            mimetype='text/csv',
            as_attachment=True,
            download_name=filename
        )
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/export/raw')
def export_raw_data():
    """Экспорт сырых данных (осторожно, может быть большой файл!)"""
    try:
        limit = request.args.get('limit', default=10000, type=int)
        
        conn = sqlite3.connect(app.config['DATABASE'])
        
        query = f"""
        SELECT * FROM raw_data 
        ORDER BY timestamp DESC
        LIMIT {limit}
        """
        
        df = pd.read_sql_query(query, conn)
        conn.close()
        
        output = BytesIO()
        df.to_csv(output, index=False)
        output.seek(0)
        
        filename = f"raw_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        
        return send_file(
            output,
            mimetype='text/csv',
            as_attachment=True,
            download_name=filename
        )
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/db_info')
def db_info():
    """Информация о базе данных"""
    try:
        conn = sqlite3.connect(app.config['DATABASE'])
        c = conn.cursor()
        
        # Количество записей
        c.execute("SELECT COUNT(*) FROM hourly_data")
        hourly_count = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM raw_data")
        raw_count = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM daily_stats")
        daily_count = c.fetchone()[0]
        
        # Диапазон дат
        c.execute("SELECT MIN(timestamp), MAX(timestamp) FROM hourly_data")
        first_date, last_date = c.fetchone()
        
        # Размер таблиц
        c.execute("SELECT page_count * page_size as size FROM pragma_page_count(), pragma_page_size()")
        db_size = c.fetchone()[0] / (1024*1024)
        
        conn.close()
        
        # Файлы архива
        archive_files = []
        if os.path.exists('archive'):
            archive_files = [f for f in os.listdir('archive') if f.endswith('.gz')]
        
        return jsonify({
            'hourly_records': hourly_count,
            'raw_records': raw_count,
            'daily_stats': daily_count,
            'first_record': first_date,
            'last_record': last_date,
            'database_size_mb': round(db_size, 2),
            'archive_files': len(archive_files),
            'archive_size_mb': round(sum(os.path.getsize(f'archive/{f}') for f in archive_files) / (1024*1024), 2) if archive_files else 0,
            'days_of_data': (datetime.now() - datetime.fromisoformat(first_date)).days if first_date else 0,
            'estimated_year_size_mb': round(db_size / max((datetime.now() - datetime.fromisoformat(first_date)).days, 1) * 365, 2) if first_date else 0
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/current_progress')
def get_current_progress():
    """Текущий прогресс"""
    progress = (time.time() - accumulation_start_time) / SAMPLE_INTERVAL * 100
    remaining = max(0, SAMPLE_INTERVAL - (time.time() - accumulation_start_time))
    
    return jsonify({
        'progress': min(100, progress),
        'remaining': int(remaining),
        'samples_collected': len(accumulated_data),
        'arduino_connected': arduino_connected,
        'total_hours': system_info['total_hourly_samples']
    })

@app.route('/api/maintenance/archive')
def manual_archive():
    """Ручной запуск архивации"""
    try:
        archive_old_data()
        return jsonify({'success': True, 'message': 'Archive completed'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/maintenance/stats')
def manual_stats():
    """Ручной расчет статистики"""
    try:
        calculate_daily_stats()
        return jsonify({'success': True, 'message': 'Stats calculated'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    print("\n" + "="*70)
    print("🌡️  AIR QUALITY MONITOR - LONG-TERM STORAGE")
    print("="*70)
    print(f"📁 Data folder: {os.path.abspath(app.config['DATA_FOLDER'])}")
    print(f"💾 Database: {app.config['DATABASE']}")
    print(f"📦 Archive: {app.config['ARCHIVE_FOLDER']}")
    print(f"⏱️  Sampling: 1 HOUR")
    print(f"📊 Storage: UP TO 1 YEAR+")
    print(f"🔌 Arduino: {SERIAL_PORT or 'Auto-detecting...'}")
    print("="*70)
    print("🚀 System ready for long-term monitoring")
    print("="*70 + "\n")
    
    # Инициализация
    init_database()
    
    # Запуск потока
    serial_thread = threading.Thread(target=read_serial_data, daemon=True)
    serial_thread.start()
    
    # Запуск сервера
    try:
        socketio.run(app, host='0.0.0.0', port=5000, debug=False, allow_unsafe_werkzeug=True)
    except KeyboardInterrupt:
        logger.info("🛑 System stopped")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
