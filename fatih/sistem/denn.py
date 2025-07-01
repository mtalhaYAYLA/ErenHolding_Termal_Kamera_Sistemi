endpoints = [
    "https://192.168.1.64/ISAPI/Thermometry/rule",
    "https://192.168.1.64/ISAPI/Thermometry/thermometryRegions",
    "https://192.168.1.64/ISAPI/Thermometry/temperature",
    "https://192.168.1.64/ISAPI/Thermometry/rule/1",
]

from requests.auth import HTTPDigestAuth
import requests

for url in endpoints:
    try:
        r = requests.get(url, auth=HTTPDigestAuth("admin", "ErenEnerji"), verify=False)
        print(f"{url} → {r.status_code}")
        print(r.text[:300])  # İlk 300 karakter
    except Exception as e:
        print(f"{url} → HATA: {e}")
