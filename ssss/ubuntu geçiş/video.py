import cv2

RTSP_URL = "rtsp://admin:ErenEnerji@192.168.1.64:554/Streaming/Channels/101"
cap = cv2.VideoCapture(RTSP_URL)

if not cap.isOpened():
    print("Hata: Kamera bağlantısı sağlanamadı.")
else:
    print("Kamera bağlantısı başarılı!")
    while True:
        ret, frame = cap.read()
        if not ret:
            print("Hata: Veri alınamıyor.")
            break
        cv2.imshow("Video Akışı", frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

cap.release()
cv2.destroyAllWindows()
