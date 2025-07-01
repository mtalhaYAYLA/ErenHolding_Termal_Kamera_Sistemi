
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


# === Canlı görüntü işleme thread'i ===
class VideoThread(QThread):
    change_pixmap_signal = pyqtSignal(QImage)
    temp_data_signal = pyqtSignal(float, float, float)  # ort, min, max

    def __init__(self, rtsp_url, is_thermal=False):
        super().__init__()
        self._run_flag = True
        self.rtsp_url = rtsp_url
        self.is_thermal = is_thermal

    def run(self):
        cap = cv2.VideoCapture(self.rtsp_url)
        while self._run_flag:
            ret, frame = cap.read()
            if ret:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                h, w, ch = rgb.shape
                bytes_per_line = ch * w
                qt_img = QImage(rgb.data, w, h, bytes_per_line, QImage.Format_RGB888)
                scaled_img = qt_img.scaled(640, 360, Qt.KeepAspectRatio)
                self.change_pixmap_signal.emit(scaled_img)

                # Sıcaklık tahmini (renk analizine göre)
                if self.is_thermal:
                    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
                    mask = cv2.inRange(hsv, (0, 50, 200), (10, 255, 255))
                    temp_pixels = cv2.countNonZero(mask)
                    max_temp = 80 if temp_pixels > 300 else 45
                    avg_temp = 60 if temp_pixels > 150 else 30
                    min_temp = 25
                    self.temp_data_signal.emit(avg_temp, min_temp, max_temp)
        cap.release()

    def stop(self):
        self._run_flag = False
        self.wait()


# === Ana GUI sınıfı ===
class PTZControlApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PTZ Kontrol Paneli")
        self.setGeometry(100, 100, 1600, 900)
        self.rotating = False  # 360 döngü kontrolü

        # ONVIF bağlantısı
        self.cam = ONVIFCamera('192.168.1.64', 80, 'admin', 'ErenEnerji')
        self.media = self.cam.create_media_service()
        self.ptz = self.cam.create_ptz_service()
        self.profile = self.media.GetProfiles()[0]
        self.token = self.profile.token

        # Görüntü panelleri
        self.camera1_label = QLabel("Kamera 1 (Normal)")
        self.camera2_label = QLabel("Kamera 2 (Termal)")
        self.camera1_label.setFixedSize(640, 360)
        self.camera2_label.setFixedSize(640, 360)

        # Termal sıcaklık bilgileri
        self.temp_avg = QLabel("Ortalama: -")
        self.temp_min = QLabel("Min: -")
        self.temp_max = QLabel("Max: -")

        # Thread'ler
        self.thread1 = VideoThread("rtsp://admin:ErenEnerji@192.168.1.64:554/Streaming/Channels/101")
        self.thread2 = VideoThread("rtsp://admin:ErenEnerji@192.168.1.64:554/Streaming/Channels/201", is_thermal=True)
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

        # Sıcaklık bilgisi alanı
        temp_layout = QFormLayout()
        temp_layout.addRow("Ortalama Sıcaklık:", self.temp_avg)
        temp_layout.addRow("En Düşük Sıcaklık:", self.temp_min)
        temp_layout.addRow("En Yüksek Sıcaklık:", self.temp_max)
        temp_box = QGroupBox("Termal Sıcaklık Bilgisi")
        temp_box.setLayout(temp_layout)

        # Yerleşim
        left_layout = QVBoxLayout()
        right_layout = QVBoxLayout()
        main_layout = QHBoxLayout()

        left_layout.addWidget(self.camera1_label)
        left_layout.addWidget(self.camera2_label)
        left_layout.addWidget(temp_box)

        right_layout.addWidget(ptz_box)

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

    def closeEvent(self, event):
        self.thread1.stop()
        self.thread2.stop()
        event.accept()


# Başlat
if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = PTZControlApp()
    window.show()
    sys.exit(app.exec_())
