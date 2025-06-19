import sys
import cv2  # OpenCV kütüphanesi
import time
import threading
import requests
import xml.etree.ElementTree as ET
import numpy as np
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

# === API ve RTSP URL'leri ===
RTSP_URL_NORMAL = f'rtsp://{CAMERA_USER}:{CAMERA_PASS}@{CAMERA_IP}:554/Streaming/Channels/101'
RTSP_URL_THERMAL = f'rtsp://{CAMERA_USER}:{CAMERA_PASS}@{CAMERA_IP}:554/Streaming/Channels/201'
EVENT_STREAM_URL = f'http://{CAMERA_IP}/ISAPI/Event/notification/alertStream'
THERMAL_ALARM_RULES_URL = f'http://{CAMERA_IP}/ISAPI/Thermal/channels/2/thermometry/1/alarmRules'

# === Canlı Video Akışı İçin Thread ===
class RTSPVideoThread(QThread):
    change_pixmap_signal = pyqtSignal(QImage)

    def __init__(self, rtsp_url, is_thermal=False, parent=None):
        super().__init__()
        self._run_flag = True
        self.rtsp_url = rtsp_url
        self.is_thermal = is_thermal
        self.parent_ui = parent

    def run(self):
        cap = cv2.VideoCapture(self.rtsp_url) # Hatanın olduğu satır
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
                print(f"Uyarı: {self.rtsp_url} adresinden görüntü alınamadı. 1 saniye sonra tekrar denenecek.")
                time.sleep(1)
                cap.release()
                cap = cv2.VideoCapture(self.rtsp_url)
        cap.release()

    def stop(self):
        self._run_flag = False
        self.wait()

# === ISAPI Olay Akışını Dinlemek İçin Thread ===
class ISAPIEventThread(QThread):
    # ... (Bu sınıfın içeriği aynı kalabilir) ...
    pass

