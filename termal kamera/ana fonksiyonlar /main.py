import sys
import os
import cv2
import time
import threading
import requests
import json
import numpy as np
from onvif import ONVIFCamera
from onvif.exceptions import ONVIFError
from requests.auth import HTTPDigestAuth


# --- Yapılandırma Yöneticisi ---
CONFIG_FILE = 'config.json'
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"

def load_config():
    """ config.json dosyasını yükler. """
    if not os.path.exists(CONFIG_FILE):
        print(f"HATA: '{CONFIG_FILE}' bulunamadı. Lütfen örnek dosyayı oluşturun.")
        sys.exit(1)
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, KeyError) as e:
        print(f"HATA: '{CONFIG_FILE}' dosyası bozuk veya eksik. Hata: {e}")
        sys.exit(1)

# --- Çevre Değişkenleri ---
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"

class RTSPFrameGrabber(threading.Thread):
    """ Sadece alarm anında kayıt için arka planda kare yakalayan thread. """
    def __init__(self, rtsp_url, scanner_instance, is_thermal=False):
        super().__init__(daemon=True)
        self.rtsp_url = rtsp_url
        self.scanner = scanner_instance
        self.is_thermal = is_thermal
        self.stream_name = "Termal" if is_thermal else "Normal"
        self._run_flag = True

    def run(self):
        print(f"[{self.stream_name} Görüntü Akışı] Başlatılıyor...")
        while self._run_flag:
            cap = None
            try:
                cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
                if not cap.isOpened():
                    print(f"[{self.stream_name} Görüntü Akışı] Bağlantı hatası. 5 saniye sonra tekrar denenecek.")
                    time.sleep(5)
                    continue
                
                while self._run_flag:
                    ret, frame = cap.read()
                    if not ret or frame is None:
                        print(f"[{self.stream_name} Görüntü Akışı] Veri alınamıyor...")
                        break
                    
                    with self.scanner.frame_lock:
                        if self.is_thermal:
                            self.scanner.latest_thermal_frame = frame.copy()
                        else:
                            self.scanner.latest_normal_frame = frame.copy()
                    time.sleep(0.1) # CPU kullanımını düşürmek için
            except Exception as e:
                print(f"HATA [{self.stream_name} Görüntü Akışı]: {e}")
                time.sleep(5)
            finally:
                if cap: cap.release()
        print(f"[{self.stream_name} Görüntü Akışı] Durduruldu.")

    def stop(self):
        self._run_flag = False

class ThermalDataThread(threading.Thread):
    """ Arka planda termal verileri çeken thread. """
    def __init__(self, url, user, password, scanner_instance):
        super().__init__(daemon=True)
        self.url = url
        self.auth = HTTPDigestAuth(user, password)
        self.scanner = scanner_instance
        self._run_flag = True

    def run(self):
        print("[Termal Veri Akışı] Başlatılıyor...")
        while self._run_flag:
            try:
                with requests.get(self.url, auth=self.auth, stream=True, timeout=(5, 10)) as response:
                    if response.status_code == 200:
                        buffer = b''
                        for chunk in response.iter_content(chunk_size=1024):
                            if not self._run_flag: break
                            buffer += chunk
                            while b'--boundary' in buffer:
                                parts = buffer.split(b'--boundary', 1)
                                block, buffer = parts[0], parts[1]
                                if b'Content-Type: application/json' in block:
                                    json_start = block.find(b'{'); json_end = block.rfind(b'}')
                                    if json_start != -1 and json_end != -1:
                                        json_str = block[json_start:json_end+1].decode('utf-8')
                                        try:
                                            self.scanner.process_thermal_data(json.loads(json_str))
                                        except json.JSONDecodeError: pass
                    else:
                        print(f"[Termal Veri Akışı] Bağlantı hatası: {response.status_code}. 5 sn sonra yeniden denenecek.")
                        time.sleep(5)
            except requests.exceptions.RequestException:
                if self._run_flag:
                    print("[Termal Veri Akışı] Bağlantı kesildi. 5 sn sonra yeniden denenecek.")
                    time.sleep(5)
        print("[Termal Veri Akışı] Durduruldu.")
        
    def stop(self):
        self._run_flag = False

