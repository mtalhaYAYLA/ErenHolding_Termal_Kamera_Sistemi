import sys
import os
import cv2
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

# ÇÖZÜM: OpenCV'nin RTSP için TCP kullanmasını sağla (H.264 hatalarını azaltır)
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"

# === KAMERA BİLGİLERİ ===
CAMERA_IP = '192.168.1.64'
CAMERA_PORT = 80
CAMERA_USER = 'admin'
CAMERA_PASS = 'ErenEnerji'

# === API ve RTSP URL'leri ===
RTSP_URL_NORMAL = f'rtsp://{CAMERA_USER}:{CAMERA_PASS}@{CAMERA_IP}:554/Streaming/Channels/101'
RTSP_URL_THERMAL = f'rtsp://{CAMERA_USER}:{CAMERA_PASS}@{CAMERA_IP}:554/Streaming/Channels/201'
THERMAL_ALARM_RULES_URL = f'http://{CAMERA_IP}/ISAPI/Thermal/channels/2/thermometry/1/alarmRules'
PTZ_STATUS_URL = f'http://{CAMERA_IP}/ISAPI/PTZCtrl/channels/1/status'

# Termal sensörün gerçek çözünürlüğü (Kamera modelinize göre güncelleyin)
THERMAL_SENSOR_WIDTH = 640
THERMAL_SENSOR_HEIGHT = 512


