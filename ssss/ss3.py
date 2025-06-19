import sys
import cv2
import time
import threading
import requests
import xml.etree.ElementTree as ET
import numpy as np # Ham veriyi işlemek için
from PyQt5.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton, QVBoxLayout,
    QHBoxLayout, QGridLayout, QGroupBox, QLineEdit, QFormLayout
)
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtCore import Qt, QThread, pyqtSignal, pyqtSlot
from onvif import ONVIFCamera
from requests.auth import HTTPDigestAuth

# === KAMERA BİLGİLERİ ===
CAMERA_IP = '192.168.1.64'
CAMERA_PORT = 80
CAMERA_USER = 'admin'
CAMERA_PASS = 'ErenEnerji'

# URL'ler
RTSP_URL_NORMAL = f'rtsp://{CAMERA_USER}:{CAMERA_PASS}@{CAMERA_IP}:554/Streaming/Channels/101'
RTSP_URL_THERMAL = f'rtsp://{CAMERA_USER}:{CAMERA_PASS}@{CAMERA_IP}:554/Streaming/Channels/201'
EVENT_STREAM_URL = f'http://{CAMERA_IP}/ISAPI/Event/notification/alertStream'
# Pixel-to-pixel veri endpoint'i (Dokümantasyona göre bu veya benzeri bir URL olmalı)
PIXEL_DATA_URL = f'http://{CAMERA_IP}/ISAPI/Thermal/channels/2/thermometry/pixelToPixelData'


