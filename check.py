#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sqlite3
import os

print("🔍 Быстрая проверка базы данных air_quality.db")
print("="*50)

# Проверяем наличие файла
if not os.path.exists('air_quality.db'):
    print("❌ Файл air_quality.db не найден!")
    exit(1)

try:
    conn = sqlite3.connect('air_quality.db')
    cursor = conn.cursor()
    
    # 1. Какие таблицы есть
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cursor.fetchall()
    
    print(f"\n📋 Найдено таблиц: {len(tables)}")
    for table in tables:
        table_name = table[0]
        cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
        count = cursor.fetchone()[0]
        print(f"   • {table_name}: {count} записей")
    
    # 2. Проверяем данные в ten_minute_data
    print("\n⏱️  Данные в ten_minute_data:")
    try:
        cursor.execute("SELECT timestamp, pm25 FROM ten_minute_data ORDER BY timestamp DESC LIMIT 3")
        rows = cursor.fetchall()
        if rows:
            for row in rows:
                print(f"   {row[0][:19]} | PM2.5: {row[1]:.1f}")
        else:
            print("   (нет данных)")
    except:
        print("   (таблица не найдена)")
    
    # 3. Проверяем данные в sensor_data
    print("\n📡 Данные в sensor_data:")
    try:
        cursor.execute("SELECT timestamp, pm25, aqi, quality FROM sensor_data ORDER BY timestamp DESC LIMIT 3")
        rows = cursor.fetchall()
        if rows:
            for row in rows:
                print(f"   {row[0][:19]} | PM2.5: {row[1]:.1f} | AQI: {row[2]} | {row[3]}")
        else:
            print("   (нет данных)")
    except:
        print("   (таблица не найдена)")
    
    conn.close()
    
    # 4. Размер файла
    size_kb = os.path.getsize('air_quality.db') / 1024
    print(f"\n💾 Размер базы данных: {size_kb:.1f} KB")
    
    print("\n✅ Проверка завершена!")
    
except Exception as e:
    print(f"❌ Ошибка: {e}")