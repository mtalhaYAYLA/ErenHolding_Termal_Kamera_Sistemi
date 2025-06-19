# main.py

import sys
from PyQt5.QtWidgets import QApplication
from ui_app import PTZControlApp # Diğer dosyadan ana uygulama sınıfını import et

if __name__ == '__main__':
    app = QApplication(sys.argv)
    main_window = PTZControlApp()
    main_window.show()
    sys.exit(app.exec_())