# === Canlı Video Akışı İçin Thread ===
class RTSPVideoThread(QThread):
    # ... (Bu sınıf önceki kodla tamamen aynı, değişiklik yok) ...
    change_pixmap_signal = pyqtSignal(QImage)

    def __init__(self, rtsp_url, is_thermal=False, parent=None):
        super().__init__()
        self._run_flag = True
        self.rtsp_url = rtsp_url
        self.is_thermal = is_thermal
        self.parent_ui = parent

    def run(self):
        cap = cv2.VideoCapture(self.rtsp_url)
        while self._run_flag:
            ret, frame = cap.read()
            if ret:
                if not self.is_thermal and self.parent_ui.hotspot_coords:
                    x, y = self.parent_ui.hotspot_coords
                    h_frame, w_frame, _ = frame.shape
                    px, py = int(x * w_frame), int(y * h_frame)
                    cv2.drawMarker(frame, (px, py), (0, 0, 255), markerType=cv2.MARKER_CROSS, markerSize=20, thickness=2)
                    temp_text = f"Hotspot: {self.parent_ui.last_max_temp:.1f} C"
                    cv2.putText(frame, temp_text, (px + 15, py - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)

                rgb_image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                h, w, ch = rgb_image.shape
                qt_img = QImage(rgb_image.data, w, h, ch * w, QImage.Format_RGB888)
                scaled_img = qt_img.scaled(640, 360, Qt.KeepAspectRatio)
                self.change_pixmap_signal.emit(scaled_img)
            else:
                time.sleep(1)
                cap.release()
                cap = cv2.VideoCapture(self.rtsp_url)
        cap.release()

    def stop(self):
        self._run_flag = False
        self.wait()

# === ISAPI Olay Akışını Dinlemek İçin Thread ===
class ISAPIEventThread(QThread):
    # ... (Bu sınıf önceki kodla tamamen aynı, değişiklik yok) ...
    thermal_data_updated = pyqtSignal(dict)
    connection_status = pyqtSignal(str)

    def __init__(self, url, user, password):
        super().__init__()
        self._run_flag = True
        self.url = url
        self.auth = HTTPDigestAuth(user, password)
        self.ns = {'isapi': 'http://www.isapi.org/ver20/XMLSchema'}
        
    def run(self):
        try:
            response = requests.get(self.url, auth=self.auth, stream=True, timeout=(5, 60))
            self.connection_status.emit("Olay Akışı: Bağlandı")
            buffer = b''
            for chunk in response.iter_content(chunk_size=1024):
                if not self._run_flag: break
                buffer += chunk
                while b'\r\n' in buffer:
                    line, buffer = buffer.split(b'\r\n', 1)
                    if line.strip().startswith(b'<EventNotificationAlert'):
                        xml_buffer = line
                        while b'</EventNotificationAlert>' not in buffer:
                            try:
                                more_chunk = next(response.iter_content(chunk_size=1024))
                                buffer += more_chunk
                            except StopIteration: break
                        
                        xml_end_index = buffer.find(b'</EventNotificationAlert>') + len(b'</EventNotificationAlert>')
                        xml_buffer += buffer[:xml_end_index]
                        buffer = buffer[xml_end_index:]
                        self.parse_event(xml_buffer)
        except requests.exceptions.RequestException as e:
            self.connection_status.emit(f"Olay Akışı: Hata")
        print("ISAPI Event Thread durdu.")

    def parse_event(self, xml_data):
        try:
            root = ET.fromstring(xml_data)
            event_type = root.find('isapi:eventType', self.ns).text
            if event_type in ['TMA', 'TMPA']:
                tda_node = root.find('.//isapi:TDA', self.ns)
                if tda_node:
                    data = {
                        'averageTemperature': float(tda_node.find('isapi:averageTemperature', self.ns).text),
                        'highestTemperature': float(tda_node.find('isapi:highestTemperature', self.ns).text),
                        'lowestTemperature': float(tda_node.find('isapi:lowestTemperature', self.ns).text),
                        'MaxTemperaturePoint': {
                            'positionX': float(tda_node.find('.//isapi:positionX', self.ns).text),
                            'positionY': float(tda_node.find('.//isapi:positionY', self.ns).text)
                        }
                    }
                    self.thermal_data_updated.emit(data)
        except Exception:
            pass

# === YENİ: Pixel-to-Pixel Sıcaklık Verisini Çekmek İçin Thread ===
class PixelDataThread(QThread):
    pixel_data_ready = pyqtSignal(np.ndarray, int, int) # (data, width, height)
    connection_status = pyqtSignal(str)

    def __init__(self, url, user, password):
        super().__init__()
        self._run_flag = True
        self.url = url
        self.auth = HTTPDigestAuth(user, password)

    def run(self):
        while self._run_flag:
            try:
                # Dokümantasyona göre bu endpoint bir resim ve ek data dönebilir.
                # 'multipart/form-data' olarak döneceği varsayımıyla istek atıyoruz.
                response = requests.get(self.url, auth=self.auth, timeout=3)
                if response.status_code == 200:
                    # Gelen veriyi işle. Bu kısım kameranın tam yanıt formatına göre ayarlanmalı.
                    # Genellikle bu tür veriler 16-bit integer'lardan oluşur.
                    # Sıcaklık değeri (Kelvin * 10) veya (Celsius * 10) olabilir.
                    # Örnek olarak, gelen ham verinin (response.content) doğrudan bir numpy dizisi olduğunu varsayalım.
                    # Gerçek implementasyon için kameranın dokümanı kritik.
                    # Varsayım: İlk 4 byte genişlik, sonraki 4 byte yükseklik, geri kalanı veri.
                    # Bu varsayım yanlış olabilir, dokümana göre güncellenmeli!
                    # Örnek: Hikvision kameralar genelde JPEG resmi ve ekinde binary veri gönderir.
                    # Şimdilik rastgele bir matris oluşturalım.
                    width, height = 640, 512 # Termal sensör çözünürlüğü
                    # (Sıcaklık - 273.15) * 10 varsayımıyla rastgele veri
                    simulated_data = np.random.randint(2980, 3130, size=(height, width)).astype(np.uint16)
                    self.pixel_data_ready.emit(simulated_data, width, height)
                    self.connection_status.emit("Pixel Veri: Alındı")
                else:
                    self.connection_status.emit(f"Pixel Veri: Hata {response.status_code}")
            except requests.exceptions.RequestException as e:
                self.connection_status.emit("Pixel Veri: Bağlantı Hatası")
            
            time.sleep(0.5) # Performans için saniyede 2 kez veri çek

    def stop(self):
        self._run_flag = False
        self.wait()

# === Ana GUI Sınıfı ===
class PTZControlApp(QWidget):
    # ... (init ve diğer UI fonksiyonları önceki kodla büyük ölçüde aynı) ...
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Gelişmiş PTZ Termal Kontrol Paneli")
        self.setGeometry(100, 100, 1280, 720)
        
        self.rotating = False
        self.hotspot_coords = None
        self.last_max_temp = 0.0
        self.max_temp_threshold = 80.0

        # Pixel verisi için değişkenler
        self.pixel_data_matrix = None
        self.thermal_sensor_width = 0
        self.thermal_sensor_height = 0

        self.init_onvif()
        self.init_ui()
        self.init_threads()

    def init_onvif(self):
        # ... (değişiklik yok) ...
        try:
            self.cam = ONVIFCamera(CAMERA_IP, CAMERA_PORT, CAMERA_USER, CAMERA_PASS)
            self.media = self.cam.create_media_service()
            self.ptz = self.cam.create_ptz_service()
            self.profile = self.media.GetProfiles()[0]
            self.token = self.profile.token
            print("ONVIF bağlantısı başarılı.")
        except Exception as e:
            print(f"ONVIF bağlantı hatası: {e}")
            self.ptz = None

    def init_ui(self):
        # ... (önceki kodla aynı, sadece yeni etiketler eklenecek) ...
        # Kamera Görüntü Alanları
        self.camera1_label = QLabel("Kamera 1 (Normal)")
        self.camera2_label = QLabel("Kamera 2 (Termal)")
        self.camera1_label.setFixedSize(640, 360)
        self.camera2_label.setFixedSize(640, 360)
        self.camera1_label.setStyleSheet("background-color: black; border: 1px solid gray;")
        self.camera2_label.setStyleSheet("background-color: black; border: 1px solid gray;")
        
        # YENİ: Fare hareketlerini takip etmeyi etkinleştir
        self.camera2_label.setMouseTracking(True)
        self.camera2_label.mouseMoveEvent = self.thermal_image_mouse_move
        
        # ... (PTZ kutusu aynı) ...
        ptz_layout = QGridLayout()
        # ...
        
        # Termal Ayarlar Paneli
        self.temp_avg_label = QLabel("Ortalama: -")
        self.temp_min_label = QLabel("Min: -")
        self.temp_max_label = QLabel("Max: -")
        self.thermal_status_label = QLabel("Olay Akışı: Bekleniyor...")
        self.pixel_status_label = QLabel("Pixel Veri: Bekleniyor...") # YENİ
        self.cursor_temp_label = QLabel("İmleç Sıcaklığı: -") # YENİ
        
        self.max_thresh_input = QLineEdit(str(self.max_temp_threshold))
        update_btn = QPushButton("Eşiği Güncelle")
        update_btn.clicked.connect(self.update_thresholds)

        temp_form_layout = QFormLayout()
        temp_form_layout.addRow("Ortalama Sıcaklık:", self.temp_avg_label)
        temp_form_layout.addRow("Min Sıcaklık:", self.temp_min_label)
        temp_form_layout.addRow("Max Sıcaklık:", self.temp_max_label)
        temp_form_layout.addRow("İmleç Sıcaklığı:", self.cursor_temp_label) # YENİ
        temp_form_layout.addRow("Maks. Sıcaklık Eşiği (°C):", self.max_thresh_input)
        temp_form_layout.addRow(update_btn)
        temp_form_layout.addRow("Olay Akışı Durumu:", self.thermal_status_label)
        temp_form_layout.addRow("Pixel Veri Durumu:", self.pixel_status_label) # YENİ
        temp_box = QGroupBox("Gerçek Zamanlı Termal Veri")
        temp_box.setLayout(temp_form_layout)

        # ... (Ana yerleşim aynı) ...
        left_layout = QVBoxLayout()
        left_layout.addWidget(self.camera1_label)
        left_layout.addWidget(self.camera2_label)
        right_layout = QVBoxLayout()
        right_layout.addWidget(temp_box) # PTZ box'ı da ekleyin
        right_layout.addStretch()
        main_layout = QHBoxLayout()
        main_layout.addLayout(left_layout)
        main_layout.addLayout(right_layout)
        self.setLayout(main_layout)

    def init_threads(self):
        self.thread_normal = RTSPVideoThread(RTSP_URL_NORMAL, is_thermal=False, parent=self)
        self.thread_thermal = RTSPVideoThread(RTSP_URL_THERMAL, is_thermal=True, parent=self)
        self.thread_normal.change_pixmap_signal.connect(lambda img: self.camera1_label.setPixmap(QPixmap.fromImage(img)))
        self.thread_thermal.change_pixmap_signal.connect(lambda img: self.camera2_label.setPixmap(QPixmap.fromImage(img)))
        
        self.thread_isapi_events = ISAPIEventThread(EVENT_STREAM_URL, CAMERA_USER, CAMERA_PASS)
        self.thread_isapi_events.thermal_data_updated.connect(self.update_thermal_data)
        self.thread_isapi_events.connection_status.connect(self.update_thermal_status)
        
        # YENİ Pixel Veri Thread'ini başlat
        self.thread_pixel_data = PixelDataThread(PIXEL_DATA_URL, CAMERA_USER, CAMERA_PASS)
        self.thread_pixel_data.pixel_data_ready.connect(self.update_pixel_data_matrix)
        self.thread_pixel_data.connection_status.connect(lambda status: self.pixel_status_label.setText(status))

        self.thread_normal.start()
        self.thread_thermal.start()
        self.thread_isapi_events.start()
        self.thread_pixel_data.start()

    @pyqtSlot(np.ndarray, int, int)
    def update_pixel_data_matrix(self, data, width, height):
        """PixelDataThread'den gelen ham sıcaklık verisini ve boyutlarını saklar."""
        self.pixel_data_matrix = data
        self.thermal_sensor_width = width
        self.thermal_sensor_height = height

    def thermal_image_mouse_move(self, event):
        """Termal görüntü üzerinde fare hareket ettiğinde çalışır."""
        if self.pixel_data_matrix is None or self.thermal_sensor_width == 0:
            return

        # İmlecin QLabel üzerindeki koordinatını al
        x = event.pos().x()
        y = event.pos().y()

        # Koordinatları termal sensör çözünürlüğüne ölçekle
        # QLabel boyutu 640x360
        sensor_x = int((x / 640) * self.thermal_sensor_width)
        sensor_y = int((y / 360) * self.thermal_sensor_height)
        
        # Matris sınırları içinde mi kontrol et
        if 0 <= sensor_x < self.thermal_sensor_width and 0 <= sensor_y < self.thermal_sensor_height:
            # Sıcaklık değerini oku. Değerin (Kelvin * 10) veya (Celsius * 10) olduğunu varsayalım.
            # Kameranın dokümanına göre bu formül değişebilir.
            # Varsayım: (Sıcaklık - 273.15) * 10
            temp_value = self.pixel_data_matrix[sensor_y, sensor_x]
            # Gerçek Celsius değerine dönüştür
            # Bu dönüşüm formülü kameraya özeldir, dokümandan teyit edilmeli!
            actual_temp = (temp_value / 10.0) - 273.15
            
            self.cursor_temp_label.setText(f"{actual_temp:.1f} °C")

    def closeEvent(self, event):
        # ... (Önceki kodla aynı, sadece yeni thread'i de durdur) ...
        self.thread_normal.stop()
        self.thread_thermal.stop()
        self.thread_isapi_events.stop()
        self.thread_pixel_data.stop() # YENİ
        event.accept()

    # Diğer tüm yardımcı fonksiyonlar (create_ptz_button, move_camera, vb.) önceki kodla aynı kalacak.
    # ...
    
    # Yer kazanmak için tüm fonksiyonları tekrar eklemiyorum.
    # Önceki koddaki `PTZControlApp` sınıfının geri kalanını buraya kopyalayın.
    def update_thresholds(self): pass
    def update_thermal_data(self, data):
        self.last_max_temp = data.get('highestTemperature', -1)
        self.temp_avg_label.setText(f"{data.get('averageTemperature', -1):.1f} °C")
        self.temp_min_label.setText(f"{data.get('lowestTemperature', -1):.1f} °C")
        self.temp_max_label.setText(f"{self.last_max_temp:.1f} °C")
        max_point = data.get('MaxTemperaturePoint')
        if max_point:
            self.hotspot_coords = (max_point.get('positionX', 0), max_point.get('positionY', 0))
        if self.last_max_temp > self.max_temp_threshold:
            self.temp_max_label.setStyleSheet("color: red; font-weight: bold;")
        else:
            self.temp_max_label.setStyleSheet("color: black;")
    def update_thermal_status(self, status):
        self.thermal_status_label.setText(status)
        if status.startswith("Bağlantı Hatası"): self.thermal_status_label.setStyleSheet("color: red;")
        else: self.thermal_status_label.setStyleSheet("color: green;")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = PTZControlApp()
    window.show()
    sys.exit(app.exec_())