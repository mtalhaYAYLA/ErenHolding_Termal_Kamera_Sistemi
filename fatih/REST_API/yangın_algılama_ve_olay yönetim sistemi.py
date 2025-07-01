# main.py

import asyncio
import time
import json
import os
import uuid
from datetime import datetime
import requests
from fastapi import FastAPI, BackgroundTasks
from requests.auth import HTTPDigestAuth

import camera_handler
from config import (
    CAMERA_USER, CAMERA_PASS, REALTIME_THERMOMETRY_URL,
    RTSP_URL_NORMAL, RTSP_URL_THERMAL, ALARM_TEMPERATURE
)

app = FastAPI(title="Termal Anomali Tespit Servisi")
auth = HTTPDigestAuth(CAMERA_USER, CAMERA_PASS)

# Olaylar arasında bekleme süresi (spam'i önlemek için)
last_event_time = 0
EVENT_COOLDOWN_SECONDS = 60 # 60 saniyede bir olay oluştur

@app.get("/status")
def get_status():
    """Servisin ayakta olup olmadığını kontrol etmek için basit bir endpoint."""
    return {"status": "running"}

def create_event(thermal_data: dict):
    """
    Anomali tespit edildiğinde tetiklenir. Gerekli tüm verileri toplar ve kaydeder.
    """
    global last_event_time
    
    # Cooldown kontrolü
    if time.time() - last_event_time < EVENT_COOLDOWN_SECONDS:
        print("Cooldown aktif, yeni olay oluşturulmuyor.")
        return

    print("!!! ANOMALİ TESPİT EDİLDİ! Olay oluşturuluyor... !!!")
    
    # Olay için benzersiz bir kimlik ve zaman damgası oluştur
    event_id = str(uuid.uuid4())
    timestamp = datetime.now()
    event_folder = f"events/{timestamp.strftime('%Y-%m-%d')}_{event_id[:8]}"
    os.makedirs(event_folder, exist_ok=True)
    
    # 1. PTZ Pozisyonunu al
    ptz_status = camera_handler.get_ptz_status()
    
    # 2. Termal ve Normal Görüntüleri Yakala
    thermal_image_bytes = camera_handler.capture_snapshot(RTSP_URL_THERMAL)
    normal_image_bytes = camera_handler.capture_snapshot(RTSP_URL_NORMAL)
    
    # 3. Verileri Dosyalara Kaydet
    event_data = {
        "event_id": event_id,
        "timestamp": timestamp.isoformat(),
        "triggering_thermal_data": thermal_data,
        "ptz_position": ptz_status,
        "alarm_temperature_celsius": ALARM_TEMPERATURE
    }
    
    with open(os.path.join(event_folder, "data.json"), "w") as f:
        json.dump(event_data, f, indent=4)
        
    if thermal_image_bytes:
        with open(os.path.join(event_folder, "thermal_image.jpg"), "wb") as f:
            f.write(thermal_image_bytes)
            
    if normal_image_bytes:
        with open(os.path.join(event_folder, "normal_image.jpg"), "wb") as f:
            f.write(normal_image_bytes)
            
    print(f"Olay başarıyla kaydedildi: {event_folder}")
    last_event_time = time.time()
    # BURAYA EK BİLDİRİM KODLARI GELEBİLİR (E-posta, SMS, vs.)


async def listen_for_thermal_anomalies(background_tasks: BackgroundTasks):
    """
    Kameranın termal veri akışını sürekli dinler ve anomali arar.
    Bu fonksiyon arka planda sürekli çalışacak.
    """
    print("Termal anomali dinleyicisi başlatılıyor...")
    while True:
        try:
            with requests.get(REALTIME_THERMOMETRY_URL, auth=auth, stream=True, timeout=(5, 65)) as response:
                if response.status_code != 200:
                    print(f"Termal veri akışı hatası: {response.status_code}")
                    await asyncio.sleep(5)
                    continue

                buffer = b''
                for chunk in response.iter_content(chunk_size=1024):
                    buffer += chunk
                    # Kameradan gelen multipart/x-mixed-replace verisini parçala
                    while b'--boundary' in buffer:
                        parts = buffer.split(b'--boundary', 1)
                        block, buffer = parts[0], parts[1]
                        if b'Content-Type: application/json' in block:
                            json_start = block.find(b'{')
                            json_end = block.rfind(b'}')
                            if json_start != -1 and json_end != -1:
                                json_str = block[json_start:json_end+1].decode('utf-8')
                                try:
                                    data = json.loads(json_str)
                                    # ANOMALİ KONTROL MANTIĞI BURADA
                                    max_temp = data.get('ThermometryUploadList', {}).get('ThermometryUpload', [{}])[0].get('LinePolygonThermCfg', {}).get('MaxTemperature')
                                    if max_temp and max_temp >= ALARM_TEMPERATURE:
                                        # ÖNEMLİ: create_event'i doğrudan çağırmak yerine
                                        # background task olarak ekliyoruz ki ana döngü bloklanmasın.
                                        background_tasks.add_task(create_event, data)
                                except json.JSONDecodeError:
                                    pass
        except requests.exceptions.RequestException as e:
            print(f"Termal veri bağlantı hatası: {e}")
            await asyncio.sleep(5) # Hata durumunda 5 saniye bekle ve tekrar dene

@app.on_event("startup")
async def startup_event(background_tasks: BackgroundTasks):
    """Uygulama başladığında termal dinleyiciyi arka planda başlat."""
    background_tasks.add_task(listen_for_thermal_anomalies, background_tasks)