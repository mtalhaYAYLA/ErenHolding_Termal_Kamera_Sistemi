import sys
import cv2
import time
import threading
import requests
import xml.etree.ElementTree as ET # XML işlemek için
from PyQt5.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton, QVBoxLayout,
    QHBoxLayout, QGridLayout, QGroupBox, QLineEdit, QFormLayout
)
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtCore import Qt, QThread, pyqtSignal, pyqtSlot
from onvif import ONVIFCamera
from requests.auth import HTTPDigestAuth

# === KAMERA BİLGİLERİ (Kolay erişim için sabit olarak tanımlandı) ===
CAMERA_IP = '192.168.1.64'
CAMERA_PORT = 80
CAMERA_USER = 'admin'
CAMERA_PASS = 'ErenEnerji'

# RTSP URL'leri
RTSP_URL_NORMAL = f'rtsp://{CAMERA_USER}:{CAMERA_PASS}@{CAMERA_IP}:554/Streaming/Channels/101'
RTSP_URL_THERMAL = f'rtsp://{CAMERA_USER}:{CAMERA_PASS}@{CAMERA_IP}:554/Streaming/Channels/201'

# ISAPI Olay Akışı Uç Noktası (Bu, real-time veri için daha doğru yöntemdir)
EVENT_STREAM_URL = f'http://{CAMERA_IP}/ISAPI/Event/notification/alertStream'

