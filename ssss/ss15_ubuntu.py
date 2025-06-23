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
    QHBoxLayout, QGridLayout, QGroupBox, QLineEdit, QFormLayout
)
from PyQt5.QtGui import QImage, QPixmap
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

# === API ve RTSP URL'leri ===
RTSP_URL_NORMAL = f'rtsp://{CAMERA_USER}:{CAMERA_PASS}@{CAMERA_IP}:554/Streaming/Channels/101'
RTSP_URL_THERMAL = f'rtsp://{CAMERA_USER}:{CAMERA_PASS}@{CAMERA_IP}:554/Streaming/Channels/201'
REALTIME_THERMOMETRY_URL = f'http://{CAMERA_IP}/ISAPI/Thermal/channels/2/thermometry/realTimethermometry/rules?format=json'
CALIB_POINT_RELATION_URL = f'http://{CAMERA_IP}/ISAPI/System/Video/inputs/channels/2/calibPointRelation'
PTZ_STATUS_URL = f'http://{CAMERA_IP}/ISAPI/PTZCtrl/channels/1/status'
THERMAL_ALARM_RULES_URL = f'http://{CAMERA_IP}/ISAPI/Thermal/channels/2/thermometry/1/alarmRules'

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
                    if not ret:
                        self.connection_status_signal.emit(f"{self.stream_name}: Veri Alınamıyor...")
                        break
                    
                    if frame is None or len(frame.shape) < 3:
                        continue
                    
                    h_frame, w_frame, _ = frame.shape
                    
                    # Görüntü Boyutlarını Kontrol Et
                    if len(frame.shape) == 3:  # Renkli (RGB) bir görüntü
                        h_frame, w_frame, _ = frame.shape
                    elif len(frame.shape) == 2:  # Gri tonlama (2D) bir görüntü
                        h_frame, w_frame = frame.shape
                    else:
                        raise ValueError("Beklenmeyen görüntü boyutu")

                    # Eğer termal görüntü ise, ROI işlemleri eklenebilir veya kaldırılabilir
                    if self.is_thermal:
                        if self.parent_ui.thermal_hotspot_coords:
                            x, y = self.parent_ui.thermal_hotspot_coords
                            px, py = int(x * w_frame), int(y * h_frame)
                            cv2.drawMarker(frame, (px, py), (0, 0, 255), cv2.MARKER_CROSS, 20, 2)
                            temp_text = f"MAKS: {self.parent_ui.last_max_temp:.1f} C"
                            cv2.putText(frame, temp_text, (px + 15, py - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
                        
                        if self.parent_ui.thermal_coldspot_coords:
                            x, y = self.parent_ui.thermal_coldspot_coords
                            px, py = int(x * w_frame), int(y * h_frame)
                            cv2.drawMarker(frame, (px, py), (255, 0, 0), cv2.MARKER_CROSS, 20, 2)
                            temp_text = f"MIN: {self.parent_ui.last_min_temp:.1f} C"
                            cv2.putText(frame, temp_text, (px + 15, py + 15), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 0, 0), 2)
                    else:
                        # Normal video işlemleri
                        pass

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
            except requests.exceptions.RequestException:
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
        self.setGeometry(100, 100, 1400, 750)
        
        self.rotating = False
        self.ptz = None
        self.auth = HTTPDigestAuth(CAMERA_USER, CAMERA_PASS)
        
        self.last_max_temp = 0.0
        self.last_min_temp = 0.0
        self.thermal_hotspot_coords = None
        self.thermal_coldspot_coords = None
        self.calibrated_hotspot_coords = None
        self.calibrated_coldspot_coords = None
        self.thermal_roi_on_visible = []
        self.ptz_limits = {'pan_min': 0, 'pan_max': 360, 'tilt_min': -90, 'tilt_max': 90}
        
        self.init_onvif()
        self.init_ui()

    def showEvent(self, event):
        super().showEvent(event)
        if not hasattr(self, 'threads_started') or not self.threads_started:
            self.init_threads()
            self.threads_started = True
            threading.Timer(1.0, self.load_initial_data).start()

    def init_onvif(self):
        try:
            self.cam = ONVIFCamera(CAMERA_IP, CAMERA_PORT, CAMERA_USER, CAMERA_PASS)
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
        update_coloring_btn = QPushButton("Hedef Renklendirmeyi Güncelle")
        update_coloring_btn.clicked.connect(self.update_thermal_coloring_rules)
        coloring_layout.addRow("Kırmızı Alarm (> Eşik °C):", self.above_thresh_input)
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
        self.load_initial_thermal_rules()
        self.ptz_status_thread_active = True
        threading.Thread(target=self.update_ptz_status_loop, daemon=True).start()
        self.roi_calib_thread_active = True
        threading.Thread(target=self.calibrate_roi_loop, daemon=True).start()

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
            
            if response_put.status_code == 200: print("Hedef renklendirme başarıyla güncellendi.")
            else: print(f"Güncelleme Hatası: {response_put.status_code} - {response_put.text}")
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
            except: pass
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
        req = self.ptz.create_type('ContinuousMove'); req.ProfileToken = self.token
        req.Velocity = {'PanTilt': {'x': pan, 'y': tilt}}; self.ptz.ContinuousMove(req)
        threading.Timer(0.5, lambda: self.ptz.Stop({'ProfileToken': self.token})).start()
    
    def toggle_rotate(self, checked):
        if not self.ptz: return
        self.rotating = checked
        if self.rotating: threading.Thread(target=self.rotate_loop, daemon=True).start()
        else: self.ptz.Stop({'ProfileToken': self.token})

    def rotate_loop(self):
        req = self.ptz.create_type('ContinuousMove'); req.ProfileToken = self.token
        req.Velocity = {'PanTilt': {'x': 0.2, 'y': 0}}
        while self.rotating: self.ptz.ContinuousMove(req); time.sleep(0.2)
            
    def go_to_absolute_position(self):
        if not self.ptz: return
        try:
            pan_deg, tilt_deg = float(self.pan_input.text()), float(self.tilt_input.text())
            onvif_pan, onvif_tilt = self.degree_to_onvif_accurate(pan_deg, tilt_deg)
            req = self.ptz.create_type('AbsoluteMove'); req.ProfileToken = self.token
            req.Position = {'PanTilt': {'x': onvif_pan, 'y': onvif_tilt}}
            self.ptz.AbsoluteMove(req)
        except Exception as e: print(f"Pozisyonlama hatası: {e}")
            
    @pyqtSlot(QImage)
    def update_image1(self, qt_img): self.camera1_label.setPixmap(QPixmap.fromImage(qt_img))
    
    @pyqtSlot(QImage)
    def update_image2(self, qt_img): self.camera2_label.setPixmap(QPixmap.fromImage(qt_img))

    @pyqtSlot(dict)
    def update_thermal_data(self, data):
        try:
            upload_list = data.get('ThermometryUploadList', {}).get('ThermometryUpload', [])
            if not upload_list: return
            
            therm_data = upload_list[0]
            cfg_data = therm_data.get('LinePolygonThermCfg', {})
            max_temp, min_temp, avg_temp = cfg_data.get('MaxTemperature'), cfg_data.get('MinTemperature'), cfg_data.get('AverageTemperature')
            
            if max_temp is not None: self.last_max_temp = max_temp
            if min_temp is not None: self.last_min_temp = min_temp

            self.temp_max_label.setText(f"{max_temp:.1f} °C" if max_temp is not None else "-")
            self.temp_min_label.setText(f"{min_temp:.1f} °C" if min_temp is not None else "-")
            self.temp_avg_label.setText(f"{avg_temp:.1f} °C" if avg_temp is not None else "-")
            
            hotspot_node, coldspot_node = therm_data.get('HighestPoint'), therm_data.get('LowestPoint')
            
            if hotspot_node: self.thermal_hotspot_coords = (hotspot_node.get('positionX', 0), hotspot_node.get('positionY', 0))
            else: self.thermal_hotspot_coords = None

            if coldspot_node: self.thermal_coldspot_coords = (coldspot_node.get('positionX', 0), coldspot_node.get('positionY', 0))
            else: self.thermal_coldspot_coords = None
        except Exception as e: print(f"Termal JSON işleme hatası: {e}")
            
    def calibrate_roi_loop(self):
        thermal_corners = [(0, 0), (1, 0), (1, 1), (0, 1)]
        while getattr(self, 'roi_calib_thread_active', False):
            visible_corners = [self.calibrate_point(tx, ty) for tx, ty in thermal_corners]
            self.thermal_roi_on_visible = visible_corners
            time.sleep(1)

    def calibrate_point(self, thermal_x, thermal_y):
        xml_request = f"<PointRelation><srcPoint><positionX>{int(thermal_x*1000)}</positionX><positionY>{int(thermal_y*1000)}</positionY></srcPoint></PointRelation>"
        headers = {'Content-Type': 'application/xml'}
        try:
            response = requests.post(CALIB_POINT_RELATION_URL, auth=self.auth, data=xml_request, headers=headers, timeout=0.5)
            if response.status_code == 200:
                root = ET.fromstring(response.content)
                ns = {'isapi': 'http://www.isapi.org/ver20/XMLSchema'}
                dest_point = root.find('isapi:destPoint', ns)
                if dest_point: return (int(dest_point.find('isapi:positionX', ns).text), int(dest_point.find('isapi:positionY', ns).text))
        except: return (int(thermal_x*1000), int(thermal_y*1000))
        return (int(thermal_x*1000), int(thermal_y*1000))

    def closeEvent(self, event):
        print("Uygulama kapatılıyor...")
        self.ptz_status_thread_active = False
        self.roi_calib_thread_active = False
        self.rotating = False
        if self.ptz: self.ptz.Stop({'ProfileToken': self.token})
        
        self.thread_normal.stop()
        self.thread_thermal.stop()
        self.thread_thermal_data.stop()
        
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = PTZControlApp()
    window.show()
    sys.exit(app.exec_())
