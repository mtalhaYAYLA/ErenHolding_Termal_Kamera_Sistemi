from onvif import ONVIFCamera
import zeep

# Kamera bilgileri
mycam = ONVIFCamera('192.168.1.64', 80, 'admin', 'ErenEnerji')

# PTZ servisi
media_service = mycam.create_media_service()
ptz_service = mycam.create_ptz_service()

profile = media_service.GetProfiles()[0]
token = profile.token

# Hareket komutu (örnek: sola kaydır)
ptz_request = ptz_service.create_type('ContinuousMove')
ptz_request.ProfileToken = token
ptz_request.Velocity = {'PanTilt': {'x': -0.5, 'y': 0.0}}

# Başlat
ptz_service.ContinuousMove(ptz_request)

# Bekle ve durdur
import time
time.sleep(1)  # 1 saniye hareket etsin
ptz_service.Stop({'ProfileToken': token})