class ConsolePTZScanner:
    PAN_DEGREE_RANGE = (0, 360)
    TILT_DEGREE_RANGE = (-5, 90)
    ONVIF_RANGE = (-1.0, 1.0)

    def __init__(self, config):
        self.config = config
        self.ptz = None
        self.profile = None
        self.token = None
        self._run_flag = True

        self.last_ptz_status = None
        self.last_thermal_data = None
        self.last_max_temp = None
        
        self.frame_lock = threading.Lock()
        self.latest_normal_frame = None
        self.latest_thermal_frame = None
        self.last_alarm_time = 0
        self.alarms_base_dir = self.config['alarm_settings']['save_directory']
        os.makedirs(self.alarms_base_dir, exist_ok=True)

        self.thread_thermal_data = None
        self.thread_grabber_normal = None
        self.thread_grabber_thermal = None
        
    def _init_onvif(self):
        """ ONVIF bağlantısını kurar. """
        try:
            cam_conf = self.config['camera']
            print("ONVIF: Kamera ile bağlantı kuruluyor...")
            self.cam = ONVIFCamera(cam_conf['ip'], cam_conf['port'], cam_conf['user'], cam_conf['password'])
            self.ptz = self.cam.create_ptz_service()
            media_service = self.cam.create_media_service()
            profiles = media_service.GetProfiles()
            self.profile = next((p for p in profiles if hasattr(p, 'PTZConfiguration') and p.PTZConfiguration is not None), None)
            
            if not self.profile:
                print("HATA: Kamerada PTZ destekli bir medya profili bulunamadı!")
                return False
            
            self.token = self.profile.token
            print(f"ONVIF bağlantısı başarılı. Profil: {self.profile.Name}")
            return True
        except Exception as e:
            print(f"ONVIF bağlantısı başarısız: {e}")
            return False

    def _map_value(self, value, from_min, from_max, to_min, to_max):
        return to_min + (to_max - to_min) * (value - from_min) / (from_max - from_min) if (from_max - from_min) != 0 else to_min

    def _convert_ptz_to_degrees(self, ptz_pos):
        pan_onvif = ptz_pos['pan']
        pan_deg = self._map_value(pan_onvif, 0, 1, 0, 180) if pan_onvif >= 0 else self._map_value(pan_onvif, -1, 0, 180, 360)
        tilt_deg = self._map_value(ptz_pos['tilt'], self.ONVIF_RANGE[0], self.ONVIF_RANGE[1], self.TILT_DEGREE_RANGE[1], self.TILT_DEGREE_RANGE[0])
        return {'pan': pan_deg, 'tilt': tilt_deg}

    def _convert_degrees_to_ptz(self, pan_deg, tilt_deg):
        pan_onvif = self._map_value(pan_deg, 0, 180, 0, 1) if 0 <= pan_deg <= 180 else self._map_value(pan_deg, 180, 360, -1, 0)
        tilt_onvif = self._map_value(tilt_deg, self.TILT_DEGREE_RANGE[0], self.TILT_DEGREE_RANGE[1], self.ONVIF_RANGE[1], self.ONVIF_RANGE[0])
        return pan_onvif, tilt_onvif

    def _update_ptz_status(self):
        """ Kameranın mevcut PTZ konumunu alır ve saklar. """
        try:
            status = self.ptz.GetStatus({'ProfileToken': self.token})
            if status and status.Position:
                self.last_ptz_status = status.Position
                return True
        except ONVIFError as e:
            print(f"PTZ durumu alınamadı: {e}")
        return False
        
    def go_to_degree(self, pan_deg, tilt_deg):
        """ Kamerayı belirtilen derece pozisyonuna hareket ettirir. """
        if not self.ptz: return
        
        pan_onvif, tilt_onvif = self._convert_degrees_to_ptz(pan_deg, tilt_deg)
        zoom_val = self.last_ptz_status.Zoom.x if self.last_ptz_status else 0
        
        req = self.ptz.create_type('AbsoluteMove')
        req.ProfileToken = self.token
        req.Position = {'PanTilt': {'x': pan_onvif, 'y': tilt_onvif}, 'Zoom': zoom_val}
        req.Speed = {'PanTilt': {'x': 1.0, 'y': 1.0}}
        
        try:
            self.ptz.AbsoluteMove(req)
            # DEĞİŞİKLİK BURADA: Hareketin tamamlanması için daha fazla bekle
            time.sleep(5) # Bu süreyi 3'ten 5'e çıkaralım
            self._update_ptz_status()
        except ONVIFError as e:
            print(f"HATA: Pozisyona gitme başarısız: {e}")

    def process_thermal_data(self, data):
        """ Termal veri thread'inden gelen JSON'ı işler. """
        try:
            self.last_thermal_data = data
            therm_data = data.get('ThermometryUploadList', {}).get('ThermometryUpload', [{}])[0]
            self.last_max_temp = therm_data.get('LinePolygonThermCfg', {}).get('MaxTemperature')
        except (IndexError, KeyError) as e:
            print(f"Termal veri işlenirken hata: {e}")
            self.last_max_temp = None

    def check_for_alarm(self):
        """ Sıcaklık eşiğini kontrol eder ve alarm tetikler. """
        alarm_conf = self.config['alarm_settings']
        if self.last_max_temp is None: return
        
        threshold = alarm_conf['threshold_c']
        cooldown = alarm_conf['cooldown_sec']
        
        if time.time() - self.last_alarm_time < cooldown: return
        
        if self.last_max_temp > threshold:
            current_pos_deg = self._convert_ptz_to_degrees({'pan': self.last_ptz_status.PanTilt.x, 'tilt': self.last_ptz_status.PanTilt.y})
            # DEĞİŞİKLİK BURADA
            print("\n" + "="*50)
            print(f"!!! ALARM !!!  Sıcaklık: {self.last_max_temp:.1f}°C (Eşik: {threshold}°C)")
            print(f"Konum: Yatay (X): {current_pos_deg['pan']:.1f}°, Dikey (Y): {current_pos_deg['tilt']:.1f}°")
            print("="*50 + "\n")
            
            self.last_alarm_time = time.time()
            self.save_alarm_data()

    def save_alarm_data(self):
        """ Alarm anındaki görüntüleri ve verileri diske kaydeder. """
        with self.frame_lock:
            normal_frame = self.latest_normal_frame
            thermal_frame = self.latest_thermal_frame
        
        if normal_frame is not None and thermal_frame is not None:
            ts_folder = time.strftime("%Y-%m-%d_%H-%M-%S")
            alarm_folder_path = os.path.join(self.alarms_base_dir, ts_folder)
            os.makedirs(alarm_folder_path, exist_ok=True)
            
            cv2.imwrite(os.path.join(alarm_folder_path, "normal.jpg"), normal_frame)
            cv2.imwrite(os.path.join(alarm_folder_path, "thermal.jpg"), thermal_frame)
            
            if self.last_thermal_data:
                with open(os.path.join(alarm_folder_path, "alarm_data.json"), 'w', encoding='utf-8') as f:
                    json.dump(self.last_thermal_data, f, ensure_ascii=False, indent=4)
            
            print(f"-> Alarm verileri kaydedildi: {alarm_folder_path}")

    def print_scan_info(self, scan_settings):
        """ Tarama başlangıcında kullanıcıyı bilgilendirir. """
        mode = scan_settings.get('mode', 'grid')
        s = scan_settings
        pan_min, pan_max = min(s['pan_start_deg'], s['pan_end_deg']), max(s['pan_start_deg'], s['pan_end_deg'])
        tilt_min, tilt_max = min(s['tilt_start_deg'], s['tilt_end_deg']), max(s['tilt_start_deg'], s['tilt_end_deg'])
        step = s['step_deg']

        print("\n" + "="*20 + " TARAMA BAŞLIYOR " + "="*20)
        print("Ayarlar 'config.json' dosyasından okundu.")
        
        if mode == 'pan_only':
            print(f"Mod: Yalnızca Yatay Eksen (pan_only)")
            print(f"Açıklama: Kamera, sabit Dikey (Y)={tilt_min}° pozisyonunda,")
            print(f"Yatay (X) ekseninde {pan_min}° ile {pan_max}° arasında {step}° adımlarla tarama yapacak.")
        elif mode == 'tilt_only':
            print(f"Mod: Yalnızca Dikey Eksen (tilt_only)")
            print(f"Açıklama: Kamera, sabit Yatay (X)={pan_min}° pozisyonunda,")
            print(f"Dikey (Y) ekseninde {tilt_min}° ile {tilt_max}° arasında {step}° adımlarla tarama yapacak.")
        else: # grid
            print(f"Mod: Izgara Tarama (grid)")
            print(f"Açıklama: Kamera, Yatay (X) ekseninde {pan_min}°-{pan_max}° ve")
            print(f"Dikey (Y) ekseninde {tilt_min}°-{tilt_max}° aralığını {step}° adımlarla tarayacak.")
        
        print("-" * 54)
        print("Tarama döngüsünü durdurmak için CTRL+C tuşlarına basın.")
        print("-" * 54)


    def run(self):
        """ Ana tarama döngüsünü başlatır ve yönetir. """
        if not self._init_onvif():
            return

        cam_conf = self.config['camera']
        rtsp_url_normal = f'rtsp://{cam_conf["user"]}:{cam_conf["password"]}@{cam_conf["ip"]}:554/Streaming/Channels/101'
        rtsp_url_thermal = f'rtsp://{cam_conf["user"]}:{cam_conf["password"]}@{cam_conf["ip"]}:554/Streaming/Channels/201'
        realtime_thermometry_url = f'http://{cam_conf["ip"]}/ISAPI/Thermal/channels/2/thermometry/realTimethermometry/rules?format=json'

        self.thread_thermal_data = ThermalDataThread(realtime_thermometry_url, cam_conf['user'], cam_conf['password'], self)
        self.thread_grabber_normal = RTSPFrameGrabber(rtsp_url_normal, self, is_thermal=False)
        self.thread_grabber_thermal = RTSPFrameGrabber(rtsp_url_thermal, self, is_thermal=True)
        
        self.thread_thermal_data.start()
        self.thread_grabber_normal.start()
        self.thread_grabber_thermal.start()

        print("İlk PTZ konumu alınıyor...")
        while not self.last_ptz_status:
            self._update_ptz_status()
            time.sleep(1)

        s = self.config['scan_settings']
        self.print_scan_info(s) # BİLGİLENDİRME MESAJI

        pan_min, pan_max = min(s['pan_start_deg'], s['pan_end_deg']), max(s['pan_start_deg'], s['pan_end_deg'])
        tilt_min, tilt_max = min(s['tilt_start_deg'], s['tilt_end_deg']), max(s['tilt_start_deg'], s['tilt_end_deg'])
        step = s['step_deg']
        wait_time = s['wait_sec']
        
        current_pan = pan_min
        current_tilt = tilt_min
        pan_direction = 1

        while self._run_flag:
            if s['mode'] == 'pan_only':
                target_pan, target_tilt = current_pan, tilt_min
            elif s['mode'] == 'tilt_only':
                target_pan, target_tilt = pan_min, current_tilt
            else: # grid
                target_pan, target_tilt = current_pan, current_tilt
            
            # DEĞİŞİKLİK BURADA
            print(f"Hareket ediliyor -> Yatay (X): {target_pan:.1f}°, Dikey (Y): {target_tilt:.1f}°")
            self.go_to_degree(target_pan, target_tilt)

            print(f"Pozisyonda {wait_time} saniye bekleniyor...")
            time.sleep(wait_time)

            max_temp_str = f"{self.last_max_temp:.1f}°C" if self.last_max_temp is not None else "Veri Yok"
            # DEĞİŞİKLİK BURADA
            print(f"Durum: Konum (X={target_pan:.1f}°, Y={target_tilt:.1f}°) | Maks. Sıcaklık: {max_temp_str}")
            self.check_for_alarm()
            print("-" * 54)
            
            if s['mode'] == 'pan_only':
                current_pan += step * pan_direction
                if not (pan_min <= current_pan <= pan_max):
                    pan_direction *= -1
                    current_pan += step * pan_direction * 2
            elif s['mode'] == 'tilt_only':
                current_tilt += step
                if current_tilt > tilt_max:
                    current_tilt = tilt_min
            else: # grid
                current_pan += step * pan_direction
                if current_pan > pan_max or current_pan < pan_min:
                    pan_direction *= -1
                    current_pan += step * pan_direction
                    current_tilt += step
                    if current_tilt > tilt_max:
                        current_tilt = tilt_min

    def stop(self):
        """ Programı ve tüm thread'leri güvenli bir şekilde durdurur. """
        print("\nProgram durduruluyor...")
        self._run_flag = False
        
        if self.thread_thermal_data: self.thread_thermal_data.stop()
        if self.thread_grabber_normal: self.thread_grabber_normal.stop()
        if self.thread_grabber_thermal: self.thread_grabber_thermal.stop()

        if self.ptz:
            try:
                self.ptz.Stop({'ProfileToken': self.token})
                print("PTZ hareketi durduruldu.")
            except ONVIFError:
                pass
        
        print("Tüm işlemler tamamlandı. Çıkılıyor.")

if __name__ == "__main__":
    config = load_config()
    scanner = ConsolePTZScanner(config)
    
    try:
        scanner.run()
    except KeyboardInterrupt:
        pass
    finally:
        scanner.stop()