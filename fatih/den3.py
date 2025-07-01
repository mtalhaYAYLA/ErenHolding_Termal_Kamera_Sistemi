"""🎯 Hikvision RTSP Kanal Formatı:
php-template

rtsp://<kullanici>:<şifre>@<ip>:554/Streaming/Channels/XYZ

Buradaki XYZ şu şekilde açılır:

X → Kamera ID (fiziksel sensör no)

1: 1. sensör (genelde normal kamera)

2: 2. sensör (senin durumda termal kamera)

Y → Akış tipi (stream index):

0: Ana akış (main stream) → En yüksek kalite

1: Alt akış (sub stream) → Orta kalite

2: Üçüncül akış (third stream) → En düşük kalite (bazı modellerde olur)

✅ Örnekler
RTSP Kanal	Açıklama
101	Kamera 1 - Ana akış (en kaliteli)
102	Kamera 1 - Alt akış (daha düşük bitrate & çözünürlük)
103	Kamera 1 - Üçüncül akış (varsa, çok düşük kalite)
201	Kamera 2 (Termal) - Ana akış
202	Kamera 2 - Alt akış
203	Kamera 2 - Üçüncül akış

🔍 Hangi Stream'leri destekliyor?
Bunu öğrenmek için:

Kameranın web arayüzüne git.

Configuration > Video > Stream Type altında:

Main Stream

Sub Stream

Third Stream (varsa)

Her biri için çözünürlük, bitrate, codec ayarları yapılabilir.

🛠️ Ne zaman hangisi kullanılır?
Akış Tipi	Kullanım Durumu
Main Stream (1x0)	Kayıt, analiz, kalite önemliyse
Sub Stream (1x1)	Düşük ağ kullanımı gerekliyse, izleme için
Third Stream (1x2)	Çok düşük bant genişliği (mobil bağlantı, yedek akış)
"""

import cv2

camera1_url = "rtsp://admin:ErenEnerji@192.168.1.64:554/Streaming/Channels/101"  # Normal kamera
camera2_url = "rtsp://admin:ErenEnerji@192.168.1.64:554/Streaming/Channels/201"  # Termal kamera

cap1 = cv2.VideoCapture(camera1_url)
cap2 = cv2.VideoCapture(camera2_url)

while True:
    ret1, frame1 = cap1.read()
    ret2, frame2 = cap2.read()

    if ret1:
        frame1 = cv2.resize(frame1, (640, 480))
        cv2.putText(frame1, "Camera 1 - Normal", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
        cv2.imshow("Camera 1", frame1)
    else:
        print("Camera 1 görüntü alınamıyor.")

    if ret2:
        frame2 = cv2.resize(frame2, (640, 480))
        cv2.putText(frame2, "Camera 2 - Termal", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
        cv2.imshow("Camera 2", frame2)
    else:
        print("Camera 2 görüntü alınamıyor.")

    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap1.release()
cap2.release()
cv2.destroyAllWindows()
