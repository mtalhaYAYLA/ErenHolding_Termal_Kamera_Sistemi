
import sys
import cv2
import time
import threading
from PyQt5.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton, QVBoxLayout,
    QHBoxLayout, QGridLayout, QGroupBox
)
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from onvif import ONVIFCamera


# === Canlı görüntü akışı için ayrı bir thread sınıfı ===
class VideoThread(QThread):
    change_pixmap_signal = pyqtSignal(QImage)

    def __init__(self, rtsp_url):
        super().__init__()
        self._run_flag = True
        self.rtsp_url = rtsp_url

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
        cap.release()

    def stop(self):
        self._run_flag = False
        self.wait()


# === Ana GUI uygulama sınıfı ===
class PTZControlApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PTZ Kontrol Paneli")
        self.setGeometry(100, 100, 1400, 800)

        # ONVIF üzerinden PTZ servisine bağlan
        self.cam = ONVIFCamera('192.168.1.64', 80, 'admin', 'ErenEnerji')
        self.media = self.cam.create_media_service()
        self.ptz = self.cam.create_ptz_service()
        self.profile = self.media.GetProfiles()[0]
        self.token = self.profile.token

        # Görüntü alanları (kamera ekranları)
        self.camera1_label = QLabel("Kamera 1 (Normal)")
        self.camera2_label = QLabel("Kamera 2 (Termal)")
        self.camera1_label.setFixedSize(640, 360)
        self.camera2_label.setFixedSize(640, 360)

        # Canlı akışları başlatan thread'ler
        self.thread1 = VideoThread("rtsp://admin:ErenEnerji@192.168.1.64:554/Streaming/Channels/101")
        self.thread2 = VideoThread("rtsp://admin:ErenEnerji@192.168.1.64:554/Streaming/Channels/201")
        self.thread1.change_pixmap_signal.connect(self.update_image1)
        self.thread2.change_pixmap_signal.connect(self.update_image2)
        self.thread1.start()
        self.thread2.start()

        # PTZ butonlarını düzenle (yön ve durdurma)
        ptz_layout = QGridLayout()
        ptz_layout.addWidget(self.ptz_btn("↑", 0, 0.3), 0, 1)
        ptz_layout.addWidget(self.ptz_btn("←", -0.3, 0), 1, 0)
        ptz_layout.addWidget(self.ptz_btn("⏹", 0, 0, stop=True), 1, 1)
        ptz_layout.addWidget(self.ptz_btn("→", 0.3, 0), 1, 2)
        ptz_layout.addWidget(self.ptz_btn("↓", 0, -0.3), 2, 1)

        ptz_box = QGroupBox("PTZ Kontrol")
        ptz_box.setLayout(ptz_layout)

        # Ana pencere düzeni
        main_layout = QHBoxLayout()
        left_layout = QVBoxLayout()
        right_layout = QVBoxLayout()

        # Sol: görüntüler, Sağ: kontrol paneli
        left_layout.addWidget(self.camera1_label)
        left_layout.addWidget(self.camera2_label)
        right_layout.addWidget(ptz_box)

        main_layout.addLayout(left_layout)
        main_layout.addLayout(right_layout)
        self.setLayout(main_layout)

    # PTZ hareket butonlarını oluşturur
    def ptz_btn(self, label, pan, tilt, stop=False):
        btn = QPushButton(label)
        btn.setFixedSize(60, 60)
        if stop:
            btn.clicked.connect(self.stop_move)
        else:
            btn.clicked.connect(lambda: self.move_camera(pan, tilt))
        return btn

    # Kamera hareket ettir
    def move_camera(self, pan, tilt):
        req = self.ptz.create_type('ContinuousMove')
        req.ProfileToken = self.token
        req.Velocity = {'PanTilt': {'x': pan, 'y': tilt}}
        self.ptz.ContinuousMove(req)
        threading.Timer(0.5, lambda: self.ptz.Stop({'ProfileToken': self.token})).start()

    # Durdurma fonksiyonu
    def stop_move(self):
        self.ptz.Stop({'ProfileToken': self.token})

    # Kamera görüntülerini güncelle (kamera 1)
    def update_image1(self, qt_img):
        self.camera1_label.setPixmap(QPixmap.fromImage(qt_img))

    # Kamera görüntülerini güncelle (kamera 2 - termal)
    def update_image2(self, qt_img):
        self.camera2_label.setPixmap(QPixmap.fromImage(qt_img))

    # Pencere kapatılırken thread'leri durdur
    def closeEvent(self, event):
        self.thread1.stop()
        self.thread2.stop()
        event.accept()


# === Uygulama başlat ===
if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = PTZControlApp()
    window.show()
    sys.exit(app.exec_())
