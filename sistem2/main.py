import sys
from PyQt5.QtWidgets import QApplication
from ui_app import PTZControlApp

if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = PTZControlApp()
    win.show()
    sys.exit(app.exec_())
"""sistem/
│
├── main.py               # Uygulamayı başlatır.
├── ui_app.py             # PyQt5 arayüz ve mantık.
├── video_thread.py       # Video akışlarını yöneten thread yapısı.
└── isapi_reader.py       # ISAPI üzerinden sıcaklık verilerini çeker."""