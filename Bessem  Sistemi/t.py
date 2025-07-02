import sys
import cv2
import datetime
import math
from PyQt5.QtWidgets import (QApplication, QWidget, QPushButton, QLabel, QVBoxLayout,
                             QHBoxLayout, QGridLayout, QMessageBox, QFrame, QLineEdit)
from PyQt5.QtCore import QThread, pyqtSignal, Qt, QTimer
from PyQt5.QtGui import QImage, QPixmap, QDoubleValidator
from onvif import ONVIFCamera
from onvif.exceptions import ONVIFError

# ===================================================================
# KAMERA BİLGİLERİNİZİ VE AYARLARI BURAYA GİRİN
# ===================================================================
NORMAL_CAMERA_RTSP = "rtsp://admin:ErenEnerji@192.168.1.64:554/Streaming/Channels/101"
CAMERA_IP = "192.168.1.64"
ONVIF_PORT = 80
CAMERA_USER = "admin"
CAMERA_PASS = "ErenEnerji"
THERMAL_CAMERA_RTSP = "rtsp://admin:ErenEnerji@192.168.1.64:554/Streaming/Channels/201"
DISPLAY_WIDTH = 640
DISPLAY_HEIGHT = 480
BASE_THERMAL_FOV_RECT = (160, 120, 320, 240)
# ===================================================================

