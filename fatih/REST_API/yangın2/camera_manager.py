# camera_manager.py

import threading
import time
import requests
import json
from onvif import ONVIFCamera
from requests.auth import HTTPDigestAuth

# Bu sınıf, her bir kamera için ayrı bir thread'de çalışarak
# otonom tarama ve veri toplama işlemlerini yapar.
class CameraWorker:
    def __init__(self, config, manager):
        self.config = config
        self.manager = manager # Ana yöneticiye erişim için
        self.id = config['id']
        self.auth = HTTPDigestAuth(config['user'], config['password'])
        self.ptz = None
        self.token = None
        self.ptz_limits = {'pan_min': 0, 'pan_max': 360, 'tilt_min': -90, 'tilt_max': 90}
        
        self.running = True
        self.manual_override = False
        self.last_manual_command_time = 0
        
        self.thermal_data = {}

        self._init_onvif()
        
        # Her kamera için kendi thread'lerini başlat
        self.scan_thread = threading.Thread(target=self.scan_loop, daemon=True)
        self.data_thread = threading.Thread(target=self.data_loop, daemon=True)

    def _init_onvif(self):
        try:
            cam = ONVIFCamera(self.config['ip'], 80, self.config['user'], self.config['password'])
            self.ptz = cam.create_ptz_service()
            profile = cam.create_media_service().GetProfiles()[0]
            self.token = profile.token
            # ... (ONVIF limit alma kodu)
            print(f"Kamera {self.id}: ONVIF bağlantısı başarılı.")
        except Exception as e:
            print(f"Kamera {self.id}: ONVIF bağlantı hatası: {e}")
            self.ptz = None
    
    def start(self):
        self.scan_thread.start()
        self.data_thread.start()
    
    def stop(self):
        self.running = False
        if self.ptz: self.ptz.Stop({'ProfileToken': self.token})
        print(f"Kamera {self.id} worker durduruluyor.")

    def scan_loop(self):
        """Otonom tarama döngüsü."""
        if not self.config['autonomous_scan']['enabled']: return
        
        scan_params = self.config['autonomous_scan']
        direction = 1

        while self.running:
            try:
                # Manuel kontrol var mı diye kontrol et
                if self.manual_override:
                    if time.time() - self.last_manual_command_time > self.config['manual_override_timeout']:
                        print(f"Kamera {self.id}: Manuel kontrol zaman aşımına uğradı, otonom taramaya dönülüyor.")
                        self.manual_override = False
                    else:
                        time.sleep(1)
                        continue
                
                # Tarama mantığı
                print(f"Kamera {self.id}: Pozisyon {scan_params['pan_start']}° hedefleniyor.")
                self.go_to_degree(scan_params['pan_start'], scan_params['tilt'])
                time.sleep(scan_params['dwell_time'])
                
                print(f"Kamera {self.id}: Pozisyon {scan_params['pan_end']}° hedefleniyor.")
                self.go_to_degree(scan_params['pan_end'], scan_params['tilt'])
                time.sleep(scan_params['dwell_time'])

            except Exception as e:
                print(f"Kamera {self.id} tarama döngüsü hatası: {e}")
                time.sleep(5)
    
    def data_loop(self):
        """ISAPI'den termal veri çekme döngüsü."""
        url = f"http://{self.config['ip']}/ISAPI/Thermal/channels/2/thermometry/realTimethermometry/rules?format=json"
        while self.running:
            try:
                with requests.get(url, auth=self.auth, stream=True, timeout=(5,65)) as r:
                    if r.status_code == 200:
                        buffer = b''
                        for chunk in r.iter_content(1024):
                            buffer += chunk
                            while b'--boundary' in buffer:
                                # ... (Önceki koddan JSON ayrıştırma mantığı)
                                pass
            except:
                time.sleep(5)
                
    # --- PTZ Kontrol Metotları ---
    def set_manual_override(self):
        """Manuel kontrolü başlatır."""
        self.manual_override = True
        self.last_manual_command_time = time.time()
        if self.ptz: self.ptz.Stop({'ProfileToken': self.token})
        print(f"Kamera {self.id}: Manuel kontrol devralındı.")

    def go_to_degree(self, pan_deg, tilt_deg):
        # ... (Önceki koddan degree_to_onvif_accurate ve AbsoluteMove mantığı)
        pass

# Bu sınıf tüm kamera worker'larını yönetir.
class CameraManager:
    def __init__(self, config_path='config.json'):
        self.workers = {}
        with open(config_path, 'r') as f:
            self.config = json.load(f)
        
        for cam_config in self.config['cameras']:
            if cam_config.get('enabled', False):
                self.workers[cam_config['id']] = CameraWorker(cam_config, self)
    
    def start_all(self):
        for worker in self.workers.values():
            worker.start()
            
    def stop_all(self):
        for worker in self.workers.values():
            worker.stop()
            
    def get_worker(self, camera_id):
        return self.workers.get(camera_id)