# === Ana GUI Sınıfı ===
class PTZControlApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Tam Kontrollü PTZ Termal Paneli")
        self.setGeometry(100, 100, 1400, 750)
        
        self.rotating = False
        self.hotspot_coords = None
        self.last_max_temp = 0.0
        
        self.init_onvif()
        self.init_ui()
        self.init_threads()
        self.load_initial_thermal_rules()

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
        main_layout = QHBoxLayout()
        camera_layout = QVBoxLayout()
        self.camera1_label = QLabel("Kamera 1 (Normal)")
        self.camera2_label = QLabel("Kamera 2 (Termal)")
        self.camera1_label.setFixedSize(640, 360)
        self.camera2_label.setFixedSize(640, 360)
        self.camera1_label.setStyleSheet("background-color: black; border: 1px solid gray;")
        self.camera2_label.setStyleSheet("background-color: black; border: 1px solid gray;")
        camera_layout.addWidget(self.camera1_label)
        camera_layout.addWidget(self.camera2_label)
        
        right_panel_layout = QVBoxLayout()
        ptz_main_box = QGroupBox("PTZ Kontrol")
        ptz_main_layout = QVBoxLayout()
        
        ptz_directional_layout = QGridLayout()
        ptz_directional_layout.addWidget(self.create_ptz_button("↑", 0, 0.3), 0, 1)
        ptz_directional_layout.addWidget(self.create_ptz_button("←", -0.3, 0), 1, 0)
        self.rotate_button = self.create_rotate_button("⟳ 360")
        ptz_directional_layout.addWidget(self.rotate_button, 1, 1)
        ptz_directional_layout.addWidget(self.create_ptz_button("→", 0.3, 0), 1, 2)
        ptz_directional_layout.addWidget(self.create_ptz_button("↓", 0, -0.3), 2, 1)
        
        ptz_absolute_layout = QFormLayout()
        self.pan_input = QLineEdit("0.0")
        self.tilt_input = QLineEdit("0.0")
        self.zoom_input = QLineEdit("0.0")
        goto_btn = QPushButton("Pozisyona Git")
        goto_btn.clicked.connect(self.go_to_absolute_position)
        ptz_absolute_layout.addRow("Pan [-1, 1]:", self.pan_input)
        ptz_absolute_layout.addRow("Tilt [-1, 1]:", self.tilt_input)
        ptz_absolute_layout.addRow("Zoom [0, 1]:", self.zoom_input)
        ptz_absolute_layout.addWidget(goto_btn)
        
        ptz_main_layout.addLayout(ptz_directional_layout)
        ptz_main_layout.addLayout(ptz_absolute_layout)
        ptz_main_box.setLayout(ptz_main_layout)

        thermal_box = QGroupBox("Termal Ayarlar")
        thermal_layout = QVBoxLayout()
        temp_info_layout = QFormLayout()
        self.temp_avg_label = QLabel("-")
        self.temp_min_label = QLabel("-")
        self.temp_max_label = QLabel("-")
        temp_info_layout.addRow("Bölge Ort. Sıcaklık:", self.temp_avg_label)
        temp_info_layout.addRow("Bölge Min. Sıcaklık:", self.temp_min_label)
        temp_info_layout.addRow("Bölge Maks. Sıcaklık:", self.temp_max_label)
        
        coloring_layout = QFormLayout()
        self.above_thresh_input = QLineEdit()
        self.between_min_input = QLineEdit()
        self.between_max_input = QLineEdit()
        update_coloring_btn = QPushButton("Hedef Renklendirmeyi Güncelle")
        update_coloring_btn.clicked.connect(self.update_thermal_coloring_rules)
        coloring_layout.addRow("Üzerinde (Maks >):", self.above_thresh_input)
        coloring_layout.addRow("Arasında (Min):", self.between_min_input)
        coloring_layout.addRow("Arasında (Maks):", self.between_max_input)
        coloring_layout.addWidget(update_coloring_btn)
        
        thermal_layout.addLayout(temp_info_layout)
        thermal_layout.addLayout(coloring_layout)
        thermal_box.setLayout(thermal_layout)

        right_panel_layout.addWidget(ptz_main_box)
        right_panel_layout.addWidget(thermal_box)
        right_panel_layout.addStretch()

        main_layout.addLayout(camera_layout)
        main_layout.addLayout(right_panel_layout)
        self.setLayout(main_layout)

    def init_threads(self):
        self.thread_normal = RTSPVideoThread(RTSP_URL_NORMAL, is_thermal=False, parent=self)
        self.thread_thermal = RTSPVideoThread(RTSP_URL_THERMAL, is_thermal=True, parent=self)
        self.thread_normal.change_pixmap_signal.connect(self.update_image1)
        self.thread_thermal.change_pixmap_signal.connect(self.update_image2)
        
        # ISAPIEventThread'i şimdilik pasifize edelim, çünkü veri işleme kodu eksik
        # self.thread_isapi = ISAPIEventThread(EVENT_STREAM_URL, CAMERA_USER, CAMERA_PASS)
        # self.thread_isapi.thermal_data_updated.connect(self.update_thermal_data)
        # self.thread_isapi.start()
        
        self.thread_normal.start()
        self.thread_thermal.start()
        
    def create_ptz_button(self, label, pan, tilt):
        btn = QPushButton(label)
        btn.setFixedSize(60, 60)
        btn.clicked.connect(lambda: self.move_camera(pan, tilt))
        return btn

    def create_rotate_button(self, label):
        btn = QPushButton(label)
        btn.setFixedSize(80, 60)
        btn.setCheckable(True)
        btn.clicked.connect(self.toggle_rotate)
        return btn

    def move_camera(self, pan, tilt):
        if not self.ptz: return
        req = self.ptz.create_type('ContinuousMove')
        req.ProfileToken = self.token
        req.Velocity = {'PanTilt': {'x': pan, 'y': tilt}}
        self.ptz.ContinuousMove(req)
        threading.Timer(0.5, lambda: self.ptz.Stop({'ProfileToken': self.token})).start()
    
    def toggle_rotate(self, checked):
        if not self.ptz: return
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

    def go_to_absolute_position(self):
        if not self.ptz: return
        try:
            pan = float(self.pan_input.text())
            tilt = float(self.tilt_input.text())
            zoom = float(self.zoom_input.text())
            pan, tilt, zoom = max(-1.0, min(1.0, pan)), max(-1.0, min(1.0, tilt)), max(0.0, min(1.0, zoom))
            req = self.ptz.create_type('AbsoluteMove')
            req.ProfileToken = self.token
            req.Position = {'PanTilt': {'x': pan, 'y': tilt}, 'Zoom': {'x': zoom}}
            self.ptz.AbsoluteMove(req)
        except Exception as e: print(f"Pozisyonlama hatası: {e}")

    @pyqtSlot(QImage)
    def update_image1(self, qt_img): self.camera1_label.setPixmap(QPixmap.fromImage(qt_img))
    
    @pyqtSlot(QImage)
    def update_image2(self, qt_img): self.camera2_label.setPixmap(QPixmap.fromImage(qt_img))
    
    @pyqtSlot(dict)
    def update_thermal_data(self, data):
        # Bu fonksiyon ISAPI thread'inden gelen veriyi işler
        pass

    def load_initial_thermal_rules(self):
        print("Mevcut hedef renklendirme kuralları yükleniyor...")
        # ...

    def update_thermal_coloring_rules(self):
        print("Hedef renklendirme kuralları güncelleniyor...")
        # ...

    def closeEvent(self, event):
        self.rotating = False
        if self.ptz: self.ptz.Stop({'ProfileToken': self.token})
        self.thread_normal.stop()
        self.thread_thermal.stop()
        # self.thread_isapi.stop()
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = PTZControlApp()
    window.show()
    sys.exit(app.exec_())