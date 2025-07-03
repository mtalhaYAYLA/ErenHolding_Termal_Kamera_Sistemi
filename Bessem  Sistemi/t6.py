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
    QSpinBox, QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox,
    QComboBox 
)
from PyQt5.QtGui import QImage, QPixmap, QColor, QIcon, QDoubleValidator
from PyQt5.QtCore import Qt, QThread, pyqtSignal, pyqtSlot, QTimer
from onvif import ONVIFCamera
from onvif.exceptions import ONVIFError
from requests.auth import HTTPDigestAuth

os.environ['QT_QPA_PLATFORM'] = 'xcb'
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"
os.environ['QT_XCB_GL_INTEGRATION'] = 'none'

# === KAMERA BİLGİLERİ ===
CAMERA_IP = '192.168.1.64'
CAMERA_PORT = 80
CAMERA_USER = 'admin'
CAMERA_PASS = 'ErenEnerji'

# === SABİTLER ===
MAX_LOG_ENTRIES = 50 
DISPLAY_WIDTH = 640
DISPLAY_HEIGHT = 360
NORMAL_FOV_WIDE_ANGLE = 60.0  
NORMAL_FOV_TELE_ANGLE = 2.5   
THERMAL_FOV_ANGLE = 15.0      

# === API ve RTSP URL'leri ===
RTSP_URL_NORMAL = f'rtsp://{CAMERA_USER}:{CAMERA_PASS}@{CAMERA_IP}:554/Streaming/Channels/101'
RTSP_URL_THERMAL = f'rtsp://{CAMERA_USER}:{CAMERA_PASS}@{CAMERA_IP}:554/Streaming/Channels/201'
REALTIME_THERMOMETRY_URL = f'http://{CAMERA_IP}/ISAPI/Thermal/channels/2/thermometry/realTimethermometry/rules?format=json'

