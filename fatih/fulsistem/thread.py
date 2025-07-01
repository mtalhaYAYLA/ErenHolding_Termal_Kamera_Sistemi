# thread.py

import cv2
import time
import requests
import json
from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtGui import QImage
import numpy as np
from requests.auth import HTTPDigestAuth
import xml.etree.ElementTree as ET
import os

os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"

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
                        if self.parent_ui.thermal_roi_on_visible:
                            pts = np.array(self.parent_ui.thermal_roi_on_visible, np.int32)
                            pts[:, :, 0] = (pts[:, :, 0] / 1000.0) * w_frame
                            pts[:, :, 1] = (pts[:, :, 1] / 1000.0) * h_frame
                            pts = pts.reshape((-1, 1, 2))
                            cv2.polylines(frame, [pts.astype(np.int32)], isClosed=True, color=(0, 255, 0), thickness=2)

                    rgb_image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    qt_img = QImage(rgb_image.data, w_frame, h_frame, 3 * w_frame, QImage.Format_RGB888)
                    scaled_img = qt_img.scaled(640, 360, Qt.KeepAspectRatio)
                    self.change_pixmap_signal.emit(scaled_img)
            except Exception as e:
                print(f"HATA ({self.stream_name} Thread): {e}")
                self.connection_status_signal.emit(f"{self.stream_name}: Thread Çöktü")
                time.sleep(5)
            finally:
                if cap: cap.release()
        print(f"{self.stream_name} video thread durdu.")

    def stop(self):
        self._run_flag = False
        self.wait()

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
                                        try: self.thermal_data_updated.emit(json.loads(json_str))
                                        except: pass
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