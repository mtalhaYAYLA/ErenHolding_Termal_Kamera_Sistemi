import sys
from PyQt5.QtWidgets import QApplication, QWidget

app = QApplication(sys.argv)
window = QWidget()
window.setWindowTitle("Test GUI")
window.setGeometry(100, 100, 400, 300)
window.show()
sys.exit(app.exec_())
