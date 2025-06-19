import cv2

# RTSP adreslerini kameranın marka/modeline göre düzenle
camera1_url = "rtsp://admin:ErenEnerji@192.168.1.64:554/Streaming/Channels/101"
camera2_url = "rtsp://admin:ErenEnerji@192.168.1.64:554/Streaming/Channels/102"

cap1 = cv2.VideoCapture(camera1_url)
cap2 = cv2.VideoCapture(camera2_url)

while True:
    ret1, frame1 = cap1.read()
    ret2, frame2 = cap2.read()

    if ret1:
        frame1 = cv2.resize(frame1, (640, 480))
        cv2.putText(frame1, "Camera 1", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
        cv2.imshow("Camera 1", frame1)
    else:
        print("Camera 1 görüntü alınamıyor.")

    if ret2:
        frame2 = cv2.resize(frame2, (640, 480))
        cv2.putText(frame2, "Camera 2", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
        cv2.imshow("Camera 2", frame2)
    else:
        print("Camera 2 görüntü alınamıyor.")

    # 'q' tuşu ile çıkış
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap1.release()
cap2.release()
cv2.destroyAllWindows()
