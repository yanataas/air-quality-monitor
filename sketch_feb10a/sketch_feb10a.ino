#include <Arduino.h>
#include <SoftwareSerial.h>
#include <CSE_ZH06.h>
#include "DHT.h"

// ---------- Пылевой датчик ZH03B ----------
#define PIN_PM_RX 10
#define PIN_PM_TX 11
SoftwareSerial pmSerial(PIN_PM_RX, PIN_PM_TX);
CSE_ZH06 pmSensor(pmSerial);

// ---------- Датчик DHT11 ----------
#define DHTPIN 2
#define DHTTYPE DHT11
DHT dht(DHTPIN, DHTTYPE);

// ---------- Настройки ----------
const unsigned long SEND_INTERVAL = 10000;     // Отправка данных каждые 10 секунд
const unsigned long SAMPLE_INTERVAL = 600000;  // 10 минут для усреднения
unsigned long lastSendTime = 0;
unsigned long lastSampleTime = 0;

// Буферы для накопления данных
#define MAX_SAMPLES 60  // 10 минут * 6 сэмплов в минуту
float tempSamples[MAX_SAMPLES];
float humSamples[MAX_SAMPLES];
int pm1Samples[MAX_SAMPLES];
int pm25Samples[MAX_SAMPLES];
int pm10Samples[MAX_SAMPLES];
int sampleIndex = 0;
int sampleCount = 0;

void setup() {
  Serial.begin(115200);
  delay(2000);
  
  // Инициализация датчиков
  pmSerial.begin(9600);
  pmSensor.begin();
  dht.begin();
  
  // Инициализация буферов
  for (int i = 0; i < MAX_SAMPLES; i++) {
    tempSamples[i] = 0;
    humSamples[i] = 0;
    pm1Samples[i] = 0;
    pm25Samples[i] = 0;
    pm10Samples[i] = 0;
  }
  
  Serial.println("AIR_QUALITY_MONITOR_STARTED");
  Serial.println("SAMPLE_INTERVAL:10000ms");    // Отправка каждые 10 секунд
  Serial.println("AVERAGING_INTERVAL:600000ms"); // Усреднение каждые 10 минут
}

void loop() {
  unsigned long currentMillis = millis();
  
  // 1. Чтение данных с датчиков каждую секунду
  static unsigned long lastReadTime = 0;
  if (currentMillis - lastReadTime >= 1000) {
    lastReadTime = currentMillis;
    
    // Чтение датчика пыли
    if (pmSensor.getPmData()) {
      // Сохраняем текущие значения
      pm1Samples[sampleIndex] = pmSensor.pm1;
      pm25Samples[sampleIndex] = pmSensor.pm25;
      pm10Samples[sampleIndex] = pmSensor.pm10;
    }
    
    // Чтение датчика температуры/влажности
    float h = dht.readHumidity();
    float t = dht.readTemperature();
    
    if (!isnan(h) && !isnan(t)) {
      humSamples[sampleIndex] = h;
      tempSamples[sampleIndex] = t;
    }
    
    sampleIndex = (sampleIndex + 1) % MAX_SAMPLES;
    if (sampleCount < MAX_SAMPLES) {
      sampleCount++;
    }
  }
  
  // 2. Отправка текущих данных каждые 10 секунд
  if (currentMillis - lastSendTime >= SEND_INTERVAL) {
    lastSendTime = currentMillis;
    
    // Рассчитываем текущие средние значения
    float currentTemp = 0, currentHum = 0;
    int currentPM1 = 0, currentPM25 = 0, currentPM10 = 0;
    
    for (int i = 0; i < sampleCount; i++) {
      currentTemp += tempSamples[i];
      currentHum += humSamples[i];
      currentPM1 += pm1Samples[i];
      currentPM25 += pm25Samples[i];
      currentPM10 += pm10Samples[i];
    }
    
    if (sampleCount > 0) {
      currentTemp /= sampleCount;
      currentHum /= sampleCount;
      currentPM1 /= sampleCount;
      currentPM25 /= sampleCount;
      currentPM10 /= sampleCount;
    }
    
    // Отправка текущих данных
    sendCurrentData(currentPM1, currentPM25, currentPM10, currentTemp, currentHum);
  }
  
  // 3. Отправка 10-минутного усредненного образца
  if (currentMillis - lastSampleTime >= SAMPLE_INTERVAL) {
    lastSampleTime = currentMillis;
    
    // Рассчитываем 10-минутные средние
    float avgTemp = 0, avgHum = 0;
    int avgPM1 = 0, avgPM25 = 0, avgPM10 = 0;
    
    for (int i = 0; i < sampleCount; i++) {
      avgTemp += tempSamples[i];
      avgHum += humSamples[i];
      avgPM1 += pm1Samples[i];
      avgPM25 += pm25Samples[i];
      avgPM10 += pm10Samples[i];
    }
    
    if (sampleCount > 0) {
      avgTemp /= sampleCount;
      avgHum /= sampleCount;
      avgPM1 /= sampleCount;
      avgPM25 /= sampleCount;
      avgPM10 /= sampleCount;
    }
    
    // Отправка 10-минутного образца
    sendTenMinuteSample(avgPM1, avgPM25, avgPM10, avgTemp, avgHum, sampleCount);
    
    // Сброс счетчика
    sampleCount = 0;
    sampleIndex = 0;
  }
  
  delay(100);
}

void sendCurrentData(int pm1, int pm25, int pm10, float temp, float hum) {
  Serial.print("{");
  Serial.print("\"type\":\"current\",");
  Serial.print("\"pm1\":"); Serial.print(pm1); Serial.print(",");
  Serial.print("\"pm25\":"); Serial.print(pm25); Serial.print(",");
  Serial.print("\"pm10\":"); Serial.print(pm10); Serial.print(",");
  Serial.print("\"temperature\":"); Serial.print(temp, 1); Serial.print(",");
  Serial.print("\"humidity\":"); Serial.print(hum, 1); Serial.print(",");
  Serial.print("\"sample_count\":"); Serial.print(sampleCount);
  Serial.println("}");
}

void sendTenMinuteSample(int pm1, int pm25, int pm10, float temp, float hum, int samples) {
  Serial.print("{");
  Serial.print("\"type\":\"ten_minute_sample\",");
  Serial.print("\"pm1\":"); Serial.print(pm1); Serial.print(",");
  Serial.print("\"pm25\":"); Serial.print(pm25); Serial.print(",");
  Serial.print("\"pm10\":"); Serial.print(pm10); Serial.print(",");
  Serial.print("\"temperature\":"); Serial.print(temp, 1); Serial.print(",");
  Serial.print("\"humidity\":"); Serial.print(hum, 1); Serial.print(",");
  Serial.print("\"total_samples\":"); Serial.print(samples); Serial.print(",");
  Serial.print("\"timestamp_ms\":"); Serial.print(millis());
  Serial.println("}");
}