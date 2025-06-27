# camera_handler.py

import os
import cv2
import requests
import xml.etree.ElementTree as ET
from requests.auth import HTTPDigestAuth
from config import (
    CAMERA_USER, CAMERA_PASS, PTZ_STATUS_URL
)

# OpenCV'nin RTSP için TCP kullanmasını zorlayarak bağlantı stabilitesini artırır.
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"

# Her istekte tekrar tekrar oluşturmamak için ortak authentication nesnesi
auth = HTTPDigestAuth(CAMERA_USER, CAMERA_PASS)

def capture_snapshot(rtsp_url: str) -> bytes | None:
    """
    Verilen RTSP URL'sinden tek bir kare yakalar ve JPEG formatında byte olarak döndürür.
    Eğer başarısız olursa None döndürür.
    """
    cap = None
    try:
        cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
        if not cap.isOpened():
            print(f"HATA: RTSP akışına bağlanılamadı: {rtsp_url}")
            return None
        
        # Akıştan stabil bir kare almak için birkaç kare okuyalım
        for _ in range(5):
            ret, frame = cap.read()

        if not ret or frame is None:
            print(f"HATA: RTSP akışından kare okunamadı: {rtsp_url}")
            return None
            
        # Görüntüyü JPEG formatında belleğe kodla
        ret, buffer = cv2.imencode('.jpg', frame)
        if not ret:
            print("HATA: Görüntü JPEG formatına çevrilemedi.")
            return None
            
        return buffer.tobytes()
    finally:
        if cap:
            cap.release()
 
def get_ptz_status() -> dict | None:
    """
    Kameranın anlık Pan ve Tilt pozisyonunu derece cinsinden alır.
    Başarısız olursa None döndürür.
    """
    try:
        response = requests.get(PTZ_STATUS_URL, auth=auth, timeout=2)
        response.raise_for_status() # Hatalı HTTP kodları için (4xx, 5xx) exception fırlatır

        root = ET.fromstring(response.content)
        # Namespace (XML şema adresi) genellikle gereklidir
        ns = {'isapi': 'http://www.isapi.org/ver20/XMLSchema'}
        azimuth_node = root.find('.//isapi:azimuth', ns)
        elevation_node = root.find('.//isapi:elevation', ns)

        if azimuth_node is not None and elevation_node is not None:
            # Değerler genellikle 10 ile çarpılmış olarak gelir, tekrar 10'a böleriz.
            azimuth = float(azimuth_node.text) / 10.0
            elevation = float(elevation_node.text) / 10.0
            return {'pan_degrees': azimuth, 'tilt_degrees': elevation}
        else:
            print("HATA: PTZ XML yanıtında 'azimuth' veya 'elevation' bulunamadı.")
            return None

    except requests.exceptions.RequestException as e:
        print(f"HATA: PTZ durumu alınırken ağ hatası oluştu: {e}")
        return None
    except Exception as e:
        print(f"HATA: PTZ durumu işlenirken genel bir hata oluştu: {e}")
        return None