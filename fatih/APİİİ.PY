# import requests
# from requests.auth import HTTPDigestAuth

# # # Cihaz IP adresi ve kullanıcı bilgileri
# # url = 'http://192.168.1.64/ISAPI/System/deviceInfo'
# # auth = HTTPDigestAuth('admin', 'ErenEnerji')  # Şifrenizi doğru girin

# # # İstek gönder
# # response = requests.get(url, auth=auth)

# # # Durum kontrolü
# # if response.status_code == 200:
# #     print("Cihaz bilgisi alındı:")
# #     print(response.text)
# # else:
# #     print(f"Hata Kodu: {response.status_code}")
# #     print("İçerik:", response.text)





# # url = 'http://192.168.1.64/ISAPI/Thermal/channels/1/temperatureData'
# # auth = HTTPDigestAuth('admin', 'ErenEnerji')

# # response = requests.get(url, auth=auth)
# # print("Durum:", response.status_code)
# # print(response.text)



import requests
from requests.auth import HTTPDigestAuth

# Kamera IP'si ve kullanıcı bilgileri
ip = "192.168.1.64"
username = "admin"
password = "ErenEnerji"

# Yetkilendirme tanımı
auth = HTTPDigestAuth(username, password)

# Termal özellik sorgulama endpoint’i
url = f"http://{ip}/ISAPI/Thermal/capabilities"

# İstek gönder
response = requests.get(url, auth=auth)

# Durum ve içerik yazdır
print("Durum Kodu:", response.status_code)
print(response.text)
