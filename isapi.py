import sys
import cv2
import time
import threading
import numpy as np
import requests
from requests.auth import HTTPDigestAuth
import xml.etree.ElementTree as ET
from PyQt5.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton, QVBoxLayout,
    QHBoxLayout, QGridLayout, QGroupBox, QLineEdit, QFormLayout
)
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from onvif import ONVIFCamera

# === ISAPI Termal Sensör Sınıfı ===
class ThermalSensor:
    def __init__(self, ip, username, password):
        self.url = f"http://{ip}/ISAPI/Thermometry/rule/1"
        self.auth = HTTPDigestAuth(username, password)

    def get_temperature(self):
        try:
            response = requests.get(self.url, auth=self.auth, timeout=5)
            if response.status_code == 200:
                root = ET.fromstring(response.content)
                temp = root.find("ruleTemperature")
                return float(temp.text)
            else:
                print(f"ISAPI Error {response.status_code}")
                return None
        except Exception as e:
            print(f"ISAPI Error: {e}")
            return None


# === Canlı video thread'i ===
class VideoThread(QThread):
    change_pixmap_signal = pyqtSignal(QImage)
    temp_data_signal = pyqtSignal(float, float, float)  # Ortalama, min, max sıcaklık

    def __init__(self, rtsp_url, thermal_sensor=None, is_thermal=False, parent=None):
        super().__init__()
        self._run_flag = True
        self.rtsp_url = rtsp_url
        self.is_thermal = is_thermal
        self.parent_ui = parent
        self.thermal_sensor = thermal_sensor

    def run(self):
        cap = cv2.VideoCapture(self.rtsp_url)
        while self._run_flag:
            ret, frame = cap.read()
            if ret:
                if self.is_thermal:
                    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
                    mask = cv2.inRange(hsv, (0, 100, 200), (10, 255, 255))
                    red_zone = cv2.bitwise_and(frame, frame, mask=mask)
                    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

                    max_hot = 0
                    for cnt in contours:
                        area = cv2.contourArea(cnt)
                        if area > 50:
                            x, y, w, h = cv2.boundingRect(cnt)
                            roi = frame[y:y+h, x:x+w]
                            hot_level = int((np.mean(roi[:, :, 2]) / 255) * 100)
                            max_hot = max(max_hot, hot_level)
                            cv2.putText(frame, f"{hot_level:.1f} C", (x, y - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
                            cv2.rectangle(frame, (x, y), (x+w, y+h), (0, 0, 255), 2)

                    overlay = cv2.addWeighted(frame, 0.7, red_zone, 0.3, 0)
                    rgb = cv2.cvtColor(overlay, cv2.COLOR_BGR2RGB)
                    qt_img = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.shape[1]*3, QImage.Format_RGB888)
                    scaled_img = qt_img.scaled(640, 360, Qt.KeepAspectRatio)
                    self.change_pixmap_signal.emit(scaled_img)

                    # Gerçek sıcaklık verisini ISAPI'den alıyoruz
                    if self.thermal_sensor:
                        real_temp = self.thermal_sensor.get_temperature()
                        if real_temp is not None:
                            temp_pixels = cv2.countNonZero(mask)
                            max_temp = self.parent_ui.max_thresh if temp_pixels > self.parent_ui.mask_threshold else 45
                            avg_temp = self.parent_ui.avg_thresh if temp_pixels > self.parent_ui.mask_threshold / 2 else 30
                            min_temp = 25
                            self.temp_data_signal.emit(avg_temp, min_temp, max_temp)
                            self.parent_ui.update_real_temperature(real_temp)  # Gerçek sıcaklık verisini GUI'ye gönderiyoruz

                else:
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    h, w, ch = rgb.shape
                    qt_img = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888)
                    scaled_img = qt_img.scaled(640, 360, Qt.KeepAspectRatio)
                    self.change_pixmap_signal.emit(scaled_img)
        cap.release()

    def stop(self):
        self._run_flag = False
        self.wait()


# === PTZControlApp Sınıfı ===
class PTZControlApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PTZ Termal Kontrol Paneli")
        self.setGeometry(100, 100, 1600, 900)

        # Eşik ayarları
        self.avg_thresh = 50.0
        self.max_thresh = 80.0
        self.mask_threshold = 250

        # ISAPI Termal Sensör
        self.thermal_sensor = ThermalSensor('192.168.1.64', 'admin', 'ErenEnerji')

        # ONVIF PTZ kontrolü
        self.cam = ONVIFCamera('192.168.1.64', 80, 'admin', 'ErenEnerji')
        self.media = self.cam.create_media_service()
        self.ptz = self.cam.create_ptz_service()
        self.profile = self.media.GetProfiles()[0]
        self.token = self.profile.token

        # Kamera görüntü alanları
        self.camera1_label = QLabel("Kamera 1 (Normal)")
        self.camera2_label = QLabel("Kamera 2 (Termal)")
        self.camera1_label.setFixedSize(640, 360)
        self.camera2_label.setFixedSize(640, 360)

        # Sıcaklık etiketleri
        self.temp_avg = QLabel("Ortalama: -")
        self.temp_min = QLabel("Min: -")
        self.temp_max = QLabel("Max: -")
        self.real_temp = QLabel("Gerçek Sıcaklık: -")

        # Video thread'leri başlat
        self.thread1 = VideoThread("rtsp://admin:ErenEnerji@192.168.1.64:554/Streaming/Channels/101", parent=self)
        self.thread2 = VideoThread("rtsp://admin:ErenEnerji@192.168.1.64:554/Streaming/Channels/201", is_thermal=True, parent=self, thermal_sensor=self.thermal_sensor)
        self.thread1.change_pixmap_signal.connect(self.update_image1)
        self.thread2.change_pixmap_signal.connect(self.update_image2)
        self.thread2.temp_data_signal.connect(self.update_temp_info)
        self.thread1.start()
        self.thread2.start()

        # GUI düzeni
        layout = QVBoxLayout()
        layout.addWidget(self.camera1_label)
        layout.addWidget(self.camera2_label)
        layout.addWidget(self.temp_avg)
        layout.addWidget(self.temp_min)
        layout.addWidget(self.temp_max)
        layout.addWidget(self.real_temp)
        self.setLayout(layout)

    def update_image1(self, qt_img):
        self.camera1_label.setPixmap(QPixmap.fromImage(qt_img))

    def update_image2(self, qt_img):
        self.camera2_label.setPixmap(QPixmap.fromImage(qt_img))

    def update_temp_info(self, avg, minv, maxv):
        self.temp_avg.setText(f"Ortalama: {avg:.1f} °C")
        self.temp_min.setText(f"Min: {minv:.1f} °C")
        self.temp_max.setText(f"Max: {maxv:.1f} °C")

    def update_real_temperature(self, real_temp):
        self.real_temp.setText(f"Gerçek Sıcaklık: {real_temp:.1f} °C")

    def update_thresholds(self):
        try:
            self.avg_thresh = float(self.avg_input.text())
            self.max_thresh = float(self.max_input.text())
            self.mask_threshold = int(self.mask_input.text())
        except ValueError:
            print("Hatalı giriş!")

    def closeEvent(self, event):
        self.thread1.stop()
        self.thread2.stop()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = PTZControlApp()
    window.show()
    sys.exit(app.exec_())
