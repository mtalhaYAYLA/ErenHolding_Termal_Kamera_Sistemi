# config.py

# --- KAMERA BİLGİLERİ ---
CAMERA_IP = '192.168.1.64'
CAMERA_PORT = 80
CAMERA_USER = 'admin'
CAMERA_PASS = 'ErenEnerji' # Şifrenizi buraya girin

# --- URL'ler ---
# Bu URL'leri kameranızın belgelerine göre doğrulayın. Genellikle bu formattadır.
RTSP_URL_NORMAL = f'rtsp://{CAMERA_USER}:{CAMERA_PASS}@{CAMERA_IP}:554/Streaming/Channels/101'
RTSP_URL_THERMAL = f'rtsp://{CAMERA_USER}:{CAMERA_PASS}@{CAMERA_IP}:554/Streaming/Channels/201'
REALTIME_THERMOMETRY_URL = f'http://{CAMERA_IP}/ISAPI/Thermal/channels/2/thermometry/realTimethermometry/rules?format=json'
PTZ_STATUS_URL = f'http://{CAMERA_IP}/ISAPI/PTZCtrl/channels/1/status'

# --- ALARM AYARLARI ---
# Bu sıcaklığın (°C) üzerine çıkıldığında alarm tetiklenir.
ALARM_TEMPERATURE = 75.0

# Arka arkaya sürekli olay oluşturmasını engellemek için bekleme süresi (saniye).
EVENT_COOLDOWN_SECONDS = 60