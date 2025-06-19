# import requests
# from requests.auth import HTTPDigestAuth

# # Kamera IP'si ve giriş bilgileri
# ip = "192.168.1.64"
# username = "admin"
# password = "ErenEnerji"
# auth = HTTPDigestAuth(username, password)

# # Cihazın desteklediği özellikleri kontrol et
# url = f"http://{ip}/ISAPI/System/deviceInfo"
# response = requests.get(url, auth=auth)

# # Durum kontrolü
# if response.status_code == 200:
#     print("Cihaz Bilgileri:")
#     print(response.text)
# else:
#     print(f"❌ Hata: {response.status_code}")
#     print(response.text)



import requests
from requests.auth import HTTPDigestAuth

ip = "192.168.1.64"
username = "admin"
password = "ErenEnerji"
auth = HTTPDigestAuth(username, password)

# Termometri kuralı
url = f"http://{ip}//ISAPI/System/capabilities"
response = requests.get(url, auth=auth)
print("Durum Kodu:", response.status_code)
print(response.text)

print("-------------------------------------------------------------------")

url = f"http://{ip}//ISAPI/Thermal/channels/2/fireDetection/capabilities"
response = requests.get(url, auth=auth)
print("Durum Kodu:", response.status_code)
print(response.text)

# Önce cihaz bilgisi çekmeyi deneyin
url = f"http://{ip}/ISAPI/Thermal/channels/2/thermometryMode/capabilities"
response = requests.get(url, auth=auth)
print("Durum Kodu:", response.status_code)
print(response.text)


url = f"http://{ip}/ISAPI/Thermal/channels/2/thermometry"
response = requests.get(url, auth=auth)
print("Durum Kodu:", response.status_code)
print(response.text)


print("-------------------------------------------------------------------")


url = f"http://{ip}/ISAPI/Thermal/channels/2/thermometry/basicParam"
response = requests.get(url, auth=auth)
print("Durum Kodu:", response.status_code)
print(response.text)
print("-------------------------------------------------------------------")