class RTSPVideoThread(QThread):
    change_pixmap_signal = pyqtSignal(QImage); connection_status_signal = pyqtSignal(str)
    def __init__(self, rtsp_url, is_thermal=False, parent=None):
        super().__init__(); self._run_flag = True; self.rtsp_url = rtsp_url; self.is_thermal = is_thermal
        self.parent_ui = parent; self.stream_name = "Termal" if is_thermal else "Normal"
        self.draw_rect_coords = None

    def run(self):
        while self._run_flag:
            cap = None
            try:
                cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
                if not cap.isOpened(): self.connection_status_signal.emit(f"{self.stream_name}: Bağlantı Hatası"); time.sleep(5); continue
                self.connection_status_signal.emit(f"{self.stream_name}: Bağlandı")
                while self._run_flag:
                    ret, frame = cap.read()
                    if not ret or frame is None: self.connection_status_signal.emit(f"{self.stream_name}: Veri Alınamıyor..."); break
                    if len(frame.shape) < 3: continue
                    with self.parent_ui.frame_lock:
                        if self.is_thermal: self.parent_ui.latest_thermal_frame = frame.copy()
                        else: self.parent_ui.latest_normal_frame = frame.copy()
                    
                    resized_frame = cv2.resize(frame, (DISPLAY_WIDTH, DISPLAY_HEIGHT))
                    
                    # DEĞİŞTİRİLDİ: Sadece yeşil dikdörtgen çiziliyor, kırmızı kare kaldırıldı
                    if not self.is_thermal and self.draw_rect_coords:
                        x, y, w, h = map(int, self.draw_rect_coords)
                        cv2.rectangle(resized_frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
                        cv2.putText(resized_frame, 'Termal Goruntu Alani', (x, y - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

                    if self.is_thermal:
                        if self.parent_ui.thermal_hotspot_coords and self.parent_ui.last_max_temp is not None:
                            x, y = self.parent_ui.thermal_hotspot_coords; px, py = int(x * DISPLAY_WIDTH), int(y * DISPLAY_HEIGHT)
                            cv2.drawMarker(resized_frame, (px, py), (0, 0, 255), cv2.MARKER_CROSS, 20, 2)
                            cv2.putText(resized_frame, f"MAKS: {self.parent_ui.last_max_temp:.1f} C", (px + 15, py - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
                        if self.parent_ui.thermal_coldspot_coords and self.parent_ui.last_min_temp is not None:
                            x, y = self.parent_ui.thermal_coldspot_coords; px, py = int(x * DISPLAY_WIDTH), int(y * DISPLAY_HEIGHT)
                            cv2.drawMarker(resized_frame, (px, py), (255, 0, 0), cv2.MARKER_CROSS, 20, 2)
                            cv2.putText(resized_frame, f"MIN: {self.parent_ui.last_min_temp:.1f} C", (px + 15, py + 15), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 0, 0), 2)
                    
                    rgb_image = cv2.cvtColor(resized_frame, cv2.COLOR_BGR2RGB); h, w, ch = rgb_image.shape
                    qt_img = QImage(rgb_image.data, w, h, ch * w, QImage.Format_RGB888)
                    self.change_pixmap_signal.emit(qt_img)
            except Exception as e:
                print(f"HATA ({self.stream_name} Thread): {e}"); self.connection_status_signal.emit(f"{self.stream_name}: Thread Çöktü"); time.sleep(5)
            finally:
                if cap: cap.release()
        print(f"{self.stream_name} video thread durdu.")
    def stop(self): self._run_flag = False; self.wait()
    def update_rect_coords(self, new_coords): self.draw_rect_coords = new_coords

class ThermalDataThread(QThread):
    # ... Bu sınıfta değişiklik yok ...
    thermal_data_updated = pyqtSignal(dict); connection_status = pyqtSignal(str)
    def __init__(self, url, user, password):
        super().__init__(); self._run_flag = True; self.url = url; self.auth = HTTPDigestAuth(user, password)
    def run(self):
        while self._run_flag:
            try:
                with requests.get(self.url, auth=self.auth, stream=True, timeout=(5, 10)) as response:
                    if response.status_code == 200:
                        self.connection_status.emit("Termal Veri: Bağlandı"); buffer = b''
                        for chunk in response.iter_content(chunk_size=1024):
                            if not self._run_flag: break
                            buffer += chunk
                            while b'--boundary' in buffer:
                                parts = buffer.split(b'--boundary', 1); block, buffer = parts[0], parts[1]
                                if b'Content-Type: application/json' in block:
                                    json_start = block.find(b'{'); json_end = block.rfind(b'}')
                                    if json_start != -1 and json_end != -1:
                                        json_str = block[json_start:json_end+1].decode('utf-8')
                                        try: self.thermal_data_updated.emit(json.loads(json_str))
                                        except json.JSONDecodeError: pass
                    else:
                        self.connection_status.emit(f"Termal Veri: Hata {response.status_code}"); time.sleep(5)
            except requests.exceptions.RequestException:
                if self._run_flag: self.connection_status.emit("Termal Veri: Bağlantı Hatası"); time.sleep(5)
        print("Termal Veri Thread durdu.")
    def stop(self): self._run_flag = False

class PTZControlApp(QWidget):
    PAN_DEGREE_RANGE = (0, 360); TILT_DEGREE_RANGE = (-5, 90)
    ONVIF_RANGE = (-1.0, 1.0); ZOOM_RANGE = (0.0, 1.0)
    def __init__(self):
        super().__init__(); self.setWindowTitle("Kalibrasyonlu Termal PTZ Kontrol Paneli"); self.setGeometry(100, 100, 1400, 900)
        self.ptz = None; self.profile = None; self.token = None
        self.is_scanning_360 = False; self.is_interval_scanning = False
        self.scan_phase = "stopped"; self.scan_params = {}; self.scan_direction = 1
        self.last_max_temp = None; self.last_min_temp = None
        self.thermal_hotspot_coords = None; self.thermal_coldspot_coords = None
        self.last_ptz_status = None
        self.latest_normal_frame = None; self.latest_thermal_frame = None
        self.last_thermal_data = None; self.frame_lock = threading.Lock()
        self.last_alarm_time = 0; self.alarms_base_dir = "alarms"
        os.makedirs(self.alarms_base_dir, exist_ok=True); self.alarm_history = []
        self.init_onvif(); self.init_ui()

    def showEvent(self, event):
        super().showEvent(event)
        if not hasattr(self, 'threads_started') or not self.threads_started:
            self.init_threads(); self.threads_started = True

    def init_onvif(self):
        try:
            print("ONVIF: Kamera ile bağlantı kuruluyor..."); self.cam = ONVIFCamera(CAMERA_IP, CAMERA_PORT, CAMERA_USER, CAMERA_PASS)
            print("ONVIF: PTZ servisi oluşturuluyor..."); self.ptz = self.cam.create_ptz_service()
            print("ONVIF: Medya servisleri alınıyor..."); media_service = self.cam.create_media_service()
            profiles = media_service.GetProfiles()
            self.profile = next((p for p in profiles if hasattr(p, 'PTZConfiguration') and p.PTZConfiguration is not None), None)
            if not self.profile: print("HATA: Kamerada PTZ destekli bir medya profili bulunamadı!"); self.ptz = None; return
            self.token = self.profile.token; print(f"ONVIF bağlantısı başarılı. Profil: {self.profile.Name}")
        except Exception as e:
            print(f"ONVIF bağlantısı başarısız: {e}"); self.ptz = None
    
    def init_ui(self):
        main_layout = QHBoxLayout(); camera_layout = QVBoxLayout()
        self.camera1_label = QLabel("Normal Kamera Yükleniyor..."); self.camera2_label = QLabel("Termal Kamera Yükleniyor...")
        self.camera1_label.setFixedSize(DISPLAY_WIDTH, DISPLAY_HEIGHT); self.camera2_label.setFixedSize(DISPLAY_WIDTH, DISPLAY_HEIGHT)
        self.camera1_label.setStyleSheet("background-color: black; color: white; border: 1px solid gray; font-size: 16px; qproperty-alignment: 'AlignCenter';")
        self.camera2_label.setStyleSheet("background-color: black; color: white; border: 1px solid gray; font-size: 16px; qproperty-alignment: 'AlignCenter';")
        camera_layout.addWidget(self.camera1_label); camera_layout.addWidget(self.camera2_label); camera_layout.addStretch()
        right_panel_layout = QVBoxLayout()
        status_frame = QGroupBox("Anlık Kamera Durumu"); status_layout = QVBoxLayout()
        info_hbox = QHBoxLayout()
        self.current_pos_label = QLabel("Pan: --°, Tilt: --°, Zoom: --%"); self.current_pos_label.setStyleSheet("font-weight: bold; color: #33aaff;")
        self.current_speed_label = QLabel("Hız: --"); self.current_speed_label.setStyleSheet("font-weight: bold; color: #22ddaa;")
        info_hbox.addWidget(self.current_pos_label); info_hbox.addWidget(self.current_speed_label); status_layout.addLayout(info_hbox)
        status_frame.setLayout(status_layout)
        ptz_frame = QGroupBox("Manuel PTZ Kontrol"); ptz_layout_container = QVBoxLayout()
        manual_speed_layout = QHBoxLayout(); manual_speed_layout.addWidget(QLabel("Manuel Hız (0-1):"))
        self.manual_speed_input = QLineEdit("0.5"); self.manual_speed_input.setValidator(QDoubleValidator(0.01, 1.0, 2))
        manual_speed_layout.addWidget(self.manual_speed_input); ptz_layout_container.addLayout(manual_speed_layout)
        ptz_grid = QGridLayout()
        self.btn_up=QPushButton("↑"); self.btn_down=QPushButton("↓"); self.btn_left=QPushButton("←"); self.btn_right=QPushButton("→")
        self.btn_zoom_in = QPushButton("Zoom +"); self.btn_zoom_out = QPushButton("Zoom -")
        ptz_grid.addWidget(self.btn_up,0,1); ptz_grid.addWidget(self.btn_down,2,1); ptz_grid.addWidget(self.btn_left,1,0); ptz_grid.addWidget(self.btn_right,1,2)
        ptz_grid.addWidget(self.btn_zoom_in, 0, 3); ptz_grid.addWidget(self.btn_zoom_out, 2, 3); ptz_layout_container.addLayout(ptz_grid)
        self.btn_up.pressed.connect(lambda: self.handle_manual_move(0, 1, 0)); self.btn_down.pressed.connect(lambda: self.handle_manual_move(0, -1, 0))
        self.btn_left.pressed.connect(lambda: self.handle_manual_move(-1, 0, 0)); self.btn_right.pressed.connect(lambda: self.handle_manual_move(1, 0, 0))
        self.btn_zoom_in.pressed.connect(lambda: self.handle_manual_move(0, 0, 1)); self.btn_zoom_out.pressed.connect(lambda: self.handle_manual_move(0, 0, -1))
        ptz_buttons = [self.btn_up, self.btn_down, self.btn_left, self.btn_right, self.btn_zoom_in, self.btn_zoom_out]
        for btn in ptz_buttons: btn.released.connect(self.stop_ptz_move)
        self.scan_360_button = QPushButton("360° Tarama Başlat"); self.scan_360_button.setCheckable(True); self.scan_360_button.clicked.connect(self.toggle_360_scan)
        ptz_layout_container.addWidget(self.scan_360_button); ptz_frame.setLayout(ptz_layout_container)
        
        interval_scan_frame = QGroupBox("Adım Adım Sıcaklık Taraması"); interval_scan_layout = QVBoxLayout()
        self.scan_mode_combo = QComboBox(); self.scan_mode_combo.addItems(["Sadece X Ekseni (Pan)", "Sadece Y Ekseni (Tilt)", "X ve Y Birlikte (Izgara)"])
        self.scan_mode_combo.currentIndexChanged.connect(self.update_scan_ui); interval_scan_layout.addWidget(self.scan_mode_combo)
        self.scan_form_layout = QFormLayout()
        self.pan_start_input = QLineEdit("180"); self.pan_end_input = QLineEdit("270")
        self.tilt_start_input = QLineEdit("20"); self.tilt_end_input = QLineEdit("30")
        self.fixed_pan_input = QLineEdit("180"); self.fixed_tilt_input = QLineEdit("45")
        self.pan_start_row = self.scan_form_layout.addRow("Pan Başlangıç (X1°):", self.pan_start_input)
        self.pan_end_row = self.scan_form_layout.addRow("Pan Bitiş (X2°):", self.pan_end_input)
        self.tilt_start_row = self.scan_form_layout.addRow("Tilt Başlangıç (Y1°):", self.tilt_start_input)
        self.tilt_end_row = self.scan_form_layout.addRow("Tilt Bitiş (Y2°):", self.tilt_end_input)
        self.fixed_pan_row = self.scan_form_layout.addRow("Sabit Pan (X°):", self.fixed_pan_input)
        self.fixed_tilt_row = self.scan_form_layout.addRow("Sabit Tilt (Y°):", self.fixed_tilt_input)
        self.scan_step_input = QLineEdit("10"); self.scan_wait_input = QLineEdit("10")
        self.scan_form_layout.addRow("Adım Büyüklüğü (°):", self.scan_step_input); self.scan_form_layout.addRow("Bekleme Süresi (sn):", self.scan_wait_input)
        validator = QDoubleValidator(); self.pan_start_input.setValidator(validator); self.pan_end_input.setValidator(validator)
        self.tilt_start_input.setValidator(validator); self.tilt_end_input.setValidator(validator); self.fixed_pan_input.setValidator(validator)
        self.fixed_tilt_input.setValidator(validator); self.scan_step_input.setValidator(QDoubleValidator(1.0, 90.0, 1))
        self.scan_wait_input.setValidator(QDoubleValidator(1.0, 600.0, 1)); interval_scan_layout.addLayout(self.scan_form_layout)
        button_hbox = QHBoxLayout()
        self.btn_go_to_target = QPushButton("Hedefe Git"); self.btn_go_to_target.clicked.connect(self.go_to_target_position)
        self.interval_scan_button = QPushButton("Sıcaklık Taramasını Başlat"); self.interval_scan_button.setCheckable(True); self.interval_scan_button.clicked.connect(self.toggle_interval_scan)
        button_hbox.addWidget(self.btn_go_to_target); button_hbox.addWidget(self.interval_scan_button); interval_scan_layout.addLayout(button_hbox)
        interval_scan_frame.setLayout(interval_scan_layout)
        
        thermal_box = QGroupBox("Termal Veri"); temp_info_layout = QFormLayout()
        self.temp_avg_label = QLabel("-"); self.max_point_temp_label = QLabel("-"); self.max_point_pos_label = QLabel("-")
        self.min_point_temp_label = QLabel("-"); self.min_point_pos_label = QLabel("-")
        temp_info_layout.addRow("Bölge Ort. Sıcaklık:", self.temp_avg_label); temp_info_layout.addRow(QLabel("--- En Sıcak Nokta ---"))
        temp_info_layout.addRow("Sıcaklık:", self.max_point_temp_label); temp_info_layout.addRow("Pozisyon (Pan, Tilt):", self.max_point_pos_label)
        temp_info_layout.addRow(QLabel("--- En Soğuk Nokta ---")); temp_info_layout.addRow("Sıcaklık:", self.min_point_temp_label)
        temp_info_layout.addRow("Pozisyon (Pan, Tilt):", self.min_point_pos_label); thermal_box.setLayout(temp_info_layout)
        alarm_box = QGroupBox("Alarm ve Kayıt Ayarları"); alarm_layout = QFormLayout()
        self.alarm_threshold_input = QLineEdit("40.0"); self.alarm_cooldown_input = QSpinBox()
        self.alarm_cooldown_input.setRange(5, 300); self.alarm_cooldown_input.setValue(30); self.alarm_cooldown_input.setSuffix(" sn")
        self.alarm_dir_input = QLineEdit(self.alarms_base_dir); self.alarm_dir_input.textChanged.connect(self.update_alarm_dir)
        self.last_alarm_label = QLabel("Henüz alarm yok."); self.last_alarm_label.setWordWrap(True)
        alarm_layout.addRow("Alarm Eşiği (> °C):", self.alarm_threshold_input); alarm_layout.addRow("Alarm Sonrası Bekleme:", self.alarm_cooldown_input)
        alarm_layout.addRow("Kayıt Klasörü:", self.alarm_dir_input); alarm_layout.addRow("Son Alarm Durumu:", self.last_alarm_label); alarm_box.setLayout(alarm_layout)
        log_box = QGroupBox("Alarm Günlüğü"); log_layout = QVBoxLayout()
        self.alarm_log_widget = QTableWidget(); self.alarm_log_widget.setColumnCount(4)
        self.alarm_log_widget.setHorizontalHeaderLabels(['Zaman', 'Kamera Adı', 'Alarm Tipi', 'Değer'])
        self.alarm_log_widget.setFixedHeight(100); self.alarm_log_widget.setEditTriggers(QTableWidget.NoEditTriggers)
        self.alarm_log_widget.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch); self.alarm_log_widget.verticalHeader().setVisible(False)
        log_layout.addWidget(self.alarm_log_widget); log_box.setLayout(log_layout)
        right_panel_layout.addWidget(status_frame); right_panel_layout.addWidget(ptz_frame);
        right_panel_layout.addWidget(interval_scan_frame); right_panel_layout.addWidget(thermal_box)
        right_panel_layout.addWidget(alarm_box); right_panel_layout.addWidget(log_box); right_panel_layout.addStretch()
        main_layout.addLayout(camera_layout); main_layout.addLayout(right_panel_layout)
        self.setLayout(main_layout)
        
        self.ptz_status_timer = QTimer(self); self.ptz_status_timer.timeout.connect(self.update_ptz_status_display)
        self.interval_scan_timer = QTimer(self); self.interval_scan_timer.timeout.connect(self.execute_interval_scan_step)
        self.update_scan_ui(0)
        self.set_controls_enabled(self.ptz is not None)

    def init_threads(self):
        self.thread_normal = RTSPVideoThread(RTSP_URL_NORMAL, False, self); self.thread_thermal = RTSPVideoThread(RTSP_URL_THERMAL, True, self)
        self.thread_thermal_data = ThermalDataThread(REALTIME_THERMOMETRY_URL, CAMERA_USER, CAMERA_PASS)
        self.thread_normal.change_pixmap_signal.connect(self.update_image1); self.thread_thermal.change_pixmap_signal.connect(self.update_image2)
        self.thread_normal.connection_status_signal.connect(lambda s: self.camera1_label.setText(s) if "Hata" in s or "Çöktü" in s else None)
        self.thread_thermal.connection_status_signal.connect(lambda s: self.camera2_label.setText(s) if "Hata" in s or "Çöktü" in s else None)
        self.thread_thermal_data.thermal_data_updated.connect(self.update_thermal_data)
        self.thread_normal.start(); self.thread_thermal.start(); self.thread_thermal_data.start()
        if self.ptz: self.ptz_status_timer.start(250)
            
    def update_alarm_log_widget(self):
        self.alarm_log_widget.setRowCount(0); self.alarm_log_widget.setRowCount(len(self.alarm_history))
        for row_index, alarm_record in enumerate(self.alarm_history):
            items = [QTableWidgetItem(alarm_record.get(k, '-')) for k in ['time', 'camera', 'type', 'value']]
            if row_index == 0:
                for item in items: item.setForeground(QColor('red'))
            for col_index, item in enumerate(items): self.alarm_log_widget.setItem(row_index, col_index, item)
            
    def update_alarm_dir(self, text): self.alarms_base_dir = text; os.makedirs(self.alarms_base_dir, exist_ok=True)
    def get_manual_speed(self):
        try: return max(0.01, min(1.0, float(self.manual_speed_input.text().replace(',', '.'))))
        except (ValueError, TypeError): return 0.5
    def set_current_speed(self, speed): self.current_speed_label.setText(f"Hız: {speed:.2f}")

    def handle_manual_move(self, pan_dir, tilt_dir, zoom_dir):
        if not self.ptz: return
        speed = self.get_manual_speed(); self.set_current_speed(speed)
        req = self.ptz.create_type('ContinuousMove'); req.ProfileToken = self.token
        req.Velocity = {'PanTilt': {'x': pan_dir * speed, 'y': tilt_dir * speed}, 'Zoom': {'x': zoom_dir * speed}}
        try: self.ptz.ContinuousMove(req)
        except ONVIFError as e: print(f"Manuel hareket hatası: {e}")

    def stop_ptz_move(self):
        if not self.ptz or self.is_scanning_360 or self.is_interval_scanning: return
        try: self.ptz.Stop({'ProfileToken': self.token}); self.set_current_speed(0.0)
        except ONVIFError as e: print(f"Durdurma hatası: {e}")

    def set_controls_enabled(self, enabled):
        if not hasattr(self, 'btn_up'): return
        ptz_widgets = [self.btn_up, self.btn_down, self.btn_left, self.btn_right, self.btn_zoom_in, self.btn_zoom_out, self.scan_360_button,
                       self.interval_scan_button, self.btn_go_to_target, self.pan_start_input, self.pan_end_input, self.tilt_start_input, self.tilt_end_input, 
                       self.fixed_pan_input, self.fixed_tilt_input, self.manual_speed_input, self.scan_step_input, self.scan_wait_input]
        for widget in ptz_widgets:
            if widget: widget.setEnabled(enabled)
        if not enabled: self.set_current_speed(0.0)
        
    def update_scan_ui(self, index):
        self.scan_form_layout.labelForField(self.pan_start_input).setVisible(False); self.pan_start_input.setVisible(False)
        self.scan_form_layout.labelForField(self.pan_end_input).setVisible(False); self.pan_end_input.setVisible(False)
        self.scan_form_layout.labelForField(self.tilt_start_input).setVisible(False); self.tilt_start_input.setVisible(False)
        self.scan_form_layout.labelForField(self.tilt_end_input).setVisible(False); self.tilt_end_input.setVisible(False)
        self.scan_form_layout.labelForField(self.fixed_pan_input).setVisible(False); self.fixed_pan_input.setVisible(False)
        self.scan_form_layout.labelForField(self.fixed_tilt_input).setVisible(False); self.fixed_tilt_input.setVisible(False)
        if index == 0: 
            self.scan_form_layout.labelForField(self.pan_start_input).setVisible(True); self.pan_start_input.setVisible(True)
            self.scan_form_layout.labelForField(self.pan_end_input).setVisible(True); self.pan_end_input.setVisible(True)
            self.scan_form_layout.labelForField(self.fixed_tilt_input).setVisible(True); self.fixed_tilt_input.setVisible(True)
        elif index == 1:
            self.scan_form_layout.labelForField(self.tilt_start_input).setVisible(True); self.tilt_start_input.setVisible(True)
            self.scan_form_layout.labelForField(self.tilt_end_input).setVisible(True); self.tilt_end_input.setVisible(True)
            self.scan_form_layout.labelForField(self.fixed_pan_input).setVisible(True); self.fixed_pan_input.setVisible(True)
        elif index == 2: 
            self.scan_form_layout.labelForField(self.pan_start_input).setVisible(True); self.pan_start_input.setVisible(True)
            self.scan_form_layout.labelForField(self.pan_end_input).setVisible(True); self.pan_end_input.setVisible(True)
            self.scan_form_layout.labelForField(self.tilt_start_input).setVisible(True); self.tilt_start_input.setVisible(True)
            self.scan_form_layout.labelForField(self.tilt_end_input).setVisible(True); self.tilt_end_input.setVisible(True)
            
    def _map_value(self, value, from_min, from_max, to_min, to_max):
        return to_min + (to_max - to_min) * (value - from_min) / (from_max - from_min) if (from_max - from_min) != 0 else to_min
    def _convert_ptz_to_degrees(self, ptz_pos):
        pan_onvif = ptz_pos['pan']; pan_deg = self._map_value(pan_onvif, 0, 1, 0, 180) if pan_onvif >= 0 else self._map_value(pan_onvif, -1, 0, 180, 360)
        tilt_deg = self._map_value(ptz_pos['tilt'], self.ONVIF_RANGE[0], self.ONVIF_RANGE[1], self.TILT_DEGREE_RANGE[1], self.TILT_DEGREE_RANGE[0])
        zoom_percent = self._map_value(ptz_pos['zoom'], self.ZOOM_RANGE[0], self.ZOOM_RANGE[1], 0, 100)
        return {'pan': pan_deg, 'tilt': tilt_deg, 'zoom': zoom_percent}
    def _convert_degrees_to_ptz(self, pan_deg, tilt_deg):
        pan_onvif = self._map_value(pan_deg, 0, 180, 0, 1) if 0 <= pan_deg <= 180 else self._map_value(pan_deg, 180, 360, -1, 0)
        tilt_onvif = self._map_value(tilt_deg, self.TILT_DEGREE_RANGE[0], self.TILT_DEGREE_RANGE[1], self.ONVIF_RANGE[1], self.ONVIF_RANGE[0])
        return pan_onvif, tilt_onvif

    def update_ptz_status_display(self):
        if not self.ptz: return
        try:
            pos_raw_status = self.ptz.GetStatus({'ProfileToken': self.token})
            if pos_raw_status and pos_raw_status.Position:
                self.last_ptz_status = pos_raw_status.Position
                pos_onvif = {'pan': self.last_ptz_status.PanTilt.x, 'tilt': self.last_ptz_status.PanTilt.y, 'zoom': self.last_ptz_status.Zoom.x}
                pos_deg = self._convert_ptz_to_degrees(pos_onvif)
                self.current_pos_label.setText(f"Pan: {pos_deg['pan']:.1f}°, Tilt: {pos_deg['tilt']:.1f}°, Zoom: {pos_deg['zoom']:.0f}%")
                self.update_dynamic_rectangle(pos_onvif['zoom'])
        except ONVIFError: pass

    def update_dynamic_rectangle(self, zoom_level):
        if not hasattr(self, 'thread_normal') or not self.thread_normal: return
        current_normal_angle = NORMAL_FOV_WIDE_ANGLE + (zoom_level * (NORMAL_FOV_TELE_ANGLE - NORMAL_FOV_WIDE_ANGLE))
        scale_ratio = THERMAL_FOV_ANGLE / current_normal_angle
        new_w = DISPLAY_WIDTH * scale_ratio; new_h = DISPLAY_HEIGHT * scale_ratio
        new_x = (DISPLAY_WIDTH - new_w) / 2; new_y = (DISPLAY_HEIGHT - new_h) / 2
        self.thread_normal.update_rect_coords((new_x, new_y, new_w, new_h))
    
    def toggle_360_scan(self, checked):
        if not self.ptz: return
        self.is_scanning_360 = checked
        self.interval_scan_button.setEnabled(not checked); self.btn_go_to_target.setEnabled(not checked)
        if checked:
            speed = self.get_manual_speed(); self.set_current_speed(speed)
            self.scan_360_button.setText("Taramayı Durdur")
            req = self.ptz.create_type('ContinuousMove'); req.ProfileToken = self.token
            req.Velocity = {'PanTilt': {'x': speed, 'y': 0}}; self.ptz.ContinuousMove(req)
        else:
            self.scan_360_button.setText("360° Tarama Başlat"); self.ptz.Stop({'ProfileToken': self.token}); self.set_current_speed(0.0)

    def toggle_interval_scan(self, checked):
        if not self.ptz: return
        self.is_interval_scanning = checked
        self.scan_360_button.setEnabled(not checked); self.btn_go_to_target.setEnabled(not checked)
        scan_mode = self.scan_mode_combo.currentIndex()
        if checked:
            try:
                self.scan_params = {'mode': scan_mode}
                if scan_mode == 0:
                    pan_start = float(self.pan_start_input.text()); pan_end = float(self.pan_end_input.text())
                    self.scan_params.update({'pan_min': min(pan_start, pan_end), 'pan_max': max(pan_start, pan_end), 'tilt_deg': float(self.fixed_tilt_input.text())})
                    self.scan_params['current_tilt'] = self.scan_params['tilt_deg']
                elif scan_mode == 1:
                    tilt_start = float(self.tilt_start_input.text()); tilt_end = float(self.tilt_end_input.text())
                    self.scan_params.update({'tilt_min': min(tilt_start, tilt_end), 'tilt_max': max(tilt_start, tilt_end), 'pan_deg': float(self.fixed_pan_input.text())})
                    self.scan_params['current_pan'] = self.scan_params['pan_deg']
                elif scan_mode == 2:
                    pan_start = float(self.pan_start_input.text()); pan_end = float(self.pan_end_input.text())
                    tilt_start = float(self.tilt_start_input.text()); tilt_end = float(self.tilt_end_input.text())
                    self.scan_params.update({'pan_min': min(pan_start, pan_end), 'pan_max': max(pan_start, pan_end), 'tilt_min': min(tilt_start, tilt_end), 'tilt_max': max(tilt_start, tilt_end)})
                self.scan_params.update({'step_deg': float(self.scan_step_input.text()), 'wait_sec': int(self.scan_wait_input.text()), 'pan_direction': 1, 'tilt_direction': 1})
                self.scan_params.setdefault('current_pan', self.scan_params.get('pan_min')); self.scan_params.setdefault('current_tilt', self.scan_params.get('tilt_min'))
                self.scan_phase = "moving"; self.interval_scan_button.setText("Taramayı Durdur"); self.execute_interval_scan_step()
            except (ValueError, KeyError) as e:
                QMessageBox.warning(self, "Giriş Hatası", f"Lütfen geçerli sayısal değerler girin.\n{e}")
                self.interval_scan_button.setChecked(False); self.is_interval_scanning = False; self.scan_360_button.setEnabled(True); self.btn_go_to_target.setEnabled(True)
        else:
            self.interval_scan_timer.stop(); self.ptz.Stop({'ProfileToken': self.token})
            self.scan_phase = "stopped"; self.interval_scan_button.setText("Sıcaklık Taramasını Başlat"); self.set_current_speed(0.0)

    def execute_interval_scan_step(self):
        if not self.is_interval_scanning: self.interval_scan_timer.stop(); return
        mode = self.scan_params.get('mode')
        if self.scan_phase == "moving":
            self.go_to_degree(self.scan_params['current_pan'], self.scan_params['current_tilt'])
            self.scan_phase = "waiting"; self.interval_scan_timer.start(self.scan_params['wait_sec'] * 1000)
        elif self.scan_phase == "waiting":
            step = self.scan_params['step_deg']
            if mode == 0:
                self.scan_params['current_pan'] += step * self.scan_params['pan_direction']
                if self.scan_params['current_pan'] >= self.scan_params['pan_max'] or self.scan_params['current_pan'] <= self.scan_params['pan_min']: self.scan_params['pan_direction'] *= -1
            elif mode == 1:
                self.scan_params['current_tilt'] += step * self.scan_params['tilt_direction']
                if self.scan_params['current_tilt'] >= self.scan_params['tilt_max'] or self.scan_params['current_tilt'] <= self.scan_params['tilt_min']: self.scan_params['tilt_direction'] *= -1
            elif mode == 2:
                self.scan_params['current_pan'] += step * self.scan_params['pan_direction']
                if (self.scan_params['current_pan'] > self.scan_params['pan_max'] and self.scan_params['pan_direction'] == 1):
                    self.scan_params['current_pan'] = self.scan_params['pan_max']; self.scan_params['pan_direction'] *= -1
                    self.scan_params['current_tilt'] += step
                elif (self.scan_params['current_pan'] < self.scan_params['pan_min'] and self.scan_params['pan_direction'] == -1):
                    self.scan_params['current_pan'] = self.scan_params['pan_min']; self.scan_params['pan_direction'] *= -1
                    self.scan_params['current_tilt'] += step
                if self.scan_params['current_tilt'] > self.scan_params['tilt_max']:
                    self.scan_params['current_tilt'] = self.scan_params['tilt_min']
            self.scan_phase = "moving"; self.interval_scan_timer.start(10)

    def go_to_degree(self, pan_deg, tilt_deg):
        if not self.ptz: return
        speed = self.get_manual_speed(); self.set_current_speed(speed)
        pan_onvif, tilt_onvif = self._convert_degrees_to_ptz(pan_deg, tilt_deg)
        pos_raw = self.ptz.GetStatus({'ProfileToken': self.token})
        zoom_val = pos_raw.Position.Zoom.x if pos_raw and pos_raw.Position else 0
        req = self.ptz.create_type('AbsoluteMove'); req.ProfileToken = self.token
        req.Position = {'PanTilt': {'x': pan_onvif, 'y': tilt_onvif}, 'Zoom': zoom_val}
        req.Speed = {'PanTilt': {'x': speed, 'y': speed}}; self.ptz.AbsoluteMove(req)

    def go_to_target_position(self):
        try:
            scan_mode = self.scan_mode_combo.currentIndex()
            pan_deg = float(self.pan_start_input.text()) if scan_mode in [0, 2] else float(self.fixed_pan_input.text())
            tilt_deg = float(self.tilt_start_input.text()) if scan_mode in [1, 2] else float(self.fixed_tilt_input.text())
            self.go_to_degree(pan_deg, tilt_deg)
        except ValueError as e: QMessageBox.warning(self, "Giriş Hatası", f"Lütfen geçerli sayısal değerler girin.\n{e}")
            
    @pyqtSlot(QImage)
    def update_image1(self, qt_img): self.camera1_label.setPixmap(QPixmap.fromImage(qt_img))
    @pyqtSlot(QImage)
    def update_image2(self, qt_img): self.camera2_label.setPixmap(QPixmap.fromImage(qt_img))
    
    @pyqtSlot(dict)
    def update_thermal_data(self, data):
        try:
            self.last_thermal_data = data; therm_data_list = data.get('ThermometryUploadList', {}).get('ThermometryUpload', [])
            if not therm_data_list: return
            therm_data = therm_data_list[0]
            avg_temp = therm_data.get('LinePolygonThermCfg', {}).get('AverageTemperature'); max_temp = therm_data.get('LinePolygonThermCfg', {}).get('MaxTemperature')
            min_temp = therm_data.get('LinePolygonThermCfg', {}).get('MinTemperature'); self.last_max_temp = max_temp; self.last_min_temp = min_temp
            hotspot_node = therm_data.get('HighestPoint'); coldspot_node = therm_data.get('LowestPoint')
            self.temp_avg_label.setText(f"{avg_temp:.1f} °C" if avg_temp is not None else "-")
            
            if max_temp is not None and hotspot_node:
                self.thermal_hotspot_coords = (hotspot_node.get('positionX', 0), hotspot_node.get('positionY', 0))
                ptz_coords = self.calculate_hotspot_ptz(self.thermal_hotspot_coords)
                self.max_point_temp_label.setText(f"{max_temp:.1f} °C"); self.max_point_pos_label.setText(f"P: {ptz_coords['pan']:.1f}°, T: {ptz_coords['tilt']:.1f}°")
            else: self.thermal_hotspot_coords = None; self.hotspot_on_normal_cam_coords = None; self.max_point_temp_label.setText("-"); self.max_point_pos_label.setText("-")
            
            if min_temp is not None and coldspot_node:
                self.thermal_coldspot_coords = (coldspot_node.get('positionX', 0), coldspot_node.get('positionY', 0))
                ptz_coords = self.calculate_hotspot_ptz(self.thermal_coldspot_coords)
                self.min_point_temp_label.setText(f"{min_temp:.1f} °C"); self.min_point_pos_label.setText(f"P: {ptz_coords['pan']:.1f}°, T: {ptz_coords['tilt']:.1f}°")
            else: self.thermal_coldspot_coords = None; self.min_point_temp_label.setText("-"); self.min_point_pos_label.setText("-")
            
            self.check_for_alarm()
        except Exception as e: print(f"Termal JSON işleme hatası: {e}")

    def calculate_hotspot_ptz(self, hotspot_coords):
        if not self.last_ptz_status: return {'pan': 0, 'tilt': 0}
        current_pos_onvif = {'pan': self.last_ptz_status.PanTilt.x, 'tilt': self.last_ptz_status.PanTilt.y, 'zoom': self.last_ptz_status.Zoom.x}
        current_pos_deg = self._convert_ptz_to_degrees(current_pos_onvif); zoom_level = current_pos_onvif['zoom']
        current_normal_angle = NORMAL_FOV_WIDE_ANGLE + (zoom_level * (NORMAL_FOV_TELE_ANGLE - NORMAL_FOV_WIDE_ANGLE))
        pan_offset_ratio = hotspot_coords[0] - 0.5; tilt_offset_ratio = hotspot_coords[1] - 0.5
        pan_offset_deg = pan_offset_ratio * THERMAL_FOV_ANGLE; tilt_offset_deg = tilt_offset_ratio * THERMAL_FOV_ANGLE
        hotspot_pan_deg = current_pos_deg['pan'] + pan_offset_deg; hotspot_tilt_deg = current_pos_deg['tilt'] - tilt_offset_deg
        scale_ratio = THERMAL_FOV_ANGLE / current_normal_angle
        pixel_x = int(DISPLAY_WIDTH / 2 + (pan_offset_ratio * (DISPLAY_WIDTH * scale_ratio)))
        pixel_y = int(DISPLAY_HEIGHT / 2 + (tilt_offset_ratio * (DISPLAY_HEIGHT * scale_ratio)))
        # YENİ: Normal kamerada çizilecek kare için koordinatları sakla
        self.hotspot_on_normal_cam_coords = (pixel_x, pixel_y) if scale_ratio < 1.1 else None # Eğer termal alan çok genişse kareyi çizme
        return {'pan': hotspot_pan_deg % 360, 'tilt': hotspot_tilt_deg}
            
    def check_for_alarm(self):
        if self.last_max_temp is None: return
        try: alarm_threshold = float(self.alarm_threshold_input.text())
        except ValueError: return
        if time.time() - self.last_alarm_time < self.alarm_cooldown_input.value(): return
        if self.last_max_temp > alarm_threshold:
            print(f"ALARM! Maksimum sıcaklık {self.last_max_temp:.1f}°C, eşik olan {alarm_threshold}°C değerini aştı.")
            # DEĞİŞTİRİLDİ: Alarm durumunda artık giriş kutuları güncellenmiyor
            with self.frame_lock: normal_frame_to_save, thermal_frame_to_save = self.latest_normal_frame, self.latest_thermal_frame
            if normal_frame_to_save is not None and thermal_frame_to_save is not None:
                ts_folder = time.strftime("%Y-%m-%d_%H-%M-%S"); alarm_folder_path = os.path.join(self.alarms_base_dir, ts_folder)
                os.makedirs(alarm_folder_path, exist_ok=True)
                cv2.imwrite(os.path.join(alarm_folder_path, "normal.jpg"), normal_frame_to_save)
                cv2.imwrite(os.path.join(alarm_folder_path, "thermal.jpg"), thermal_frame_to_save)
                if self.last_thermal_data:
                    with open(os.path.join(alarm_folder_path, "alarm_data.json"), 'w', encoding='utf-8') as f: json.dump(self.last_thermal_data, f, ensure_ascii=False, indent=4)
                print(f"Görüntüler ve veri şuraya kaydedildi: {alarm_folder_path}")
                self.last_alarm_label.setText(f"{ts_folder}\nSıcaklık: {self.last_max_temp:.1f}°C"); self.last_alarm_time = time.time()
                alarm_record = {"time": time.strftime("%H:%M:%S"), "camera": "Termal Kamera", "type": "Yüksek Sıcaklık", "value": f"{self.last_max_temp:.1f}°C"}
                self.alarm_history.insert(0, alarm_record)
                if len(self.alarm_history) > MAX_LOG_ENTRIES: self.alarm_history.pop()
                self.update_alarm_log_widget()
    
    def closeEvent(self, event):
        print("Uygulama kapatılıyor..."); self.is_interval_scanning = False; self.is_scanning_360 = False
        self.ptz_status_timer.stop(); self.interval_scan_timer.stop()
        if hasattr(self, 'thread_normal'): self.thread_normal.stop()
        if hasattr(self, 'thread_thermal'): self.thread_thermal.stop()
        if hasattr(self, 'thread_thermal_data'): self.thread_thermal_data.stop()
        if self.ptz:
            try: self.ptz.Stop({'ProfileToken': self.token})
            except ONVIFError: pass
        if hasattr(self, 'thread_normal'): self.thread_normal.wait()
        if hasattr(self, 'thread_thermal'): self.thread_thermal.wait()
        if hasattr(self, 'thread_thermal_data'): self.thread_thermal_data.wait()
        print("Tüm thread'ler durduruldu. Kapatılıyor."); event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = PTZControlApp()
    window.show()
    sys.exit(app.exec_())