import sys
import os
import cv2
import time
import threading
import requests
import json
import xml.etree.ElementTree as ET
import numpy as np
from PyQt5.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton, QVBoxLayout,
    QHBoxLayout, QGridLayout, QGroupBox, QLineEdit, QFormLayout,
    QSpinBox, QListWidget  # QListWidget eklendi
)
from PyQt5.QtGui import QImage, QPixmap, QColor # QColor eklendi
from PyQt5.QtCore import Qt, QThread, pyqtSignal, pyqtSlot
from onvif import ONVIFCamera
from requests.auth import HTTPDigestAuth

# HATA DÜZELTME: OpenCV'nin RTSP için TCP kullanmasını sağla (video akışı stabilitesini artırır)
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"

# === KAMERA BİLGİLERİ ===
CAMERA_IP = '192.168.1.64'
CAMERA_PORT = 80
CAMERA_USER = 'admin'
CAMERA_PASS = 'ErenEnerji'

# === SABİTLER ===
MAX_LOG_ENTRIES = 50 # Günlükte tutulacak maksimum kayıt sayısı

# === API ve RTSP URL'leri ===
RTSP_URL_NORMAL = f'rtsp://{CAMERA_USER}:{CAMERA_PASS}@{CAMERA_IP}:554/Streaming/Channels/101'
RTSP_URL_THERMAL = f'rtsp://{CAMERA_USER}:{CAMERA_PASS}@{CAMERA_IP}:554/Streaming/Channels/201'
REALTIME_THERMOMETRY_URL = f'http://{CAMERA_IP}/ISAPI/Thermal/channels/2/thermometry/realTimethermometry/rules?format=json'
PTZ_STATUS_URL = f'http://{CAMERA_IP}/ISAPI/PTZCtrl/channels/1/status'

