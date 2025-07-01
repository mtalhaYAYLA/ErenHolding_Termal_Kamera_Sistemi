# app.py

from flask import Flask, Response
import cv2
import threading
import time

# === KAMERA BİLGİLERİ (Kolay erişim için sabit olarak tanımlandı) ===
CAMERA_IP = '192.168.1.64'
CAMERA_USER = 'admin'
CAMERA_PASS = 'ErenEnerji'

# RTSP URL'leri
RTSP_URL_NORMAL = f'rtsp://{CAMERA_USER}:{CAMERA_PASS}@{CAMERA_IP}:554/Streaming/Channels/101'
RTSP_URL_THERMAL = f'rtsp://{CAMERA_USER}:{CAMERA_PASS}@{CAMERA_IP}:554/Streaming/Channels/201'

# Global değişkenler - Farklı thread'lerin iletişim kurması için
normal_frame = None
thermal_frame = None
lock = threading.Lock() # Frame'lere erişirken çakışmayı önlemek için kilit

# Flask uygulamasını oluştur
app = Flask(__name__)

def capture_frames(rtsp_url, frame_type):
    """
    Belirtilen RTSP URL'sinden sürekli olarak video kareleri yakalar ve global bir değişkene yazar.
    Bu fonksiyon bir thread içinde çalışacak.
    """
    global normal_frame, thermal_frame

    while True:
        try:
            cap = cv2.VideoCapture(rtsp_url)
            if not cap.isOpened():
                print(f"HATA: {frame_type} akışı açılamadı. 5 saniye sonra tekrar denenecek.")
                time.sleep(5)
                continue

            print(f"BAŞARILI: {frame_type} akışına bağlanıldı.")
            
            while True:
                ret, frame = cap.read()
                if not ret:
                    print(f"UYARI: {frame_type} akışından veri alınamadı. Yeniden bağlanılıyor...")
                    break # İç döngüyü kırarak yeniden bağlantı kurulmasını sağla

                with lock:
                    if frame_type == "normal":
                        normal_frame = frame.copy()
                    elif frame_type == "thermal":
                        thermal_frame = frame.copy()
        except Exception as e:
            print(f"{frame_type} thread'inde hata oluştu: {e}")
            time.sleep(5) # Hata durumunda 5 saniye bekle ve tekrar dene

def generate_stream(frame_type):
    """
    Global değişkendeki kareyi alıp MJPEG formatında bir HTTP yanıtı olarak yayınlar.
    """
    global normal_frame, thermal_frame

    while True:
        with lock:
            if frame_type == "normal":
                frame = normal_frame
            elif frame_type == "thermal":
                frame = thermal_frame
            else:
                frame = None

        if frame is None:
            # Henüz bir kare yakalanmadıysa veya akış koptuysa bekle
            time.sleep(0.1)
            continue
        
        # Kareyi JPEG formatına dönüştür
        (flag, encodedImage) = cv2.imencode(".jpg", frame)
        if not flag:
            continue

        # HTTP yanıtı olarak kareyi yield et (stream et)
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + bytearray(encodedImage) + b'\r\n')

# === API Uç Noktaları (Endpoints) ===

@app.route("/")
def index():
    """
    Kullanıcının göreceği basit HTML arayüzü.
    """
    return """
    <html>
      <head>
        <title>Çoklu Kamera Gözetleme Sistemi</title>
        <style>
            body { font-family: sans-serif; background-color: #f0f2f5; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
            .container { display: flex; gap: 20px; }
            .camera-box { border: 1px solid #ccc; border-radius: 8px; padding: 10px; background-color: white; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
            h3 { text-align: center; margin-top: 0; }
            img { display: block; width: 640px; height: 360px; background-color: #000; }
        </style>
      </head>
      <body>
        <div class="container">
            <div class="camera-box">
                <h3>Normal Kamera</h3>
                <img src="/stream/normal">
            </div>
            <div class="camera-box">
                <h3>Termal Kamera</h3>
                <img src="/stream/thermal">
            </div>
        </div>
      </body>
    </html>
    """

@app.route("/stream/normal")
def stream_normal():
    """Normal kamera için video akışını sunar."""
    return Response(generate_stream("normal"),
                    mimetype="multipart/x-mixed-replace; boundary=frame")

@app.route("/stream/thermal")
def stream_thermal():
    """Termal kamera için video akışını sunar."""
    return Response(generate_stream("thermal"),
                    mimetype="multipart/x-mixed-replace; boundary=frame")


if __name__ == '__main__':
    # Arka planda video karelerini yakalamak için thread'leri başlat
    normal_thread = threading.Thread(target=capture_frames, args=(RTSP_URL_NORMAL, "normal"), daemon=True)
    thermal_thread = threading.Thread(target=capture_frames, args=(RTSP_URL_THERMAL, "thermal"), daemon=True)
    
    normal_thread.start()
    thermal_thread.start()
    
    # Flask sunucusunu çalıştır
    # host='0.0.0.0' ile ağdaki diğer cihazların da erişebilmesini sağla
    app.run(host='0.0.0.0', port=5000, debug=False)