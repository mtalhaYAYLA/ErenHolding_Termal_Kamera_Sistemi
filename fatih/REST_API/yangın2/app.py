# app.py

from flask import Flask, Response, jsonify, request
from camera_manager import CameraManager
import cv2

app = Flask(__name__)
manager = CameraManager()

# === Video Akışı Bölümü (Değişiklik yok) ===
# ... (Önceki koddan generate_stream fonksiyonu)

@app.route("/stream/normal/<int:camera_id>")
def stream_normal(camera_id):
    # Bu kısım daha da geliştirilerek worker'dan frame alınmalı. Şimdilik konsept olarak böyle.
    # return Response(generate_stream_for_camera(camera_id, "normal"), ...)
    return "Normal Stream Placeholder"

# === YENİ: API Uç Noktaları ===

@app.route("/api/cameras", methods=['GET'])
def get_cameras():
    """Tüm kameraların yapılandırmasını ve anlık durumunu döndürür."""
    # Worker'lardan anlık durum bilgisi alınarak zenginleştirilebilir.
    return jsonify(manager.config)

@app.route("/api/cameras/<int:camera_id>/ptz/goto", methods=['POST'])
def ptz_goto(camera_id):
    """Belirtilen kamerayı mutlak pozisyona gönderir."""
    worker = manager.get_worker(camera_id)
    if not worker:
        return jsonify({"error": "Kamera bulunamadı"}), 404
        
    data = request.json
    pan = data.get('pan')
    tilt = data.get('tilt')
    
    if pan is None or tilt is None:
        return jsonify({"error": "Eksik parametre: pan ve tilt gerekli"}), 400
        
    worker.set_manual_override() # Otonom taramayı durdur
    worker.go_to_degree(pan, tilt) # İstenen pozisyona git
    
    return jsonify({"status": "ok", "message": f"Kamera {camera_id}, {pan}°/{tilt}° pozisyonuna gidiyor."})

# ... Diğer API endpoint'leri (move, zoom, vs.) buraya eklenebilir ...

if __name__ == '__main__':
    manager.start_all() # Tüm kamera worker'larını başlat
    try:
        app.run(host='0.0.0.0', port=5000, debug=False)
    finally:
        manager.stop_all() # Uygulama kapanırken tüm worker'ları durdur