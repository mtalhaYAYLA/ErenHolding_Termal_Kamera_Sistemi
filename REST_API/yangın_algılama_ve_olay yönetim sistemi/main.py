# main.py

import sys
import os
import asyncio
import time
import json
import uuid
from datetime import datetime
import requests
from contextlib import asynccontextmanager
from fastapi import FastAPI
from requests.auth import HTTPDigestAuth

# Proje klasörünü Python'un modül arama yoluna ekle (güvenlik için)
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Yerel modüllerimizi import ediyoruz
import camera_handler
from config import (
    CAMERA_USER, CAMERA_PASS, REALTIME_THERMOMETRY_URL,
    RTSP_URL_NORMAL, RTSP_URL_THERMAL, ALARM_TEMPERATURE, EVENT_COOLDOWN_SECONDS
)

# Paylaşılan değişkenler
auth = HTTPDigestAuth(CAMERA_USER, CAMERA_PASS)
last_event_time = 0
is_processing_event = False

def create_and_save_event(thermal_data: dict):
    """Anomali tespit edildiğinde tetiklenir. Gerekli tüm verileri toplar ve kaydeder."""
    global last_event_time, is_processing_event

    current_time = time.time()
    if current_time - last_event_time < EVENT_COOLDOWN_SECONDS:
        print(f"Cooldown aktif. {int(EVENT_COOLDOWN_SECONDS - (current_time - last_event_time))} saniye sonra tekrar denenebilir.")
        return
    
    is_processing_event = True
    print("="*50)
    print("!!! ANOMALİ TESPİT EDİLDİ! Olay oluşturuluyor... !!!")
    
    try:
        event_id = str(uuid.uuid4())
        timestamp = datetime.now()
        event_folder = os.path.join("events", f"{timestamp.strftime('%Y-%m-%d_%H-%M-%S')}_{event_id[:8]}")
        os.makedirs(event_folder, exist_ok=True)
        
        print(f"Olay ID: {event_id}\nKayıt Klasörü: {event_folder}")
        
        print("PTZ pozisyonu alınıyor...")
        ptz_status = camera_handler.get_ptz_status()
        
        print("Termal görüntü yakalanıyor...")
        thermal_image_bytes = camera_handler.capture_snapshot(RTSP_URL_THERMAL)
        print("Normal görüntü yakalanıyor...")
        normal_image_bytes = camera_handler.capture_snapshot(RTSP_URL_NORMAL)
        
        print("Veriler dosyalanıyor...")
        max_temp_info = thermal_data.get('ThermometryUploadList', {}).get('ThermometryUpload', [{}])[0]
        event_data = {
            "event_id": event_id, "timestamp_utc": timestamp.utcnow().isoformat() + "Z",
            "triggering_thermal_data": max_temp_info, "ptz_position_at_event": ptz_status,
            "alarm_config": {"set_temperature_celsius": ALARM_TEMPERATURE, "cooldown_seconds": EVENT_COOLDOWN_SECONDS},
            "files": {
                "thermal_image": "thermal_image.jpg" if thermal_image_bytes else None,
                "normal_image": "normal_image.jpg" if normal_image_bytes else None,
                "event_data": "data.json"
            }
        }
        
        with open(os.path.join(event_folder, "data.json"), "w", encoding="utf-8") as f:
            json.dump(event_data, f, indent=4, ensure_ascii=False)
        if thermal_image_bytes:
            with open(os.path.join(event_folder, "thermal_image.jpg"), "wb") as f: f.write(thermal_image_bytes)
        if normal_image_bytes:
            with open(os.path.join(event_folder, "normal_image.jpg"), "wb") as f: f.write(normal_image_bytes)
                
        print("Olay başarıyla kaydedildi!")
        print("="*50)
        last_event_time = time.time()
    except Exception as e:
        print(f"HATA: Olay oluşturulurken kritik bir hata oluştu: {e}")
    finally:
        is_processing_event = False

async def listen_for_thermal_anomalies():
    """Kameranın termal veri akışını sürekli dinler ve anomali arar."""
    print("Termal anomali dinleyicisi başlatılıyor...")
    while True:
        try:
            with requests.get(REALTIME_THERMOMETRY_URL, auth=auth, stream=True, timeout=(10, 65)) as response:
                response.raise_for_status()
                print("Termal veri akışına başarıyla bağlandı. Anomali bekleniyor...")
                buffer = b''
                for chunk in response.iter_content(chunk_size=1024):
                    if not chunk: continue
                    buffer += chunk
                    while b'--boundary' in buffer:
                        parts = buffer.split(b'--boundary', 1)
                        block, buffer = parts[0], parts[1]
                        if b'Content-Type: application/json' in block:
                            json_start = block.find(b'{'); json_end = block.rfind(b'}')
                            if json_start != -1 and json_end != -1:
                                try:
                                    data = json.loads(block[json_start:json_end+1].decode('utf-8'))
                                    max_temp = data.get('ThermometryUploadList', {}).get('ThermometryUpload', [{}])[0].get('LinePolygonThermCfg', {}).get('MaxTemperature')
                                    if max_temp and max_temp >= ALARM_TEMPERATURE and not is_processing_event:
                                        # <-- DEĞİŞİKLİK BURADA: 'await' eklendi.
                                        await asyncio.to_thread(create_and_save_event, data)
                                except (json.JSONDecodeError, KeyError, IndexError): pass
        except requests.exceptions.RequestException as e:
            print(f"Termal veri bağlantı hatası: {e}. 5 saniye sonra tekrar denenecek.")
            await asyncio.sleep(5)
        except Exception as e:
            print(f"Beklenmedik bir hata oluştu: {e}. 10 saniye sonra tekrar denenecek.")
            await asyncio.sleep(10)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Uygulama yaşam döngüsü yöneticisi."""
    print("Uygulama başlatılıyor... Termal dinleyici görevi oluşturuluyor.")
    asyncio.create_task(listen_for_thermal_anomalies())
    yield
    print("Uygulama kapatılıyor.")

app = FastAPI(
    title="Eren Enerji Termal Anomali Tespit Servisi",
    description="Termal kameralardan gelen verileri dinleyerek yüksek sıcaklık olaylarını tespit eder ve kayıt altına alır.",
    version="1.0.1", # Versiyonu güncelledim
    lifespan=lifespan
)

@app.get("/", summary="Servis Durumu", tags=["Genel"])
def get_root():
    """Servisin ayakta olup olmadığını ve son olay durumunu kontrol eder."""
    return {
        "service_status": "running",
        "last_event_timestamp": datetime.fromtimestamp(last_event_time).isoformat() if last_event_time > 0 else "No events yet.",
        "is_currently_processing_event": is_processing_event,
        "api_docs": "/docs"
    }