# === Canlı Video Akışı İçin Thread (Sıcak nokta işaretleme eklendi) ===
class RTSPVideoThread(QThread):
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
                # Normal video akışında ISAPI'den gelen sıcak noktayı işaretle
                if not self.is_thermal and self.parent_ui.hotspot_coords:
                    x, y = self.parent_ui.hotspot_coords
                    h_frame, w_frame, _ = frame.shape
                    # Koordinatlar 0.0-1.0 aralığında olduğu için resim boyutuyla çarp
                    px, py = int(x * w_frame), int(y * h_frame)
                    
                    # Hedef imleci ve sıcaklık bilgisi
                    cv2.drawMarker(frame, (px, py), (0, 0, 255), markerType=cv2.MARKER_CROSS, markerSize=20, thickness=2)
                    temp_text = f"Hotspot: {self.parent_ui.last_max_temp:.1f} C"
                    cv2.putText(frame, temp_text, (px + 15, py - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)

                rgb_image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                h, w, ch = rgb_image.shape
                qt_img = QImage(rgb_image.data, w, h, ch * w, QImage.Format_RGB888)
                scaled_img = qt_img.scaled(640, 360, Qt.KeepAspectRatio)
                self.change_pixmap_signal.emit(scaled_img)
            else:
                time.sleep(1) # Bağlantı koparsa 1 saniye bekle
                cap.release()
                cap = cv2.VideoCapture(self.rtsp_url)
        cap.release()

    def stop(self):
        self._run_flag = False
        self.wait()

# === ISAPI Olay Akışını Dinlemek İçin Yeni Thread ===
class ISAPIEventThread(QThread):
    thermal_data_updated = pyqtSignal(dict)
    connection_status = pyqtSignal(str)

    def __init__(self, url, user, password):
        super().__init__()
        self._run_flag = True
        self.url = url
        self.auth = HTTPDigestAuth(user, password)

    def run(self):
        try:
            # stream=True ile bağlantıyı açık tut
            response = requests.get(self.url, auth=self.auth, stream=True, timeout=(5, 60)) # (connect_timeout, read_timeout)
            self.connection_status.emit("Bağlandı, olaylar bekleniyor...")
            
            buffer = b''
            for chunk in response.iter_content(chunk_size=1024):
                if not self._run_flag:
                    break
                buffer += chunk
                # Gelen veriyi satır satır işle
                while b'\r\n' in buffer:
                    line, buffer = buffer.split(b'\r\n', 1)
                    # Heartbeat (bağlantı kontrolü) veya boş satırları atla
                    if line.strip() == b'--boundary' or line.strip() == b'' or b'heartbeat' in line:
                        continue
                    
                    # XML verisi içeren bir bloğu bulmaya çalış
                    if line.strip().startswith(b'<EventNotificationAlert'):
                        xml_buffer = line
                        # XML'in sonunu bulana kadar oku
                        while b'</EventNotificationAlert>' not in buffer:
                            try:
                                more_chunk = next(response.iter_content(chunk_size=1024))
                                buffer += more_chunk
                            except StopIteration:
                                break
                        
                        xml_end_index = buffer.find(b'</EventNotificationAlert>') + len(b'</EventNotificationAlert>')
                        xml_buffer += buffer[:xml_end_index]
                        buffer = buffer[xml_end_index:]
                        
                        self.parse_event(xml_buffer)

        except requests.exceptions.RequestException as e:
            print(f"ISAPI olay akışı hatası: {e}")
            self.connection_status.emit(f"Bağlantı Hatası: {e}")
        
        print("ISAPI Event Thread durdu.")

    def parse_event(self, xml_data):
        try:
            # XML verisini ayrıştır
            root = ET.fromstring(xml_data)
            # İsim alanı (namespace) olabilir, bunu ele almamız gerekir
            ns = {'isapi': 'http://www.isapi.org/ver20/XMLSchema'}
            
            event_type = root.find('isapi:eventType', ns).text
            
            # Sadece sıcaklık alarmlarıyla ilgileniyoruz (TMA veya TMPA)
            if event_type in ['TMA', 'TMPA']:
                data = {}
                tda_node = root.find('.//isapi:TDA', ns)
                if tda_node is not None:
                    data['averageTemperature'] = float(tda_node.find('isapi:averageTemperature', ns).text)
                    data['highestTemperature'] = float(tda_node.find('isapi:highestTemperature', ns).text)
                    data['lowestTemperature'] = float(tda_node.find('isapi:lowestTemperature', ns).text)
                    
                    max_point_node = tda_node.find('.//isapi:MaxTemperaturePoint', ns)
                    if max_point_node is not None:
                        data['MaxTemperaturePoint'] = {
                            'positionX': float(max_point_node.find('isapi:positionX', ns).text),
                            'positionY': float(max_point_node.find('isapi:positionY', ns).text)
                        }
                    self.thermal_data_updated.emit(data)

        except ET.ParseError as e:
            # Bazen eksik veya bozuk XML gelebilir
            print(f"XML Ayrıştırma Hatası: {e}\nGelen Veri: {xml_data[:200]}...")
        except Exception as e:
            print(f"Olay işlenirken hata oluştu: {e}")


# === Ana GUI Sınıfı (Önceki kodla aynı, sadece thread başlatma kısmı farklı) ===
class PTZControlApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ISAPI Destekli PTZ Termal Kontrol Paneli")
        self.setGeometry(100, 100, 1280, 720)
        
        # UI durumu için değişkenler
        self.rotating = False
        self.hotspot_coords = None
        self.last_max_temp = 0.0
        self.max_temp_threshold = 80.0

        self.init_onvif()
        self.init_ui()
        self.init_threads()

    def init_onvif(self):
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
        # ... (Önceki koddaki init_ui fonksiyonunun tamamı buraya gelecek) ...
        # Kamera Görüntü Alanları
        self.camera1_label = QLabel("Kamera 1 (Normal)")
        self.camera2_label = QLabel("Kamera 2 (Termal)")
        self.camera1_label.setFixedSize(640, 360)
        self.camera2_label.setFixedSize(640, 360)
        self.camera1_label.setStyleSheet("background-color: black; border: 1px solid gray;")
        self.camera2_label.setStyleSheet("background-color: black; border: 1px solid gray;")

        # Sıcaklık Bilgi Paneli
        self.temp_avg_label = QLabel("Ortalama: -")
        self.temp_min_label = QLabel("Min: -")
        self.temp_max_label = QLabel("Max: -")
        self.thermal_status_label = QLabel("Termal API: Bekleniyor...")
        self.thermal_status_label.setStyleSheet("color: orange;")

        # Eşik Giriş Alanı
        self.max_thresh_input = QLineEdit(str(self.max_temp_threshold))
        update_btn = QPushButton("Eşiği Güncelle")
        update_btn.clicked.connect(self.update_thresholds)

        # PTZ Kontrol Butonları
        ptz_layout = QGridLayout()
        ptz_layout.addWidget(self.create_ptz_button("↑", 0, 0.3), 0, 1)
        ptz_layout.addWidget(self.create_ptz_button("←", -0.3, 0), 1, 0)
        self.rotate_button = self.create_rotate_button("⟳ 360")
        ptz_layout.addWidget(self.rotate_button, 1, 1)
        ptz_layout.addWidget(self.create_ptz_button("→", 0.3, 0), 1, 2)
        ptz_layout.addWidget(self.create_ptz_button("↓", 0, -0.3), 2, 1)
        ptz_box = QGroupBox("PTZ Kontrol")
        ptz_box.setLayout(ptz_layout)
        
        # Termal Ayarlar Paneli
        temp_form_layout = QFormLayout()
        temp_form_layout.addRow("Ortalama Sıcaklık:", self.temp_avg_label)
        temp_form_layout.addRow("Min Sıcaklık:", self.temp_min_label)
        temp_form_layout.addRow("Max Sıcaklık:", self.temp_max_label)
        temp_form_layout.addRow("Maks. Sıcaklık Eşiği (°C):", self.max_thresh_input)
        temp_form_layout.addRow(update_btn)
        temp_form_layout.addRow("Termal API Durumu:", self.thermal_status_label)
        temp_box = QGroupBox("Gerçek Zamanlı Termal Veri")
        temp_box.setLayout(temp_form_layout)

        # Ana Yerleşim
        left_layout = QVBoxLayout()
        left_layout.addWidget(self.camera1_label)
        left_layout.addWidget(self.camera2_label)

        right_layout = QVBoxLayout()
        right_layout.addWidget(ptz_box)
        right_layout.addWidget(temp_box)
        right_layout.addStretch()

        main_layout = QHBoxLayout()
        main_layout.addLayout(left_layout)
        main_layout.addLayout(right_layout)
        self.setLayout(main_layout)

    def init_threads(self):
        """Tüm thread'leri başlatır."""
        # Video Thread'leri
        self.thread_normal = RTSPVideoThread(RTSP_URL_NORMAL, is_thermal=False, parent=self)
        self.thread_thermal = RTSPVideoThread(RTSP_URL_THERMAL, is_thermal=True, parent=self)
        self.thread_normal.change_pixmap_signal.connect(lambda img: self.camera1_label.setPixmap(QPixmap.fromImage(img)))
        self.thread_thermal.change_pixmap_signal.connect(lambda img: self.camera2_label.setPixmap(QPixmap.fromImage(img)))
        
        # YENİ Termal Olay Akışı Thread'i
        self.thread_isapi_events = ISAPIEventThread(EVENT_STREAM_URL, CAMERA_USER, CAMERA_PASS)
        self.thread_isapi_events.thermal_data_updated.connect(self.update_thermal_data)
        self.thread_isapi_events.connection_status.connect(self.update_thermal_status)

        self.thread_normal.start()
        self.thread_thermal.start()
        self.thread_isapi_events.start()

    # ... (Geri kalan tüm fonksiyonlar: create_ptz_button, move_camera, toggle_rotate, update_thermal_data vb. önceki kodla aynı) ...
    # Kısaltmak için sadece birkaçını ekliyorum, tam kod için yukarıdaki blokları birleştirin.
    def create_ptz_button(self, label, pan, tilt):
        btn = QPushButton(label)
        btn.setFixedSize(60, 60)
        btn.clicked.connect(lambda: self.move_camera(pan, tilt))
        return btn

    def create_rotate_button(self, label):
        btn = QPushButton(label)
        btn.setFixedSize(80, 60)
        btn.setCheckable(True)
        btn.clicked.connect(lambda checked: self.toggle_rotate(checked))
        return btn

    def move_camera(self, pan, tilt):
        if not self.ptz:
            print("PTZ servisi mevcut değil.")
            return
        req = self.ptz.create_type('ContinuousMove')
        req.ProfileToken = self.token
        req.Velocity = {'PanTilt': {'x': pan, 'y': tilt}}
        self.ptz.ContinuousMove(req)
        threading.Timer(0.5, lambda: self.ptz.Stop({'ProfileToken': self.token})).start()
    
    def toggle_rotate(self, checked):
        if not self.ptz:
            print("PTZ servisi mevcut değil.")
            self.rotate_button.setChecked(False)
            return
        self.rotating = checked
        if self.rotating:
            threading.Thread(target=self.rotate_loop, daemon=True).start()
        else:
            self.ptz.Stop({'ProfileToken': self.token})

    def rotate_loop(self):
        req = self.ptz.create_type('ContinuousMove')
        req.ProfileToken = self.token
        req.Velocity = {'PanTilt': {'x': 0.2, 'y': 0}}
        while self.rotating:
            self.ptz.ContinuousMove(req)
            time.sleep(0.2)
            
    @pyqtSlot(dict)
    def update_thermal_data(self, data):
        avg_temp = data.get('averageTemperature', -1)
        min_temp = data.get('lowestTemperature', -1)
        max_temp = data.get('highestTemperature', -1)
        self.last_max_temp = max_temp
        
        self.temp_avg_label.setText(f"{avg_temp:.1f} °C")
        self.temp_min_label.setText(f"{min_temp:.1f} °C")
        self.temp_max_label.setText(f"{max_temp:.1f} °C")

        max_point = data.get('MaxTemperaturePoint', {})
        if max_point:
            self.hotspot_coords = (max_point.get('positionX', 0), max_point.get('positionY', 0))
        else:
            self.hotspot_coords = None

        if max_temp > self.max_temp_threshold:
            self.temp_max_label.setStyleSheet("color: red; font-weight: bold;")
            print(f"ALARM: Maksimum sıcaklık eşiği ({self.max_temp_threshold}°C) aşıldı! Ölçülen: {max_temp}°C")
        else:
            self.temp_max_label.setStyleSheet("color: black;")
            
    @pyqtSlot(str)
    def update_thermal_status(self, status):
        self.thermal_status_label.setText(status)
        if status.startswith("Bağlantı Hatası"):
            self.thermal_status_label.setStyleSheet("color: red;")
        else:
            self.thermal_status_label.setStyleSheet("color: green;")

    def update_thresholds(self):
        try:
            self.max_temp_threshold = float(self.max_thresh_input.text())
            print(f"Yeni maksimum sıcaklık eşiği: {self.max_temp_threshold}°C")
        except ValueError:
            print("Hatalı giriş!")

    def closeEvent(self, event):
        print("Uygulama kapatılıyor, thread'ler durduruluyor...")
        self.rotating = False
        if self.ptz:
            self.ptz.Stop({'ProfileToken': self.token})
        self.thread_normal.stop()
        self.thread_thermal.stop()
        self.thread_isapi_events.stop()
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = PTZControlApp()
    window.show()
    sys.exit(app.exec_())