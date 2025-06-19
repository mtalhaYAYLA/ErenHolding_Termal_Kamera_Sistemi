from onvif import ONVIFCamera
import threading
import time

class PTZController:
    def __init__(self, ip, port, username, password):
        self.cam = ONVIFCamera(ip, port, username, password)
        self.media = self.cam.create_media_service()
        self.ptz = self.cam.create_ptz_service()
        self.profile = self.media.GetProfiles()[0]
        self.token = self.profile.token
        self.rotating = False

    def move(self, pan, tilt):
        req = self.ptz.create_type('ContinuousMove')
        req.ProfileToken = self.token
        req.Velocity = {'PanTilt': {'x': pan, 'y': tilt}}
        self.ptz.ContinuousMove(req)
        threading.Timer(0.5, lambda: self.ptz.Stop({'ProfileToken': self.token})).start()

    def toggle_rotate(self, btn):
        if btn.isChecked():
            self.rotating = True
            threading.Thread(target=self._rotate_loop, daemon=True).start()
        else:
            self.rotating = False
            self.ptz.Stop({'ProfileToken': self.token})

    def _rotate_loop(self):
        req = self.ptz.create_type('ContinuousMove')
        req.ProfileToken = self.token
        req.Velocity = {'PanTilt': {'x': 0.2, 'y': 0.0}}
        while self.rotating:
            self.ptz.ContinuousMove(req)
            time.sleep(0.2)
