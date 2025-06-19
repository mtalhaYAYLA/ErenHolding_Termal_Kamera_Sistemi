from PyQt5.QtWidgets import QWidget, QLabel, QVBoxLayout, QTimer
from PyQt5.QtGui import QPixmap
from video_thread import VideoThread
from isapi_reader import ThermalSensor

class PTZControlApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Gerçek Sıcaklık Okuma Paneli")
        self.setMinimumSize(1280, 720)

        # Kamera görüntüsü için QLabel
        self.camera_label = QLabel("Kamera")
        self.camera_label.setMinimumSize(640, 360)

        # Sıcaklık etiketi
        self.temp_label = QLabel("Gerçek Sıcaklık: - °C")
        self.temp_label.setStyleSheet("font-size: 24px; color: red;")

        # RTSP video akışı thread'i başlatılıyor
        self.thread = VideoThread("rtsp://admin:ErenEnerji@192.168.1.64:554/Streaming/Channels/201")
        self.thread.change_pixmap_signal.connect(self.update_image)
        self.thread.start()

        # ISAPI üzerinden sıcaklık verisi almak için ThermalSensor sınıfı
        self.thermal = ThermalSensor("192.168.1.64", "admin", "ErenEnerji")
        self.timer = QTimer()
        self.timer.timeout.connect(self.refresh_temperature)
        self.timer.start(2000)  # Her 2 saniyede bir sıcaklık verisini yenileyin

        layout = QVBoxLayout()
        layout.addWidget(self.camera_label)
        layout.addWidget(self.temp_label)
        self.setLayout(layout)

    def update_image(self, img):
        """Yeni video karesi alındığında GUI'yi günceller."""
        self.camera_label.setPixmap(QPixmap.fromImage(img))

    def refresh_temperature(self):
        """ISAPI üzerinden sıcaklık verisini alır ve GUI'yi günceller."""
        temp = self.thermal.get_temperature()
        if temp is not None:
            self.temp_label.setText(f"Gerçek Sıcaklık: {temp:.1f} °C")
        else:
            self.temp_label.setText("Gerçek Sıcaklık Alınamadı")

    def closeEvent(self, event):
        """Uygulama kapandığında video thread'ini durdurur."""
        self.thread.stop()
        event.accept()