# === Hata Yönetimli Video Thread ===
class RTSPVideoThread(QThread):
    # ... (Bu sınıfta değişiklik yok, aynı kalıyor)
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
        while self._run_flag:
            cap = None
            try:
                cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
                if not cap.isOpened():
                    self.connection_status_signal.emit(f"{self.stream_name}: Bağlantı Hatası")
                    time.sleep(5)
                    continue

                self.connection_status_signal.emit(f"{self.stream_name}: Bağlandı")
                
                while self._run_flag:
                    ret, frame = cap.read()
                    if not ret or frame is None:
                        self.connection_status_signal.emit(f"{self.stream_name}: Veri Alınamıyor...")
                        break
                    
                    if len(frame.shape) < 3: continue

                    with self.parent_ui.frame_lock:
                        if self.is_thermal:
                            self.parent_ui.latest_thermal_frame = frame.copy()
                        else:
                            self.parent_ui.latest_normal_frame = frame.copy()

                    h_frame, w_frame, _ = frame.shape

                    if self.is_thermal:
                        if self.parent_ui.thermal_hotspot_coords and self.parent_ui.last_max_temp is not None:
                            x, y = self.parent_ui.thermal_hotspot_coords
                            px, py = int(x * w_frame), int(y * h_frame)
                            cv2.drawMarker(frame, (px, py), (0, 0, 255), cv2.MARKER_CROSS, 20, 2)
                            temp_text = f"MAKS: {self.parent_ui.last_max_temp:.1f} C"
                            cv2.putText(frame, temp_text, (px + 15, py - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
                        
                        if self.parent_ui.thermal_coldspot_coords and self.parent_ui.last_min_temp is not None:
                            x, y = self.parent_ui.thermal_coldspot_coords
                            px, py = int(x * w_frame), int(y * h_frame)
                            cv2.drawMarker(frame, (px, py), (255, 0, 0), cv2.MARKER_CROSS, 20, 2)
                            temp_text = f"MIN: {self.parent_ui.last_min_temp:.1f} C"
                            cv2.putText(frame, temp_text, (px + 15, py + 15), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 0, 0), 2)
                    
                    rgb_image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    qt_img = QImage(rgb_image.data, w_frame, h_frame, 3 * w_frame, QImage.Format_RGB888)
                    scaled_img = qt_img.scaled(640, 360, Qt.KeepAspectRatio)
                    self.change_pixmap_signal.emit(scaled_img)
            except Exception as e:
                print(f"HATA ({self.stream_name} Thread): {e}")
                self.connection_status_signal.emit(f"{self.stream_name}: Thread Çöktü")
                time.sleep(5)
            finally:
                if cap:
                    cap.release()
        print(f"{self.stream_name} video thread durdu.")

    def stop(self):
        self._run_flag = False
        self.wait()

# === Gerçek Zamanlı Termal Veri Alan Thread ===
class ThermalDataThread(QThread):
    # ... (Bu sınıfta değişiklik yok, aynı kalıyor)
    thermal_data_updated = pyqtSignal(dict)
    connection_status = pyqtSignal(str)

    def __init__(self, url, user, password):
        super().__init__()
        self._run_flag = True
        self.url = url
        self.auth = HTTPDigestAuth(user, password)

    def run(self):
        while self._run_flag:
            try:
                with requests.get(self.url, auth=self.auth, stream=True, timeout=(5, 65)) as response:
                    if response.status_code == 200:
                        self.connection_status.emit("Termal Veri: Bağlandı")
                        buffer = b''
                        for chunk in response.iter_content(chunk_size=1024):
                            if not self._run_flag: break
                            buffer += chunk
                            while b'--boundary' in buffer:
                                parts = buffer.split(b'--boundary', 1)
                                block, buffer = parts[0], parts[1]
                                if b'Content-Type: application/json' in block:
                                    json_start = block.find(b'{')
                                    json_end = block.rfind(b'}')
                                    if json_start != -1 and json_end != -1:
                                        json_str = block[json_start:json_end+1].decode('utf-8')
                                        try:
                                            self.thermal_data_updated.emit(json.loads(json_str))
                                        except json.JSONDecodeError:
                                            pass
                    else:
                        self.connection_status.emit(f"Termal Veri: Hata {response.status_code}")
                        time.sleep(5)
            except requests.exceptions.RequestException as e:
                print(f"Termal Veri Thread Bağlantı Hatası: {e}")
                self.connection_status.emit("Termal Veri: Bağlantı Hatası")
                time.sleep(5)
        print("Termal Veri Thread durdu.")

    def stop(self):
        self._run_flag = False
        self.wait()

# === ANA GUI SINIFI ===
class PTZControlApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Kalibrasyonlu Termal PTZ Kontrol Paneli")
        self.setGeometry(100, 100, 1400, 1050) # Pencere yüksekliği artırıldı
        
        self.rotating = False
        self.ptz = None
        self.auth = HTTPDigestAuth(CAMERA_USER, CAMERA_PASS)
        
        self.last_max_temp = None
        self.last_min_temp = None
        self.thermal_hotspot_coords = None
        self.thermal_coldspot_coords = None
        self.ptz_limits = {'pan_min': 0, 'pan_max': 360, 'tilt_min': -90, 'tilt_max': 90}
        
        self.latest_normal_frame = None
        self.latest_thermal_frame = None
        self.frame_lock = threading.Lock()
        self.last_alarm_time = 0
        self.alarms_base_dir = "alarms"
        os.makedirs(self.alarms_base_dir, exist_ok=True)
        
        # === YENİ: ALARM GEÇMİŞİ İÇİN LİSTE ===
        self.alarm_history = []
        # ======================================
        
        self.init_onvif()
        self.init_ui()

    def showEvent(self, event):
        # ... (Bu fonksiyonda değişiklik yok)
        super().showEvent(event)
        if not hasattr(self, 'threads_started') or not self.threads_started:
            self.init_threads()
            self.threads_started = True
            threading.Timer(1.0, self.load_initial_data).start()

    def init_onvif(self):
        # ... (Bu fonksiyonda değişiklik yok)
        try:
            # WSDL dosyalarının konumu belirtilebilir, bu bazı ağlarda bağlantıyı hızlandırır
            wsdl_dir = os.path.join(os.getcwd(), 'wsdl')
            if not os.path.exists(wsdl_dir):
                os.makedirs(wsdl_dir)
            self.cam = ONVIFCamera(CAMERA_IP, CAMERA_PORT, CAMERA_USER, CAMERA_PASS, wsdl_dir)
            self.ptz = self.cam.create_ptz_service()
            media_service = self.cam.create_media_service()
            self.profile = media_service.GetProfiles()[0]
            self.token = self.profile.token
            ptz_config_options = self.ptz.GetConfigurationOptions({'ConfigurationToken': self.profile.PTZConfiguration.token})
            if ptz_config_options.Spaces and ptz_config_options.Spaces.AbsolutePanTiltPositionSpace:
                pan_limits = ptz_config_options.Spaces.AbsolutePanTiltPositionSpace[0].XRange
                tilt_limits = ptz_config_options.Spaces.AbsolutePanTiltPositionSpace[0].YRange
                self.ptz_limits.update({'pan_min': pan_limits.Min, 'pan_max': pan_limits.Max, 'tilt_min': tilt_limits.Min, 'tilt_max': tilt_limits.Max})
            print(f"ONVIF bağlantısı başarılı. Gerçek PTZ Limitleri: Pan [{self.ptz_limits['pan_min']:.2f}, {self.ptz_limits['pan_max']:.2f}], Tilt [{self.ptz_limits['tilt_min']:.2f}, {self.ptz_limits['tilt_max']:.2f}]")
        except Exception as e:
            print(f"ONVIF bağlantı/limit alma hatası: {e}. Varsayılan limitler kullanılacak.")
            self.ptz = None

    def init_ui(self):
        main_layout = QHBoxLayout()
        camera_layout = QVBoxLayout()
        self.camera1_label = QLabel("Normal Kamera Yükleniyor...")
        self.camera2_label = QLabel("Termal Kamera Yükleniyor...")
        self.camera1_label.setFixedSize(640, 360)
        self.camera2_label.setFixedSize(640, 360)
        self.camera1_label.setStyleSheet("background-color: black; color: white; border: 1px solid gray; font-size: 16px; qproperty-alignment: 'AlignCenter';")
        self.camera2_label.setStyleSheet("background-color: black; color: white; border: 1px solid gray; font-size: 16px; qproperty-alignment: 'AlignCenter';")
        camera_layout.addWidget(self.camera1_label)
        camera_layout.addWidget(self.camera2_label)
        
        right_panel_layout = QVBoxLayout()
        
        # PTZ Kutusu (değişiklik yok)
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
        self.pan_input_label = QLabel(f"Pan [{self.ptz_limits['pan_min']:.1f}°, {self.ptz_limits['pan_max']:.1f}°]:")
        self.tilt_input_label = QLabel(f"Tilt [{self.ptz_limits['tilt_min']:.1f}°, {self.ptz_limits['tilt_max']:.1f}°]:")
        ptz_absolute_layout.addRow(self.pan_input_label, self.pan_input)
        ptz_absolute_layout.addRow(self.tilt_input_label, self.tilt_input)
        ptz_absolute_layout.addRow(self.current_pan_label, self.current_tilt_label)
        ptz_absolute_layout.addWidget(goto_btn)
        ptz_main_layout.addLayout(ptz_directional_layout)
        ptz_main_layout.addLayout(ptz_absolute_layout)
        ptz_main_box.setLayout(ptz_main_layout)

        # Termal Veri Kutusu (değişiklik yok)
        thermal_box = QGroupBox("Termal Veri")
        temp_info_layout = QFormLayout()
        self.temp_avg_label = QLabel("-")
        self.max_point_temp_label = QLabel("-")
        self.max_point_pos_label = QLabel("-")
        self.min_point_temp_label = QLabel("-")
        self.min_point_pos_label = QLabel("-")
        temp_info_layout.addRow("Bölge Ort. Sıcaklık:", self.temp_avg_label)
        separator_style = "font-weight: bold; margin-top: 5px; color: red;"
        max_label = QLabel("--- En Sıcak Nokta ---")
        max_label.setStyleSheet(separator_style)
        temp_info_layout.addRow(max_label)
        temp_info_layout.addRow("Sıcaklık:", self.max_point_temp_label)
        temp_info_layout.addRow("Pozisyon (X, Y):", self.max_point_pos_label)
        min_label_style = "font-weight: bold; margin-top: 5px; color: cyan;"
        min_label = QLabel("--- En Soğuk Nokta ---")
        min_label.setStyleSheet(min_label_style)
        temp_info_layout.addRow(min_label)
        temp_info_layout.addRow("Sıcaklık:", self.min_point_temp_label)
        temp_info_layout.addRow("Pozisyon (X, Y):", self.min_point_pos_label)
        thermal_box.setLayout(temp_info_layout)
        
        # Alarm Ayarları Kutusu (değişiklik yok)
        alarm_box = QGroupBox("Alarm ve Kayıt Ayarları")
        alarm_layout = QFormLayout()
        self.alarm_threshold_input = QLineEdit("80.0")
        self.alarm_cooldown_input = QSpinBox()
        self.alarm_cooldown_input.setRange(5, 300)
        self.alarm_cooldown_input.setValue(30)
        self.alarm_cooldown_input.setSuffix(" sn")
        self.alarm_dir_input = QLineEdit(self.alarms_base_dir)
        self.alarm_dir_input.textChanged.connect(self.update_alarm_dir)
        self.last_alarm_label = QLabel("Henüz alarm yok.")
        self.last_alarm_label.setWordWrap(True)
        alarm_layout.addRow("Alarm Eşiği (> °C):", self.alarm_threshold_input)
        alarm_layout.addRow("Alarm Sonrası Bekleme:", self.alarm_cooldown_input)
        alarm_layout.addRow("Kayıt Klasörü:", self.alarm_dir_input)
        alarm_layout.addRow("Son Alarm Durumu:", self.last_alarm_label)
        alarm_box.setLayout(alarm_layout)

        # === YENİ: ALARM GÜNLÜĞÜ KUTUSU ===
        log_box = QGroupBox("Alarm Günlüğü")
        log_layout = QVBoxLayout()
        self.alarm_log_widget = QListWidget()
        self.alarm_log_widget.setFixedHeight(150) # Liste kutusunun yüksekliğini ayarla
        log_layout.addWidget(self.alarm_log_widget)
        log_box.setLayout(log_layout)
        # ==================================

        right_panel_layout.addWidget(ptz_main_box)
        right_panel_layout.addWidget(thermal_box)
        right_panel_layout.addWidget(alarm_box)
        right_panel_layout.addWidget(log_box) # Yeni kutuyu arayüze ekle
        right_panel_layout.addStretch()

        main_layout.addLayout(camera_layout)
        main_layout.addLayout(right_panel_layout)
        self.setLayout(main_layout)
    
    # === YENİ: ALARM GÜNLÜĞÜ WIDGET'INI GÜNCELLEYEN FONKSİYON ===
    def update_alarm_log_widget(self):
        self.alarm_log_widget.clear()
        self.alarm_log_widget.addItems(self.alarm_history)
        # En son eklenen (listenin en başındaki) alarmı kırmızı yap
        if self.alarm_log_widget.count() > 0:
            self.alarm_log_widget.item(0).setForeground(QColor('red'))
    # ========================================================

    # ... Diğer fonksiyonlar (init_threads, ptz hareketleri vb.) ...
    def init_threads(self):
        self.thread_normal = RTSPVideoThread(RTSP_URL_NORMAL, False, self)
        self.thread_thermal = RTSPVideoThread(RTSP_URL_THERMAL, True, self)
        self.thread_thermal_data = ThermalDataThread(REALTIME_THERMOMETRY_URL, CAMERA_USER, CAMERA_PASS)
        
        self.thread_normal.change_pixmap_signal.connect(self.update_image1)
        self.thread_thermal.change_pixmap_signal.connect(self.update_image2)
        
        self.thread_normal.connection_status_signal.connect(lambda s: self.camera1_label.setText(s) if "Hata" in s or "Çöktü" in s else None)
        self.thread_thermal.connection_status_signal.connect(lambda s: self.camera2_label.setText(s) if "Hata" in s or "Çöktü" in s else None)
        
        self.thread_thermal_data.thermal_data_updated.connect(self.update_thermal_data)
        
        self.thread_normal.start()
        self.thread_thermal.start()
        self.thread_thermal_data.start()

    def load_initial_data(self):
        self.ptz_status_thread_active = True
        threading.Thread(target=self.update_ptz_status_loop, daemon=True).start()

    def update_alarm_dir(self, text):
        self.alarms_base_dir = text
        os.makedirs(self.alarms_base_dir, exist_ok=True)

    def update_ptz_status_loop(self):
        while getattr(self, 'ptz_status_thread_active', False):
            try:
                response = requests.get(PTZ_STATUS_URL, auth=self.auth, timeout=1)
                if response.status_code == 200:
                    root = ET.fromstring(response.content)
                    # Namespace'i dinamik olarak bulmak daha sağlamdır
                    ns_map = {node[0]: node[1] for _, node in ET.iterparse(response.content, events=['start-ns'])}
                    ns = ns_map.get('', 'http://www.isapi.org/ver20/XMLSchema') # Varsayılan namespace
                    
                    azimuth_node = root.find(f'.//{{{ns}}}azimuth')
                    elevation_node = root.find(f'.//{{{ns}}}elevation')
                    
                    if azimuth_node is not None and elevation_node is not None:
                        azimuth = float(azimuth_node.text) / 10.0
                        elevation = float(elevation_node.text) / 10.0
                        self.current_pan_label.setText(f"Mevcut P: {azimuth:.1f}°")
                        self.current_tilt_label.setText(f"Mevcut T: {elevation:.1f}°")
            except Exception:
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
        btn.pressed.connect(lambda: self.move_camera_start(pan, tilt))
        btn.released.connect(self.move_camera_stop)
        return btn

    def create_rotate_button(self, label):
        btn = QPushButton(label)
        btn.setFixedSize(80, 60)
        btn.setCheckable(True)
        btn.clicked.connect(self.toggle_rotate)
        return btn
        
    def move_camera_start(self, pan, tilt):
        if not self.ptz: return
        try:
            req = self.ptz.create_type('ContinuousMove')
            req.ProfileToken = self.token
            req.Velocity = {'PanTilt': {'x': pan, 'y': tilt, 'Zoom':0}}
            self.ptz.ContinuousMove(req)
        except Exception as e:
            print(f"Sürekli hareket hatası: {e}")

    def move_camera_stop(self):
        if not self.ptz: return
        try:
            self.ptz.Stop({'ProfileToken': self.token})
        except Exception as e:
            print(f"Durdurma hatası: {e}")

    def toggle_rotate(self, checked):
        self.rotating = checked
        if self.rotating:
            self.move_camera_start(0.2, 0)
        else:
            self.move_camera_stop()
            
    def go_to_absolute_position(self):
        if not self.ptz: return
        try:
            pan_deg, tilt_deg = float(self.pan_input.text()), float(self.tilt_input.text())
            onvif_pan, onvif_tilt = self.degree_to_onvif_accurate(pan_deg, tilt_deg)
            req = self.ptz.create_type('AbsoluteMove')
            req.ProfileToken = self.token
            req.Position = {'PanTilt': {'x': onvif_pan, 'y': onvif_tilt, 'Zoom':0}}
            self.ptz.AbsoluteMove(req)
        except Exception as e: print(f"Pozisyonlama hatası: {e}")
            
    @pyqtSlot(QImage)
    def update_image1(self, qt_img): self.camera1_label.setPixmap(QPixmap.fromImage(qt_img))
    
    @pyqtSlot(QImage)
    def update_image2(self, qt_img): self.camera2_label.setPixmap(QPixmap.fromImage(qt_img))

    @pyqtSlot(dict)
    def update_thermal_data(self, data):
        # ... (Bu fonksiyonda değişiklik yok)
        try:
            upload_list = data.get('ThermometryUploadList', {}).get('ThermometryUpload', [])
            if not upload_list: return
            
            therm_data = upload_list[0]
            
            cfg_data = therm_data.get('LinePolygonThermCfg', {})
            avg_temp = cfg_data.get('AverageTemperature')
            max_temp = cfg_data.get('MaxTemperature')
            min_temp = cfg_data.get('MinTemperature')
            
            self.last_max_temp = max_temp
            self.last_min_temp = min_temp

            hotspot_node = therm_data.get('HighestPoint')
            coldspot_node = therm_data.get('LowestPoint')
            
            self.temp_avg_label.setText(f"{avg_temp:.1f} °C" if avg_temp is not None else "-")

            if max_temp is not None and hotspot_node:
                self.thermal_hotspot_coords = (hotspot_node.get('positionX', 0), hotspot_node.get('positionY', 0))
                self.max_point_temp_label.setText(f"{max_temp:.1f} °C")
                self.max_point_pos_label.setText(f"X: {self.thermal_hotspot_coords[0]:.3f}, Y: {self.thermal_hotspot_coords[1]:.3f}")
            else:
                self.thermal_hotspot_coords = None
                self.max_point_temp_label.setText("-")
                self.max_point_pos_label.setText("-")

            if min_temp is not None and coldspot_node:
                self.thermal_coldspot_coords = (coldspot_node.get('positionX', 0), coldspot_node.get('positionY', 0))
                self.min_point_temp_label.setText(f"{min_temp:.1f} °C")
                self.min_point_pos_label.setText(f"X: {self.thermal_coldspot_coords[0]:.3f}, Y: {self.thermal_coldspot_coords[1]:.3f}")
            else:
                self.thermal_coldspot_coords = None
                self.min_point_temp_label.setText("-")
                self.min_point_pos_label.setText("-")
                
            self.check_for_alarm()

        except Exception as e: 
            print(f"Termal JSON işleme hatası: {e}")
            
    def check_for_alarm(self):
        if self.last_max_temp is None:
            return

        try:
            alarm_threshold = float(self.alarm_threshold_input.text())
        except ValueError:
            return

        cooldown_seconds = self.alarm_cooldown_input.value()
        if time.time() - self.last_alarm_time < cooldown_seconds:
            return

        if self.last_max_temp > alarm_threshold:
            print(f"ALARM! Maksimum sıcaklık {self.last_max_temp:.1f}°C, eşik olan {alarm_threshold}°C değerini aştı.")
            
            with self.frame_lock:
                normal_frame_to_save = self.latest_normal_frame
                thermal_frame_to_save = self.latest_thermal_frame

            if normal_frame_to_save is not None and thermal_frame_to_save is not None:
                timestamp_str_folder = time.strftime("%Y-%m-%d_%H-%M-%S")
                alarm_folder_path = os.path.join(self.alarms_base_dir, timestamp_str_folder)
                os.makedirs(alarm_folder_path, exist_ok=True)

                normal_image_path = os.path.join(alarm_folder_path, "normal.jpg")
                thermal_image_path = os.path.join(alarm_folder_path, "thermal.jpg")

                cv2.imwrite(normal_image_path, normal_frame_to_save)
                cv2.imwrite(thermal_image_path, thermal_frame_to_save)
                
                print(f"Görüntüler şuraya kaydedildi: {alarm_folder_path}")
                self.last_alarm_label.setText(f"{timestamp_str_folder}\nSıcaklık: {self.last_max_temp:.1f}°C")
                self.last_alarm_time = time.time()
                
                # === YENİ: ALARM GÜNLÜĞÜNÜ GÜNCELLE ===
                timestamp_str_log = time.strftime("%H:%M:%S")
                log_message = f"{timestamp_str_log} - ALARM: {self.last_max_temp:.1f}°C"
                self.alarm_history.insert(0, log_message)
                # Listeyi sınırla
                if len(self.alarm_history) > MAX_LOG_ENTRIES:
                    self.alarm_history.pop()
                # Arayüzü güncelle
                self.update_alarm_log_widget()
                # ======================================

            else:
                print("Alarm tetiklendi ancak kaydedilecek görüntüler henüz mevcut değil.")
    
    def closeEvent(self, event):
        # ... (Bu fonksiyonda değişiklik yok)
        print("Uygulama kapatılıyor...")
        self.ptz_status_thread_active = False
        self.rotating = False
        self.move_camera_stop()
        
        self.thread_normal.stop()
        self.thread_thermal.stop()
        self.thread_thermal_data.stop()
        
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = PTZControlApp()
    window.show()
    sys.exit(app.exec_())