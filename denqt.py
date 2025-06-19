import sys
import cv2
import threading
import numpy as np
from PyQt5.QtWidgets import QApplication, QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtCore import Qt, QTimer
from onvif import ONVIFCamera
import time

class PTZControl(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Hikvision PTZ Kontrol")

        # Kamera bağlantısı (RTSP canlı görüntü + ONVIF PTZ)
        self.rtsp_url = "rtsp://admin:ErenEnerji@192.168.1.64:554/Streaming/Channels/101"
        self.cap = cv2.VideoCapture(self.rtsp_url)

        self.cam = ONVIFCamera('192.168.1.64', 80, 'admin', 'ErenEnerji')
        self.ptz = self.cam.create_ptz_service()
        self.media = self.cam.create_media_service()
        self.profile = self.media.GetProfiles()[0]
        self.token = self.profile.token

        # Arayüz
        self.image_label = QLabel()
        self.image_label.setFixedSize(640, 480)

        self.up_button = QPushButton("↑")
        self.down_button = QPushButton("↓")
        self.left_button = QPushButton("←")
        self.right_button = QPushButton("→")

        self.up_button.clicked.connect(lambda: self.move_camera(0.0, 0.3))
        self.down_button.clicked.connect(lambda: self.move_camera(0.0, -0.3))
        self.left_button.clicked.connect(lambda: self.move_camera(-0.3, 0.0))
        self.right_button.clicked.connect(lambda: self.move_camera(0.3, 0.0))

        # Layout
        btn_layout = QHBoxLayout()
        btn_layout.addWidget(self.left_button)
        btn_layout.addWidget(self.up_button)
        btn_layout.addWidget(self.down_button)
        btn_layout.addWidget(self.right_button)

        main_layout = QVBoxLayout()
        main_layout.addWidget(self.image_label)
        main_layout.addLayout(btn_layout)
        self.setLayout(main_layout)

        # Görüntü zamanlayıcı
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_frame)
        self.timer.start(30)

    def update_frame(self):
        ret, frame = self.cap.read()
        if ret:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb.shape
            bytes_per_line = ch * w
            qt_image = QImage(rgb.data, w, h, bytes_per_line, QImage.Format_RGB888)
            self.image_label.setPixmap(QPixmap.fromImage(qt_image))

    def move_camera(self, pan_speed, tilt_speed):
        request = self.ptz.create_type('ContinuousMove')
        request.ProfileToken = self.token
        request.Velocity = {'PanTilt': {'x': pan_speed, 'y': tilt_speed}}
        self.ptz.ContinuousMove(request)
        time.sleep(0.5)
        self.ptz.Stop({'ProfileToken': self.token})


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = PTZControl()
    window.show()
    sys.exit(app.exec_())
