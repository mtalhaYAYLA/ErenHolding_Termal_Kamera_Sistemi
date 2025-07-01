# api_test_final.py
# Bu betik, API'nin çalışma mantığına tam uyumlu olarak, doğrudan giriş yapar,
# doğru formatta yeni bir ürün oluşturur, bu ürünü ID'si ile siler ve işlemi doğrular.

import requests
import time

# --- 1. AYARLAR BÖLÜMÜ ---
BASE_URL = "http://localhost:3000"

# !!! DEĞİŞTİRİLECEK ALAN !!!
# Postman'de başarılı giriş yaptığınız test kullanıcısı e-postasını ve şifresini buraya girin.
# Verdiğiniz loglara göre bu bilgileri kullanıyorum:
TEST_USER_EMAIL = "test1@example.com"
TEST_USER_PASSWORD = "123456"
# --------------------------------------------------------------------------

# Global değişkenler, test adımları arasında veri taşımak için
access_token = None
product_id_to_test = None

# --- 2. TEST SENARYOSU ---
print("API Test Senaryosu Başlatıldı (Nihai Versiyon)")
print("-" * 50)

# --- ADIM 1: GİRİŞ YAPMA VE TOKEN ALMA ---
print(">>> ADIM 1: Var olan test kullanıcısı ile giriş yapılıyor...")
login_url = f"{BASE_URL}/auth/login"
login_payload = {"email": TEST_USER_EMAIL, "password": TEST_USER_PASSWORD}

try:
    response = requests.post(login_url, json=login_payload, timeout=5)

    if response.status_code in [200, 201]:
        # Sunucunun 'access_token' anahtarını kullanarak token'ı alıyoruz
        access_token = response.json().get("access_token")
        if access_token:
            print("✅ BAŞARILI: Giriş yapıldı ve token alındı.")
        else:
            print("❌ HATA: Giriş başarılı ama yanıtta 'access_token' bulunamadı.")
    else:
        print(f"❌ HATA: Giriş başarısız! Kod: {response.status_code}, Mesaj: {response.text}")

except requests.exceptions.RequestException as e:
    print(f"❌ KRİTİK HATA: Sunucuya bağlanılamıyor. Hata: {e}")
    # Bağlantı yoksa, senaryonun geri kalanını çalıştırmanın anlamı yok.
    exit()

print("-" * 50)
time.sleep(1)

# --- ADIM 2: YENİ BİR ÜRÜN OLUŞTURMA ---
if access_token:
    print(">>> ADIM 2: Yeni bir test ürünü oluşturuluyor...")
    products_url = f"{BASE_URL}/products"
    headers = {"Authorization": f"Bearer {access_token}"}
    
    # Sunucunun beklediği doğru ve temiz veri yapısı (payload)
    product_payload = {
        "name": f"Otomasyon Test Ürünü - {int(time.time())}",
        "description": "Bu ürün Python otomasyon betiği ile oluşturulmuştur.",
        "price": 19.99,
        "stock": 150
    }
    print(f"   Gönderilen Veri: {product_payload}")

    response = requests.post(products_url, json=product_payload, headers=headers)

    if response.status_code == 201: # 201 = Created (Başarıyla Oluşturuldu)
        new_product = response.json()
        # Bir sonraki adımda kullanmak üzere, sunucunun oluşturduğu ID'yi saklıyoruz
        product_id_to_test = new_product.get('id')
        print(f"✅ BAŞARILI: Yeni ürün oluşturuldu. Test edilecek ID: {product_id_to_test}")
    else:
        print(f"❌ HATA: Yeni ürün oluşturulamadı! Kod: {response.status_code}, Mesaj: {response.text}")
else:
    print(">>> ADIM 2 ATLANDI: Token olmadığı için ürün oluşturulamıyor.")

print("-" * 50)
time.sleep(1)

# --- ADIM 3: OLUŞTURULAN ÜRÜNÜ ID'Sİ İLE GETİRME (GET /products/{id}) ---
if product_id_to_test:
    print(f">>> ADIM 3: ID'si {product_id_to_test} olan ürünün bilgileri getiriliyor...")
    
    product_url = f"{BASE_URL}/products/{product_id_to_test}"
    # GET istekleri de korumalı olabilir, bu yüzden header eklemek güvenlidir.
    headers = {"Authorization": f"Bearer {access_token}"}

    response = requests.get(product_url, headers=headers)
    
    if response.status_code == 200:
        fetched_product = response.json()
        print("✅ BAŞARILI: Ürün bilgileri başarıyla getirildi.")
        print(f"   Gelen Ürün: {fetched_product}")
    else:
        print(f"❌ HATA: Ürün bilgileri getirilemedi! Kod: {response.status_code}, Mesaj: {response.text}")
else:
    print(">>> ADIM 3 ATLANDI: Test edilecek bir ürün ID'si bulunamadı.")
    
print("-" * 50)
time.sleep(1)

# --- ADIM 4: OLUŞTURULAN ÜRÜNÜ SİLME (DELETE) ---
if product_id_to_test:
    print(f">>> ADIM 4: ID'si {product_id_to_test} olan ürün siliniyor...")
    delete_url = f"{BASE_URL}/products/{product_id_to_test}"
    headers = {"Authorization": f"Bearer {access_token}"}
    
    response = requests.delete(delete_url, headers=headers)

    if response.status_code in [200, 204]: # 200 veya 204 başarılı silme demektir
        print(f"✅ BAŞARILI: Ürün silindi! (Durum Kodu: {response.status_code})")
    else:
        print(f"❌ HATA: Ürün silinemedi! Kod: {response.status_code}, Mesaj: {response.text}")
else:
    print(">>> ADIM 4 ATLANDI: Silinecek bir ürün ID'si bulunamadı.")

print("-" * 50)
print("API Test Senaryosu Tamamlandı.")