# === Hata Yönetimli Video Thread ===
class RTSPVideoThread(QThread):
    change_pixmap_signal = pyqtSignal(QImage)
    connection_status_signal = pyqtSignal(str)

    def __init__(self, rtsp_url, is_thermal=False, parent=None):
        super().__init__()
        self._run_flag = True
        self.rtsp_url = rtsp_url
        self.is_thermal = is_thermal
        self.parent_ui = parent
        self.stream_name = "Termal" if is_thermal else "Normal"

    def run(self):
        try:
            cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
            if not cap.isOpened():
                self.connection_status_signal.emit(f"{self.stream_name}: Bağlantı Hatası")
                return

            self.connection_status_signal.emit(f"{self.stream_name}: Bağlandı")
            
            while self._run_flag:
                ret, frame = cap.read()
                if ret:
                    if not self.is_thermal and self.parent_ui.hotspot_coords:
                        x, y = self.parent_ui.hotspot_coords
                        h_frame, w_frame, _ = frame.shape
                        px, py = int(x * w_frame), int(y * h_frame)
                        cv2.drawMarker(frame, (px, py), (0, 0, 255), cv2.MARKER_CROSS, 20, 2)
                        temp_text = f"Hotspot: {self.parent_ui.last_max_temp:.1f} C"
                        cv2.putText(frame, temp_text, (px + 15, py - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
                    
                    rgb_image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    h, w, ch = rgb_image.shape
                    qt_img = QImage(rgb_image.data, w, h, ch * w, QImage.Format_RGB888)
                    scaled_img = qt_img.scaled(640, 360, Qt.KeepAspectRatio)
                    self.change_pixmap_signal.emit(scaled_img)
                else:
                    self.connection_status_signal.emit(f"{self.stream_name}: Veri Alınamıyor...")
                    time.sleep(2)
                    cap.release()
                    cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
            cap.release()
        except Exception as e:
            print(f"HATA ({self.stream_name} Thread): {e}")
            self.connection_status_signal.emit(f"{self.stream_name}: Thread Çöktü")

    def stop(self):
        self._run_flag = False
        self.wait()

# === Pixel-to-Pixel Sıcaklık Verisini Çekmek İçin Thread (Simülasyonlu) ===
class PixelDataThread(QThread):
    pixel_data_ready = pyqtSignal(np.ndarray)

    def __init__(self):
        super().__init__()
        self._run_flag = True

    def run(self):
        # Bu thread, gerçek bir sistemde kameranın pixelToPixelData endpoint'inden
        # ham termal veriyi çekmelidir. Şimdilik simülasyon yapılıyor.
        while self._run_flag:
            width, height = THERMAL_SENSOR_WIDTH, THERMAL_SENSOR_HEIGHT
            # Simülasyon: Sıcaklık değerleri Celsius * 100 olarak (örn. 25.50 C -> 2550)
            simulated_data = np.random.randint(2200, 5000, size=(height, width)).astype(np.uint16)
            self.pixel_data_ready.emit(simulated_data)
            time.sleep(0.5)

    def stop(self):
        self._run_flag = False
        self.wait()


# === Ana GUI Sınıfı ===
class PTZControlApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Tam Kontrollü PTZ Termal Paneli")
        self.setGeometry(100, 100, 1400, 750)
        
        self.rotating = False
        self.ptz = None
        self.auth = HTTPDigestAuth(CAMERA_USER, CAMERA_PASS)
        
        self.hotspot_coords = None
        self.last_max_temp = 0.0
        self.pixel_data_matrix = None
        self.ptz_limits = {'pan_min': 0, 'pan_max': 360, 'tilt_min': -90, 'tilt_max': 90}
        
        self.init_onvif()
        self.init_ui()
        self.init_threads()
        
        threading.Timer(1.0, self.load_initial_data).start()

    def init_onvif(self):
        try:
            self.cam = ONVIFCamera(CAMERA_IP, CAMERA_PORT, CAMERA_USER, CAMERA_PASS)
            self.ptz = self.cam.create_ptz_service()
            self.profile = self.cam.create_media_service().GetProfiles()[0]
            self.token = self.profile.token
            
            config_token = self.profile.PTZConfiguration.token
            ptz_config_options = self.ptz.GetConfigurationOptions({'ConfigurationToken': config_token})
            
            pan_limits = ptz_config_options.Spaces.AbsolutePanTiltPositionSpace[0].XRange
            tilt_limits = ptz_config_options.Spaces.AbsolutePanTiltPositionSpace[0].YRange
            
            self.ptz_limits['pan_min'] = pan_limits.Min
            self.ptz_limits['pan_max'] = pan_limits.Max
            self.ptz_limits['tilt_min'] = tilt_limits.Min
            self.ptz_limits['tilt_max'] = tilt_limits.Max
            
            print(f"ONVIF bağlantısı başarılı. PTZ Limitleri: Pan [{pan_limits.Min}, {pan_limits.Max}], Tilt [{tilt_limits.Min}, {tilt_limits.Max}]")
        except Exception as e:
            print(f"ONVIF bağlantı/limit alma hatası: {e}. Varsayılan limitler kullanılacak.")

    def init_ui(self):
        main_layout = QHBoxLayout()
        
        camera_layout = QVBoxLayout()
        self.camera1_label = QLabel("Normal Kamera Yükleniyor...")
        self.camera2_label = QLabel("Termal Kamera Yükleniyor...")
        self.camera1_label.setFixedSize(640, 360)
        self.camera2_label.setFixedSize(640, 360)
        self.camera1_label.setStyleSheet("background-color: black; color: white; border: 1px solid gray; font-size: 16px; qproperty-alignment: 'AlignCenter';")
        self.camera2_label.setStyleSheet("background-color: black; color: white; border: 1px solid gray; font-size: 16px; qproperty-alignment: 'AlignCenter';")
        self.camera2_label.setMouseTracking(True)
        self.camera2_label.mouseMoveEvent = self.thermal_image_mouse_move
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
        self.pan_input = QLineEdit("90.0")
        self.tilt_input = QLineEdit("0.0")
        self.current_pan_label = QLabel("Mevcut P: -")
        self.current_tilt_label = QLabel("Mevcut T: -")
        goto_btn = QPushButton("Pozisyona Git")
        goto_btn.clicked.connect(self.go_to_absolute_position)
        ptz_absolute_layout.addRow(f"Pan [{self.ptz_limits['pan_min']}°, {self.ptz_limits['pan_max']}°]:", self.pan_input)
        ptz_absolute_layout.addRow(f"Tilt [{self.ptz_limits['tilt_min']}°, {self.ptz_limits['tilt_max']}°]:", self.tilt_input)
        ptz_absolute_layout.addRow(self.current_pan_label, self.current_tilt_label)
        ptz_absolute_layout.addWidget(goto_btn)
        
        ptz_main_layout.addLayout(ptz_directional_layout)
        ptz_main_layout.addLayout(ptz_absolute_layout)
        ptz_main_box.setLayout(ptz_main_layout)

        thermal_box = QGroupBox("Termal Ayarlar")
        thermal_layout = QVBoxLayout()
        temp_info_layout = QFormLayout()
        self.cursor_temp_label = QLabel("-")
        self.hot_area_temp_label = QLabel("-")
        self.cold_area_temp_label = QLabel("-")
        temp_info_layout.addRow("İmleç Sıcaklığı:", self.cursor_temp_label)
        temp_info_layout.addRow("Sıcak Alan Ort.:", self.hot_area_temp_label)
        temp_info_layout.addRow("Soğuk Alan Ort.:", self.cold_area_temp_label)
        
        coloring_layout = QFormLayout()
        self.above_thresh_input = QLineEdit()
        self.between_min_input = QLineEdit()
        self.between_max_input = QLineEdit()
        update_coloring_btn = QPushButton("Hedef Renklendirmeyi Güncelle")
        update_coloring_btn.clicked.connect(self.update_thermal_coloring_rules)
        coloring_layout.addRow("Kırmızı (> Eşik °C):", self.above_thresh_input)
        coloring_layout.addRow("Mavi (Min °C):", self.between_min_input)
        coloring_layout.addRow("Mavi (Maks °C):", self.between_max_input)
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
        self.thread_pixel_data = PixelDataThread()
        
        self.thread_normal.change_pixmap_signal.connect(self.update_image1)
        self.thread_thermal.change_pixmap_signal.connect(self.update_image2)
        
        self.thread_normal.connection_status_signal.connect(lambda status: self.camera1_label.setText(status) if "Hata" in status else None)
        self.thread_thermal.connection_status_signal.connect(lambda status: self.camera2_label.setText(status) if "Hata" in status else None)
        
        self.thread_pixel_data.pixel_data_ready.connect(self.update_pixel_data_matrix)
        
        self.thread_normal.start()
        self.thread_thermal.start()
        self.thread_pixel_data.start()

    def load_initial_data(self):
        self.load_initial_thermal_rules()
        self.ptz_status_thread_active = True
        threading.Thread(target=self.update_ptz_status_loop, daemon=True).start()

    def load_initial_thermal_rules(self):
        print("Mevcut hedef renklendirme kuralları yükleniyor...")
        try:
            response = requests.get(THERMAL_ALARM_RULES_URL, auth=self.auth, timeout=3)
            if response.status_code == 200:
                root = ET.fromstring(response.content)
                ns = {'isapi': 'http://www.isapi.org/ver20/XMLSchema'}
                above_node = root.find('.//isapi:ThermometryAlarmMode[isapi:rule="highestGreater"]/isapi:alarm', ns)
                if above_node is not None: self.above_thresh_input.setText(above_node.text)

                print("Hedef renklendirme kuralları başarıyla yüklendi.")
            else:
                print(f"Kural yükleme hatası: {response.status_code}")
        except Exception as e:
            print(f"Hedef renklendirme kuralları yüklenemedi: {e}")

    def update_thermal_coloring_rules(self):
        print("Hedef renklendirme kuralları güncelleniyor...")
        try:
            response_get = requests.get(THERMAL_ALARM_RULES_URL, auth=self.auth, timeout=3)
            if response_get.status_code != 200: return
            
            root = ET.fromstring(response_get.content)
            ns = {'isapi': 'http://www.isapi.org/ver20/XMLSchema'}

            above_node = root.find('.//isapi:ThermometryAlarmMode[isapi:rule="highestGreater"]/isapi:alarm', ns)
            if above_node is not None: above_node.text = self.above_thresh_input.text()
            
            updated_xml = ET.tostring(root, encoding='UTF-8')
            headers = {'Content-Type': 'application/xml'}
            response_put = requests.put(THERMAL_ALARM_RULES_URL, auth=self.auth, data=updated_xml, headers=headers)
            
            if response_put.status_code == 200:
                print("Hedef renklendirme başarıyla güncellendi.")
            else:
                print(f"Güncelleme Hatası: {response_put.status_code} - {response_put.text}")
        except Exception as e:
            print(f"Güncelleme sırasında hata: {e}")
        
    def update_ptz_status_loop(self):
        while getattr(self, 'ptz_status_thread_active', False):
            try:
                response = requests.get(PTZ_STATUS_URL, auth=self.auth, timeout=1)
                if response.status_code == 200:
                    root = ET.fromstring(response.content)
                    ns = {'isapi': 'http://www.isapi.org/ver20/XMLSchema'}
                    azimuth = float(root.find('.//isapi:azimuth', ns).text) / 10.0
                    elevation = float(root.find('.//isapi:elevation', ns).text) / 10.0
                    self.current_pan_label.setText(f"Mevcut P: {azimuth:.1f}°")
                    self.current_tilt_label.setText(f"Mevcut T: {elevation:.1f}°")
            except:
                pass
            time.sleep(1)

    def degree_to_onvif_accurate(self, pan_deg, tilt_deg):
        pan_range = self.ptz_limits['pan_max'] - self.ptz_limits['pan_min']
        tilt_range = self.ptz_limits['tilt_max'] - self.ptz_limits['tilt_min']
        onvif_pan = ((pan_deg - self.ptz_limits['pan_min']) / pan_range) * 2.0 - 1.0 if pan_range != 0 else 0
        onvif_tilt = ((tilt_deg - self.ptz_limits['tilt_min']) / tilt_range) * 2.0 - 1.0 if tilt_range != 0 else 0
        return max(-1.0, min(1.0, onvif_pan)), max(-1.0, min(1.0, onvif_tilt))

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
            pan_deg = float(self.pan_input.text())
            tilt_deg = float(self.tilt_input.text())
            onvif_pan, onvif_tilt = self.degree_to_onvif_accurate(pan_deg, tilt_deg)
            req = self.ptz.create_type('AbsoluteMove')
            req.ProfileToken = self.token
            req.Position = {'PanTilt': {'x': onvif_pan, 'y': onvif_tilt}}
            self.ptz.AbsoluteMove(req)
        except Exception as e:
            print(f"Pozisyonlama hatası: {e}")
            
    @pyqtSlot(QImage)
    def update_image1(self, qt_img): self.camera1_label.setPixmap(QPixmap.fromImage(qt_img))
    
    @pyqtSlot(QImage)
    def update_image2(self, qt_img): self.camera2_label.setPixmap(QPixmap.fromImage(qt_img))

    @pyqtSlot(np.ndarray)
    def update_pixel_data_matrix(self, data_matrix):
        self.pixel_data_matrix = data_matrix
        if data_matrix is not None:
            temps_celsius = data_matrix.astype(np.float32) / 100.0
            self.last_max_temp = np.max(temps_celsius)
            max_index = np.unravel_index(np.argmax(data_matrix, axis=None), data_matrix.shape)
            self.hotspot_coords = (max_index[1] / THERMAL_SENSOR_WIDTH, max_index[0] / THERMAL_SENSOR_HEIGHT)
            try:
                above_thresh = float(self.above_thresh_input.text())
                hot_pixels = temps_celsius[temps_celsius > above_thresh]
                self.hot_area_temp_label.setText(f"{np.mean(hot_pixels):.1f} °C" if hot_pixels.size > 0 else "-")
                
                cold_pixels = temps_celsius[temps_celsius < (above_thresh-10)] # Örnek olarak kırmızıdan 10 derece daha soğuk
                self.cold_area_temp_label.setText(f"{np.mean(cold_pixels):.1f} °C" if cold_pixels.size > 0 else "-")
            except (ValueError, TypeError):
                self.hot_area_temp_label.setText("Geçersiz Eşik")
                self.cold_area_temp_label.setText("Geçersiz Eşik")
    
    def thermal_image_mouse_move(self, event):
        if self.pixel_data_matrix is None: return
        x, y = event.pos().x(), event.pos().y()
        label_w, label_h = self.camera2_label.width(), self.camera2_label.height()
        sensor_x = int((x / label_w) * THERMAL_SENSOR_WIDTH)
        sensor_y = int((y / label_h) * THERMAL_SENSOR_HEIGHT)
        if 0 <= sensor_x < THERMAL_SENSOR_WIDTH and 0 <= sensor_y < THERMAL_SENSOR_HEIGHT:
            actual_temp = self.pixel_data_matrix[sensor_y, sensor_x] / 100.0
            self.cursor_temp_label.setText(f"{actual_temp:.1f} °C")

    def closeEvent(self, event):
        print("Uygulama kapatılıyor...")
        self.ptz_status_thread_active = False
        self.rotating = False
        if self.ptz: self.ptz.Stop({'ProfileToken': self.token})
        self.thread_normal.stop()
        self.thread_thermal.stop()
        self.thread_pixel_data.stop()
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = PTZControlApp()
    window.show()
    sys.exit(app.exec_())