class VideoThread(QThread):
    change_pixmap_signal = pyqtSignal(QImage)
    def __init__(self, rtsp_url, draw_rect_coords=None):
        super().__init__()
        self.rtsp_url, self._run_flag, self.draw_rect_coords = rtsp_url, True, draw_rect_coords
    def run(self):
        cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
        if not cap.isOpened(): print(f"Hata: VideoCapture açılamadı - {self.rtsp_url}"); return
        while self._run_flag:
            ret, cv_img = cap.read();
            if ret:
                resized_img = cv2.resize(cv_img, (DISPLAY_WIDTH, DISPLAY_HEIGHT))
                if self.draw_rect_coords:
                    x, y, w, h = map(int, self.draw_rect_coords)
                    cv2.rectangle(resized_img, (x, y), (x + w, y + h), (0, 255, 0), 2)
                    cv2.putText(resized_img, 'Termal Goruntu Alani', (x, y - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
                rgb_image = cv2.cvtColor(resized_img, cv2.COLOR_BGR2RGB)
                h_img, w_img, ch = rgb_image.shape; bytes_per_line = ch * w_img
                qt_image = QImage(rgb_image.data, w_img, h_img, bytes_per_line, QImage.Format_RGB888)
                self.change_pixmap_signal.emit(qt_image)
        cap.release()
    def stop(self): self._run_flag = False; self.wait()
    def update_rect_coords(self, new_coords): self.draw_rect_coords = new_coords

class ONVIFController:
    def __init__(self, ip, port, user, password):
        self.is_connected = False
        try:
            self.mycam = ONVIFCamera(ip, port, user, password)
            self.media_service = self.mycam.create_media_service()
            self.ptz_service = self.mycam.create_ptz_service()
            profiles = self.media_service.GetProfiles()
            if not profiles: raise ONVIFError("Kameradan media profili alınamadı.")
            self.media_profile = profiles[0]
            self.continuous_move_request = self.ptz_service.create_type('ContinuousMove')
            self.continuous_move_request.ProfileToken = self.media_profile.token
            self.absolute_move_request = self.ptz_service.create_type('AbsoluteMove')
            self.absolute_move_request.ProfileToken = self.media_profile.token
            self.ptz_service.Stop({'ProfileToken': self.media_profile.token}); self.is_connected = True
            print("ONVIF bağlantısı başarılı.")
        except Exception as e:
            self.is_connected = False; print(f"ONVIF bağlantı hatası: {e}")
    def get_current_position(self):
        if not self.is_connected: return None
        try:
            status = self.ptz_service.GetStatus({'ProfileToken': self.media_profile.token})
            if status and status.Position:
                return {'pan': status.Position.PanTilt.x, 'tilt': status.Position.PanTilt.y, 'zoom': status.Position.Zoom.x}
        except ONVIFError: pass
        return None
    def move_continuous(self, pan_speed, tilt_speed, zoom_speed=0.0):
        if not self.is_connected: return
        self.continuous_move_request.Velocity = { 'PanTilt': {'x': pan_speed, 'y': tilt_speed}, 'Zoom': {'x': zoom_speed} }
        self.ptz_service.ContinuousMove(self.continuous_move_request)
    def move_absolute_with_speed(self, pan, tilt, zoom, speed_vector):
        if not self.is_connected: return
        try:
            self.absolute_move_request.Position = {'PanTilt': {'x': pan, 'y': tilt}, 'Zoom': {'x': zoom}}
            if speed_vector: self.absolute_move_request.Speed = speed_vector
            else:
                if 'Speed' in self.absolute_move_request: del self.absolute_move_request.Speed
            self.ptz_service.AbsoluteMove(self.absolute_move_request)
        except ONVIFError as e: print(f"Hızlı mutlak hareket hatası: {e}")
    def stop_move(self):
        if not self.is_connected: return
        self.ptz_service.Stop({'ProfileToken': self.media_profile.token})

class App(QWidget):
    PAN_DEGREE_RANGE = (0, 360); TILT_DEGREE_RANGE = (-5, 90)
    ONVIF_RANGE = (-1.0, 1.0); ZOOM_RANGE = (0.0, 1.0); MAX_RECT_SCALE_FACTOR = 4.0

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Gelişmiş Kamera Kontrol Paneli")
        self.normal_thread, self.thermal_thread, self.onvif_controller = None, None, None
        self.is_scanning_360 = False
        self.is_interval_scanning = False
        self.scan_phase = "stopped"  # YENİ: Tarama durumunu tutar: "stopped", "moving_to_start", "scanning"
        self.scan_params = {}
        
        self.ptz_status_timer = QTimer(self); self.ptz_status_timer.timeout.connect(self.update_ptz_status_display)
        self.interval_scan_timer = QTimer(self); self.interval_scan_timer.timeout.connect(self.execute_interval_scan_step)
        self.initUI()
        self.set_current_speed(0.0)

    def initUI(self):
        main_layout = QHBoxLayout()
        video_layout = QVBoxLayout()
        normal_cam_layout = QVBoxLayout()
        self.normal_video_label = QLabel(self); self.normal_video_label.setFixedSize(DISPLAY_WIDTH, DISPLAY_HEIGHT)
        self.normal_video_label.setStyleSheet("background-color: #111; border: 1px solid #555;"); normal_cam_layout.addWidget(QLabel("<b>Normal Kamera</b>")); normal_cam_layout.addWidget(self.normal_video_label)
        thermal_cam_layout = QVBoxLayout()
        self.thermal_video_label = QLabel(self); self.thermal_video_label.setFixedSize(DISPLAY_WIDTH, DISPLAY_HEIGHT)
        self.thermal_video_label.setStyleSheet("background-color: #111; border: 1px solid #555;"); thermal_cam_layout.addWidget(QLabel("<b>Termal Kamera</b>")); thermal_cam_layout.addWidget(self.thermal_video_label)
        video_layout.addLayout(normal_cam_layout); video_layout.addLayout(thermal_cam_layout)
        
        control_panel_layout = QVBoxLayout(); control_panel_layout.setAlignment(Qt.AlignTop)
        self.connect_button = QPushButton("Kameralara Bağlan"); self.connect_button.clicked.connect(self.toggle_connection)
        self.screenshot_button = QPushButton("Fotoğraf Çek (İkisi de)"); self.screenshot_button.clicked.connect(self.take_screenshot)

        status_frame = QFrame(); status_frame.setFrameShape(QFrame.StyledPanel); status_layout = QVBoxLayout(status_frame)
        status_layout.addWidget(QLabel("<b>Anlık Kamera Durumu</b>"))
        info_hbox = QHBoxLayout()
        self.current_pos_label = QLabel("Pan: --°, Tilt: --°, Zoom: --%"); self.current_pos_label.setStyleSheet("font-weight: bold; color: #33aaff;")
        self.current_speed_label = QLabel("Hız: --"); self.current_speed_label.setStyleSheet("font-weight: bold; color: #22ddaa;")
        info_hbox.addWidget(self.current_pos_label); info_hbox.addWidget(self.current_speed_label); status_layout.addLayout(info_hbox)

        ptz_frame = QFrame(); ptz_frame.setFrameShape(QFrame.StyledPanel); ptz_layout_container = QVBoxLayout(ptz_frame)
        ptz_layout_container.addWidget(QLabel("<b>Manuel PTZ Kontrol</b>"))
        manual_speed_layout = QHBoxLayout()
        manual_speed_layout.addWidget(QLabel("Manuel Hız (0-1):"))
        self.manual_speed_input = QLineEdit("0.5"); self.manual_speed_input.setValidator(QDoubleValidator(0.01, 1.0, 2))
        manual_speed_layout.addWidget(self.manual_speed_input); ptz_layout_container.addLayout(manual_speed_layout)
        ptz_grid = QGridLayout()
        self.btn_up=QPushButton("↑"); self.btn_down=QPushButton("↓"); self.btn_left=QPushButton("←"); self.btn_right=QPushButton("→")
        self.btn_zoom_in = QPushButton("Zoom +"); self.btn_zoom_out = QPushButton("Zoom -")
        ptz_grid.addWidget(self.btn_up,0,1); ptz_grid.addWidget(self.btn_down,2,1); ptz_grid.addWidget(self.btn_left,1,0); ptz_grid.addWidget(self.btn_right,1,2)
        ptz_grid.addWidget(self.btn_zoom_in, 0, 3); ptz_grid.addWidget(self.btn_zoom_out, 2, 3)
        ptz_layout_container.addLayout(ptz_grid)
        self.btn_up.pressed.connect(lambda: self.handle_manual_move(0, 1, 0)); self.btn_down.pressed.connect(lambda: self.handle_manual_move(0, -1, 0))
        self.btn_left.pressed.connect(lambda: self.handle_manual_move(-1, 0, 0)); self.btn_right.pressed.connect(lambda: self.handle_manual_move(1, 0, 0))
        self.btn_zoom_in.pressed.connect(lambda: self.handle_manual_move(0, 0, 1)); self.btn_zoom_out.pressed.connect(lambda: self.handle_manual_move(0, 0, -1))
        ptz_buttons = [self.btn_up, self.btn_down, self.btn_left, self.btn_right, self.btn_zoom_in, self.btn_zoom_out]
        for btn in ptz_buttons: btn.released.connect(self.stop_ptz_move)
        self.scan_360_button = QPushButton("360° Tarama Başlat"); self.scan_360_button.setCheckable(True); self.scan_360_button.clicked.connect(self.toggle_360_scan)
        ptz_layout_container.addWidget(self.scan_360_button)

        interval_scan_frame = QFrame(); interval_scan_frame.setFrameShape(QFrame.StyledPanel); interval_scan_layout = QVBoxLayout(interval_scan_frame)
        interval_scan_layout.addWidget(QLabel("<b>Sınırlı Tarama Ayarları</b>"))
        interval_grid = QGridLayout()
        self.pan_start_input = QLineEdit("90"); interval_grid.addWidget(QLabel("Pan Başlangıç (X1°):"), 0, 0); interval_grid.addWidget(self.pan_start_input, 0, 1)
        self.pan_end_input = QLineEdit("270"); interval_grid.addWidget(QLabel("Pan Bitiş (X2°):"), 1, 0); interval_grid.addWidget(self.pan_end_input, 1, 1)
        self.tilt_fixed_input = QLineEdit("45"); interval_grid.addWidget(QLabel("Tilt Hedef/Sabit (Y°):"), 2, 0); interval_grid.addWidget(self.tilt_fixed_input, 2, 1)
        validator = QDoubleValidator(); self.pan_start_input.setValidator(validator); self.pan_end_input.setValidator(validator); self.tilt_fixed_input.setValidator(validator)
        interval_scan_layout.addLayout(interval_grid)
        button_hbox = QHBoxLayout()
        self.btn_go_to_target = QPushButton("Hedefe Git"); self.btn_go_to_target.clicked.connect(self.go_to_target_position)
        self.interval_scan_button = QPushButton("Sınırlı Taramayı Başlat"); self.interval_scan_button.setCheckable(True); self.interval_scan_button.clicked.connect(self.toggle_interval_scan)
        button_hbox.addWidget(self.btn_go_to_target); button_hbox.addWidget(self.interval_scan_button); interval_scan_layout.addLayout(button_hbox)
        
        control_panel_layout.addWidget(self.connect_button); control_panel_layout.addWidget(self.screenshot_button); control_panel_layout.addWidget(status_frame)
        control_panel_layout.addWidget(ptz_frame); control_panel_layout.addWidget(interval_scan_frame); control_panel_layout.addStretch()

        main_layout.addLayout(video_layout); main_layout.addLayout(control_panel_layout)
        self.setLayout(main_layout); self.set_controls_enabled(False)
    
    def get_manual_speed(self):
        try: return max(0.01, min(1.0, float(self.manual_speed_input.text().replace(',', '.'))))
        except (ValueError, TypeError): return 0.5
        
    def set_current_speed(self, speed): self.current_speed_label.setText(f"Hız: {speed:.2f}")
    def handle_manual_move(self, pan_dir, tilt_dir, zoom_dir):
        speed = self.get_manual_speed(); self.set_current_speed(speed)
        self.onvif_controller.move_continuous(pan_dir * speed, tilt_dir * speed, zoom_dir * speed)

    def set_controls_enabled(self, enabled):
        ptz_widgets = [self.btn_up, self.btn_down, self.btn_left, self.btn_right, self.btn_zoom_in, self.btn_zoom_out, self.scan_360_button,
                       self.interval_scan_button, self.btn_go_to_target, self.pan_start_input, self.pan_end_input, self.tilt_fixed_input, self.manual_speed_input]
        for widget in ptz_widgets: widget.setEnabled(enabled)
        self.screenshot_button.setEnabled(enabled)
        if not enabled: self.set_current_speed(0.0)

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
    def toggle_connection(self):
        if self.normal_thread and self.normal_thread.isRunning():
            self.ptz_status_timer.stop(); self.interval_scan_timer.stop()
            if self.normal_thread: self.normal_thread.stop()
            if self.thermal_thread: self.thermal_thread.stop()
            self.normal_video_label.setPixmap(QPixmap()); self.thermal_video_label.setPixmap(QPixmap())
            self.connect_button.setText("Kameralara Bağlan"); self.set_controls_enabled(False)
        else:
            self.connect_button.setText("Bağlantıyı Kes")
            self.normal_thread = VideoThread(NORMAL_CAMERA_RTSP, draw_rect_coords=BASE_THERMAL_FOV_RECT); self.normal_thread.change_pixmap_signal.connect(self.update_normal_image); self.normal_thread.start()
            self.thermal_thread = VideoThread(THERMAL_CAMERA_RTSP); self.thermal_thread.change_pixmap_signal.connect(self.update_thermal_image); self.thermal_thread.start()
            self.onvif_controller = ONVIFController(CAMERA_IP, ONVIF_PORT, CAMERA_USER, CAMERA_PASS)
            if self.onvif_controller.is_connected: self.set_controls_enabled(True); self.ptz_status_timer.start(250)
            else: QMessageBox.warning(self, "ONVIF Hatası", "Kamera hareket kontrolü (ONVIF) başlatılamadı."); self.set_controls_enabled(False); self.screenshot_button.setEnabled(True)
    def update_thermal_rect_from_zoom(self, zoom_level):
        if not self.normal_thread: return
        scale_factor = 1.0 + (zoom_level * (self.MAX_RECT_SCALE_FACTOR - 1.0)); base_x, base_y, base_w, base_h = BASE_THERMAL_FOV_RECT
        new_w, new_h = base_w * scale_factor, base_h * scale_factor; new_x, new_y = (DISPLAY_WIDTH - new_w) / 2, (DISPLAY_HEIGHT - new_h) / 2
        self.normal_thread.update_rect_coords((new_x, new_y, new_w, new_h))
        
    def update_ptz_status_display(self):
        if self.onvif_controller and self.onvif_controller.is_connected:
            pos_raw = self.onvif_controller.get_current_position()
            if pos_raw:
                pos_deg = self._convert_ptz_to_degrees(pos_raw)
                self.current_pos_label.setText(f"Pan: {pos_deg['pan']:.1f}°, Tilt: {pos_deg['tilt']:.1f}°, Zoom: {pos_deg['zoom']:.0f}%")
                self.update_thermal_rect_from_zoom(pos_raw['zoom'])

    def stop_ptz_move(self):
        if self.onvif_controller and not self.is_scanning_360 and self.scan_phase == "stopped":
            self.onvif_controller.stop_move(); self.set_current_speed(0.0)
            
    def update_normal_image(self, qt_image): self.normal_pixmap = QPixmap.fromImage(qt_image); self.normal_video_label.setPixmap(self.normal_pixmap)
    def update_thermal_image(self, qt_image): self.thermal_pixmap = QPixmap.fromImage(qt_image); self.thermal_video_label.setPixmap(self.thermal_pixmap)

    def toggle_360_scan(self, checked):
        if not self.onvif_controller: return
        self.is_scanning_360 = checked
        self.interval_scan_button.setEnabled(not checked); self.btn_go_to_target.setEnabled(not checked)
        if checked:
            speed = self.get_manual_speed(); self.set_current_speed(speed)
            self.scan_360_button.setText("Taramayı Durdur"); self.onvif_controller.move_continuous(speed, 0, 0)
        else:
            self.scan_360_button.setText("360° Tarama Başlat"); self.onvif_controller.stop_move(); self.set_current_speed(0.0)

    # =====================================================================================
    #  SINIRLI TARAMA BAŞLATMA FONKSİYONU (YENİ MANTIK)
    # =====================================================================================
    def toggle_interval_scan(self, checked):
        if not self.onvif_controller: return
        self.is_interval_scanning = checked
        self.scan_360_button.setEnabled(not checked); self.btn_go_to_target.setEnabled(not checked)
        
        if checked:
            try:
                pan_start_deg = float(self.pan_start_input.text().replace(',', '.'))
                pan_end_deg = float(self.pan_end_input.text().replace(',', '.'))
                tilt_fixed_deg = float(self.tilt_fixed_input.text().replace(',', '.'))
                speed = self.get_manual_speed()

                current_pos = self.onvif_controller.get_current_position()
                if not current_pos:
                    QMessageBox.warning(self, "Hata", "Kameranın mevcut pozisyonu alınamadı.")
                    self.interval_scan_button.setChecked(False); self.is_interval_scanning = False
                    return

                # Adım 1: Gerekli parametreleri hesapla ve sakla
                p1_onvif, tilt_onvif = self._convert_degrees_to_ptz(pan_start_deg, tilt_fixed_deg)
                p2_onvif, _ = self._convert_degrees_to_ptz(pan_end_deg, tilt_fixed_deg)
                
                dist_p1 = abs(current_pos['pan'] - p1_onvif)
                dist_p2 = abs(current_pos['pan'] - p2_onvif)

                # En yakın başlangıç noktasını ve yönü belirle
                initial_target = p1_onvif if dist_p1 <= dist_p2 else p2_onvif
                direction = 1 if initial_target == p1_onvif else -1
                
                self.scan_params = {
                    'min_pan': min(p1_onvif, p2_onvif),
                    'max_pan': max(p1_onvif, p2_onvif),
                    'tilt': tilt_onvif,
                    'initial_target': initial_target,
                    'direction': direction
                }

                # Adım 2: "Başlangıç noktasına git" fazını başlat
                self.scan_phase = "moving_to_start"
                self.interval_scan_button.setText("Sınırlı Taramayı Durdur")
                self.set_current_speed(speed)
                self.onvif_controller.move_absolute_with_speed(initial_target, tilt_onvif, current_pos['zoom'], {'PanTilt': {'x': speed, 'y': speed}})
                self.interval_scan_timer.start(250)
            
            except ValueError as e:
                QMessageBox.warning(self, "Giriş Hatası", f"Lütfen geçerli sayısal değerler girin.\n{e}")
                self.interval_scan_button.setChecked(False); self.is_interval_scanning = False
                self.scan_360_button.setEnabled(True); self.btn_go_to_target.setEnabled(True)
        else:
            # Tarama durdurma
            self.interval_scan_timer.stop()
            self.onvif_controller.stop_move()
            self.scan_phase = "stopped"
            self.interval_scan_button.setText("Sınırlı Taramayı Başlat")
            self.set_current_speed(0.0)

    # =====================================================================================
    #  TARAMA DÖNGÜSÜ FONKSİYONU (YENİ İKİ AŞAMALI MANTIK)
    # =====================================================================================
    def execute_interval_scan_step(self):
        if self.scan_phase == "stopped":
            self.interval_scan_timer.stop(); return
        
        pos = self.onvif_controller.get_current_position()
        if not pos: return

        speed = self.get_manual_speed()
        self.set_current_speed(speed)
        current_pan = pos['pan']

        # FAZ 1: Başlangıç noktasına varışı kontrol et
        if self.scan_phase == "moving_to_start":
            if abs(current_pan - self.scan_params['initial_target']) < 0.02:
                # Başlangıç noktasına varıldı, şimdi sürekli taramaya geç
                self.scan_phase = "scanning"
                # Yönü, başlangıç noktasına göre ayarla. p1'e geldiysek p2'ye (sağa), p2'ye geldiysek p1'e (sola) git.
                direction = 1 if self.scan_params['initial_target'] == self.scan_params['min_pan'] else -1
                self.scan_params['direction'] = direction
                self.onvif_controller.move_continuous(speed * direction, 0, 0)
        
        # FAZ 2: Sürekli tarama ve sınır kontrolü
        elif self.scan_phase == "scanning":
            direction = self.scan_params['direction']
            # Üst sınıra ulaşıldı mı ve sağa mı gidiyor?
            if current_pan >= self.scan_params['max_pan'] and direction == 1:
                self.scan_params['direction'] = -1 # Yönü ters çevir
                self.onvif_controller.move_continuous(speed * -1, 0, 0)
            # Alt sınıra ulaşıldı mı ve sola mı gidiyor?
            elif current_pan <= self.scan_params['min_pan'] and direction == -1:
                self.scan_params['direction'] = 1 # Yönü ters çevir
                self.onvif_controller.move_continuous(speed * 1, 0, 0)


    def go_to_target_position(self):
        if not self.onvif_controller or not self.onvif_controller.is_connected: return
        try:
            pan_deg = float(self.pan_start_input.text().replace(',', '.'))
            tilt_deg = float(self.tilt_fixed_input.text().replace(',', '.'))
            speed = self.get_manual_speed(); self.set_current_speed(speed)
            pan_onvif, tilt_onvif = self._convert_degrees_to_ptz(pan_deg, tilt_deg)
            current_pos = self.onvif_controller.get_current_position()
            zoom_val = current_pos['zoom'] if current_pos else 0
            speed_vector = {'PanTilt': {'x': speed, 'y': speed}}
            self.onvif_controller.move_absolute_with_speed(pan_onvif, tilt_onvif, zoom_val, speed_vector)
        except ValueError as e:
            QMessageBox.warning(self, "Giriş Hatası", f"Lütfen geçerli sayısal değerler girin.\n{e}")

    def take_screenshot(self):
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        saved_files = []
        if self.normal_pixmap: saved_files.append(f"screenshot_{timestamp}_normal.jpg"); self.normal_pixmap.save(saved_files[-1],"jpg")
        if self.thermal_pixmap: saved_files.append(f"screenshot_{timestamp}_thermal.jpg"); self.thermal_pixmap.save(saved_files[-1],"jpg")
        if saved_files: QMessageBox.information(self, "Başarılı", f"Görüntüler kaydedildi:\n" + "\n".join(saved_files))
        else: QMessageBox.warning(self, "Hata", "Kaydedilecek görüntü yok.")

    def closeEvent(self, event):
        self.ptz_status_timer.stop(); self.interval_scan_timer.stop()
        if self.normal_thread: self.normal_thread.stop()
        if self.thermal_thread: self.thermal_thread.stop()
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    a = App()
    a.show()
    sys.exit(app.exec_())