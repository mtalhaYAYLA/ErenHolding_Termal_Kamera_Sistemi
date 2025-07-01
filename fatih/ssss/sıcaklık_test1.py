import requests
import numpy as np
from requests.auth import HTTPDigestAuth

# === KAMERA BİLGİLERİ ===
CAMERA_IP = '192.168.1.64'
CAMERA_USER = 'admin'
CAMERA_PASS = 'ErenEnerji'

# Test edilecek ISAPI uç noktası
PIXEL_DATA_URL = f'http://{CAMERA_IP}/ISAPI/Thermal/channels/2/thermometry/pixelToPixelData' 

def test_thermal_data():
    """ISAPI'den pixel-to-pixel verisini almayı dener ve analiz eder."""
    print(f"Bağlanılan URL: {PIXEL_DATA_URL}")
    auth = HTTPDigestAuth(CAMERA_USER, CAMERA_PASS)
    
    try:
        # GET isteği gönder. Timeout süresini artırabilirsiniz.
        response = requests.get(PIXEL_DATA_URL, auth=auth, timeout=5)
        
        print(f"HTTP Durum Kodu: {response.status_code}")
        print("--- Yanıt Başlıkları (Headers) ---")
        for key, value in response.headers.items():
            print(f"{key}: {value}")
        print("---------------------------------")

        if response.status_code == 200:
            # Yanıt başarılıysa içeriği analiz et
            content_type = response.headers.get('Content-Type', '')
            print(f"\nİçerik Tipi: {content_type}")
            
            # Yanıt bir resim ve ek data içeriyorsa (multipart)
            if 'multipart' in content_type:
                print("Multipart yanıt algılandı. Sınır (boundary) ile ayrıştırma denenmeli.")
                # Bu kısım daha karmaşık bir ayrıştırma gerektirir.
                # Şimdilik sadece içeriğin ilk 500 byte'ını gösterelim.
                print(f"İçeriğin başı: {response.content[:500]}")

            # Yanıt sadece ham binary veri ise
            else:
                print("Ham binary veri algılandı.")
                # Veriyi 16-bit unsigned integer dizisine dönüştürmeyi dene
                # Byte sırası ('<': little-endian, '>': big-endian) önemli olabilir.
                try:
                    data_array = np.frombuffer(response.content, dtype=np.uint16)
                    print(f"Başarıyla numpy dizisine dönüştürüldü. Toplam eleman sayısı: {data_array.size}")
                    
                    # Varsayımsal çözünürlüğe göre yeniden şekillendir
                    width, height = THERMAL_SENSOR_WIDTH, THERMAL_SENSOR_HEIGHT
                    if data_array.size == width * height:
                        temp_matrix = data_array.reshape((height, width))
                        print(f"Matris boyutu: {temp_matrix.shape}")
                        
                        # Sıcaklık dönüşümünü yap (Varsayım: Celsius = Değer / 100)
                        temps_celsius = temp_matrix.astype(np.float32) / 100.0
                        
                        print("\n--- Örnek Sıcaklık Değerleri ---")
                        print(f"Min Sıcaklık: {np.min(temps_celsius):.2f} °C")
                        print(f"Maks Sıcaklık: {np.max(temps_celsius):.2f} °C")
                        print(f"Ort. Sıcaklık: {np.mean(temps_celsius):.2f} °C")
                    else:
                        print(f"HATA: Beklenen piksel sayısı ({width*height}) ile gelen veri boyutu ({data_array.size}) uyuşmuyor.")
                
                except Exception as e:
                    print(f"Numpy'a dönüştürme hatası: {e}")

        else:
            print(f"\nHata İçeriği:\n{response.text}")

    except requests.exceptions.RequestException as e:
        print(f"\nBağlantı Hatası: {e}")

if __name__ == "__main__":
    test_thermal_data()