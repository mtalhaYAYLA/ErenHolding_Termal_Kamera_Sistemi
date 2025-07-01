import requests
from requests.auth import HTTPDigestAuth
import xml.etree.ElementTree as ET

class ThermalSensor:
    def __init__(self, ip, username, password):
        self.url = f"http://{ip}/ISAPI/Thermometry/rule/1"  # Kameranın termometre sensörü endpoint'i
        self.auth = HTTPDigestAuth(username, password)

    def get_temperature(self):
        try:
            response = requests.get(self.url, auth=self.auth, timeout=5)
            if response.status_code == 200:
                root = ET.fromstring(response.content)
                temp = root.find("ruleTemperature")  # XML içinde sıcaklık verisini alıyoruz
                if temp is not None:
                    return float(temp.text)
                else:
                    print("Sıcaklık verisi bulunamadı.")
                    return None
            else:
                print(f"ISAPI Error {response.status_code}")
                return None
        except requests.exceptions.Timeout:
            print("Zaman aşımı hatası.")
            return None
        except Exception as e:
            print(f"ISAPI Hatası: {e}")
            return None
