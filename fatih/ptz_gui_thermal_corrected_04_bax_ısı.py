import sys
import cv2
import time
import threading
import numpy as np
from PyQt5.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton, QVBoxLayout,
    QHBoxLayout, QGridLayout, QGroupBox, QLineEdit, QFormLayout
)
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from onvif import ONVIFCamera


# === Canli video thread'i ===
class VideoThread(QThread):
    change_pixmap_signal = pyqtSignal(QImage)
    temp_data_signal = pyqtSignal(float, float, float)  # ort, min, max

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
                if self.is_thermal:
                    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
                    mask = cv2.inRange(hsv, (0, 100, 200), (10, 255, 255))
                    red_zone = cv2.bitwise_and(frame, frame, mask=mask)
                    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

                    for cnt in contours:
                        area = cv2.contourArea(cnt)
                        if area > 50:
                            x, y, w, h = cv2.boundingRect(cnt)
                            roi = frame[y:y+h, x:x+w]
                            hot_level = int((np.mean(roi[:, :, 2]) / 255) * 100)
                            cv2.putText(frame, f"{hot_level:.1f} C", (x, y - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
                            cv2.rectangle(frame, (x, y), (x+w, y+h), (0, 0, 255), 2)

                    overlay = cv2.addWeighted(frame, 0.7, red_zone, 0.3, 0)
                    rgb = cv2.cvtColor(overlay, cv2.COLOR_BGR2RGB)
                    qt_img = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.shape[1]*3, QImage.Format_RGB888)
                    scaled_img = qt_img.scaled(640, 360, Qt.KeepAspectRatio)
                    self.change_pixmap_signal.emit(scaled_img)

                    temp_pixels = cv2.countNonZero(mask)
                    max_temp = self.parent_ui.max_thresh if temp_pixels > self.parent_ui.mask_threshold else 45
                    avg_temp = self.parent_ui.avg_thresh if temp_pixels > self.parent_ui.mask_threshold / 2 else 30
                    min_temp = 25
                    self.temp_data_signal.emit(avg_temp, min_temp, max_temp)
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


# === Ana GUI ===
class PTZControlApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PTZ Termal Kontrol Paneli")
        self.setGeometry(100, 100, 1600, 900)
        self.rotating = False

        # Eşik ayarları
        self.avg_thresh = 50.0
        self.max_thresh = 80.0
        self.mask_threshold = 250

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

        # Eşik giriş alanları
        self.avg_input = QLineEdit(str(self.avg_thresh))
        self.max_input = QLineEdit(str(self.max_thresh))
        self.mask_input = QLineEdit(str(self.mask_threshold))
        update_btn = QPushButton("Eşikleri Güncelle")
        update_btn.clicked.connect(self.update_thresholds)

        # Video thread'leri başlat
        self.thread1 = VideoThread("rtsp://admin:ErenEnerji@192.168.1.64:554/Streaming/Channels/101", parent=self)
        self.thread2 = VideoThread("rtsp://admin:ErenEnerji@192.168.1.64:554/Streaming/Channels/201", is_thermal=True, parent=self)
        self.thread1.change_pixmap_signal.connect(self.update_image1)
        self.thread2.change_pixmap_signal.connect(self.update_image2)
        self.thread2.temp_data_signal.connect(self.update_temp_info)
        self.thread1.start()
        self.thread2.start()

        # PTZ Butonları
        ptz_layout = QGridLayout()
        ptz_layout.addWidget(self.ptz_btn("↑", 0, 0.3), 0, 1)
        ptz_layout.addWidget(self.ptz_btn("←", -0.3, 0), 1, 0)
        ptz_layout.addWidget(self.rotate_btn("⟳ 360"), 1, 1)
        ptz_layout.addWidget(self.ptz_btn("→", 0.3, 0), 1, 2)
        ptz_layout.addWidget(self.ptz_btn("↓", 0, -0.3), 2, 1)
        ptz_box = QGroupBox("PTZ Kontrol")
        ptz_box.setLayout(ptz_layout)

        # Sıcaklık paneli
        temp_layout = QFormLayout()
        temp_layout.addRow("Ortalama Sıcaklık:", self.temp_avg)
        temp_layout.addRow("Min Sıcaklık:", self.temp_min)
        temp_layout.addRow("Max Sıcaklık:", self.temp_max)
        temp_layout.addRow("Ortalama Eşik:", self.avg_input)
        temp_layout.addRow("Maks. Eşik:", self.max_input)
        temp_layout.addRow("Maske Piksel Eşiği:", self.mask_input)
        temp_layout.addRow(update_btn)
        temp_box = QGroupBox("Termal Ayarları")
        temp_box.setLayout(temp_layout)

        # Yerleşim
        left_layout = QVBoxLayout()
        left_layout.addWidget(self.camera1_label)
        left_layout.addWidget(self.camera2_label)
        left_layout.addWidget(temp_box)

        right_layout = QVBoxLayout()
        right_layout.addWidget(ptz_box)

        main_layout = QHBoxLayout()
        main_layout.addLayout(left_layout)
        main_layout.addLayout(right_layout)
        self.setLayout(main_layout)

    def ptz_btn(self, label, pan, tilt):
        btn = QPushButton(label)
        btn.setFixedSize(60, 60)
        btn.clicked.connect(lambda: self.move_camera(pan, tilt))
        return btn

    def rotate_btn(self, label):
        btn = QPushButton(label)
        btn.setFixedSize(80, 60)
        btn.setCheckable(True)
        btn.clicked.connect(lambda: self.toggle_rotate(btn))
        return btn

    def move_camera(self, pan, tilt):
        req = self.ptz.create_type('ContinuousMove')
        req.ProfileToken = self.token
        req.Velocity = {'PanTilt': {'x': pan, 'y': tilt}}
        self.ptz.ContinuousMove(req)
        threading.Timer(0.5, lambda: self.ptz.Stop({'ProfileToken': self.token})).start()

    def toggle_rotate(self, btn):
        if btn.isChecked():
            self.rotating = True
            threading.Thread(target=self.rotate_loop, daemon=True).start()
        else:
            self.rotating = False
            self.ptz.Stop({'ProfileToken': self.token})

    def rotate_loop(self):
        req = self.ptz.create_type('ContinuousMove')
        req.ProfileToken = self.token
        req.Velocity = {'PanTilt': {'x': 0.2, 'y': 0.0}}
        while self.rotating:
            self.ptz.ContinuousMove(req)
            time.sleep(0.2)

    def update_image1(self, qt_img):
        self.camera1_label.setPixmap(QPixmap.fromImage(qt_img))

    def update_image2(self, qt_img):
        self.camera2_label.setPixmap(QPixmap.fromImage(qt_img))

    def update_temp_info(self, avg, minv, maxv):
        self.temp_avg.setText(f"{avg:.1f} °C")
        self.temp_min.setText(f"{minv:.1f} °C")
        self.temp_max.setText(f"{maxv:.1f} °C")

    def update_thresholds(self):
        try:
            self.avg_thresh = float(self.avg_input.text())
            self.max_thresh = float(self.max_input.text())
            self.mask_threshold = int(self.mask_input.text())
            print("Yeni eşikler:", self.avg_thresh, self.max_thresh, self.mask_threshold)
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
