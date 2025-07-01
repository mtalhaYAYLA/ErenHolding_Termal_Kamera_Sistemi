import cv2
import numpy as np
import time
from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtGui import QImage

class VideoThread(QThread):
    change_pixmap_signal = pyqtSignal(QImage)
    temp_data_signal = pyqtSignal(float, float, float)

    def __init__(self, rtsp_url, is_thermal=False, parent=None):
        super().__init__()
        self.rtsp_url = rtsp_url
        self.is_thermal = is_thermal
        self._run_flag = True
        self.parent_ui = parent

    def run(self):
        cap = cv2.VideoCapture(self.rtsp_url)
        while self._run_flag:
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.2)
                continue

            if self.is_thermal:
                hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
                mask = cv2.inRange(hsv, (0, 100, 200), (10, 255, 255))
                red_zone = cv2.bitwise_and(frame, frame, mask=mask)
                contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

                max_hot = 0
                for cnt in contours:
                    if cv2.contourArea(cnt) > 50:
                        x, y, w, h = cv2.boundingRect(cnt)
                        roi = frame[y:y+h, x:x+w]
                        hot_level = int((np.mean(roi[:, :, 2]) / 255) * 100)
                        max_hot = max(max_hot, hot_level)
                        cv2.putText(frame, f"{hot_level:.1f} C", (x, y - 10),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
                        cv2.rectangle(frame, (x, y), (x+w, y+h), (0, 0, 255), 2)

                overlay = cv2.addWeighted(frame, 0.7, red_zone, 0.3, 0)
                rgb = cv2.cvtColor(overlay, cv2.COLOR_BGR2RGB)
            else:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

            h, w, ch = rgb.shape
            qt_img = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888)
            self.change_pixmap_signal.emit(qt_img)

            if self.is_thermal:
                temp_pixels = cv2.countNonZero(mask)
                max_temp = self.parent_ui.max_thresh if temp_pixels > self.parent_ui.mask_threshold else 45
                avg_temp = self.parent_ui.avg_thresh if temp_pixels > self.parent_ui.mask_threshold / 2 else 30
                min_temp = 25
                self.temp_data_signal.emit(avg_temp, min_temp, max_temp)
                self.parent_ui.update_hot_region(max_hot)

        cap.release()

    def stop(self):
        self._run_flag = False
        self.wait()
