# ==============================================================================
# PROJE: Python ile REST API Test Otomasyonu
# AMAÇ: Docker ile sunulan bir API'nin temel işlevlerini (Login, Create, Read, Delete)
#       otomatikleştirmek ve çalışma mantığını anlamak.
# YAZAR: Talha
# VERSİYON: 1.0 - Nihai ve Açıklamalı Sürüm
# ==============================================================================

# --- BÖLÜM 1: GEREKLİ KÜTÜPHANELERİN İÇE AKTARILMASI ---

# 'requests' kütüphanesi: HTTP istekleri (GET, POST, DELETE vb.) göndermek için
# Python'daki endüstri standardı kütüphanedir. Bizim API ile konuşan "tercümanımızdır".
import requests

# 'time' kütüphanesi: Zamanla ilgili işlemler yapmak için kullanılır.
# Programı bir süre bekletmek (time.sleep) veya benzersiz değerler üretmek
# (time.time) için kullanacağız.
import time

# ==============================================================================
# --- BÖLÜM 2: PROJE AYARLARI VE GLOBAL DEĞİŞKENLER ---
#
# Testlerimizi daha yönetilebilir ve okunabilir kılmak için tüm sabit
# ayarları ve adımlar arasında veri taşıyacak değişkenleri burada tanımlıyoruz.
# ==============================================================================

# API'nin çalıştığı ana adres. Yarın sunucu adresi değişirse,
# sadece bu satırı değiştirmemiz yeterli olacaktır.
BASE_URL = "http://localhost:3000"

# Postman'de başarılı giriş yaptığımız, sistemde önceden var olan
# test kullanıcısının e-posta ve şifresi.
# DİKKAT: 'register' endpoint'i yetki gerektirdiği için yeni kullanıcı oluşturmuyoruz.
TEST_USER_EMAIL = "test1@example.com"
TEST_USER_PASSWORD = "123456"

# --- Global Değişkenler ---
# Bu değişkenler, senaryonun farklı adımları arasında bilgi taşımak için kullanılır.
# Başlangıçta 'None' (boş) olarak ayarlanırlar.

# Adım 1'de giriş yaptıktan sonra sunucudan alacağımız erişim anahtarını (token)
# bu değişkende saklayacağız.
access_token = None

# Adım 2'de yeni bir ürün oluşturduktan sonra, sunucunun bu ürüne atadığı
# benzersiz ID'yi bu değişkende saklayacağız. Bu ID'yi daha sonra ürünü
# getirmek ve silmek için kullanacağız.
product_id_to_test = None

# ==============================================================================
# --- BÖLÜM 3: OTOMASYON SENARYOSUNUN ÇALIŞTIRILMASI ---
#
# Bu bölüm, test senaryomuzun adımlarını sırasıyla çalıştırır.
# Her adımın çıktısı, bir sonraki adımın girdisi olabilir.
# ==============================================================================

print("API Test Senaryosu Başlatıldı (Nihai ve Açıklamalı Versiyon)")
print("-" * 60)

# --- ADIM 1: GİRİŞ YAPMA VE TOKEN ALMA (AUTHENTICATION) ---
# Senaryomuzun ilk ve en önemli adımı. Kimliğimizi kanıtlayıp
# korumalı işlemleri yapmak için bir "giriş kartı" (token) almalıyız.
# ------------------------------------------------------------------------------
print(">>> ADIM 1: Var olan test kullanıcısı ile giriş yapılıyor...")

# Giriş yapılacak tam URL'i oluşturuyoruz. f-string, değişkenleri metne kolayca ekler.
login_url = f"{BASE_URL}/auth/login"

# Sunucuya göndereceğimiz veri (payload). Bu bir Python sözlüğüdür.
# 'requests' kütüphanesi bunu otomatik olarak JSON formatına çevirecektir.
login_payload = {"email": TEST_USER_EMAIL, "password": TEST_USER_PASSWORD}

try:
    # `requests.post()` fonksiyonu ile sunucuya bir POST isteği gönderiyoruz.
    # `json=login_payload`: Hazırladığımız veriyi isteğin gövdesine ekler.
    # `timeout=5`: Eğer sunucu 5 saniye içinde cevap vermezse, hataya düş.
    response = requests.post(login_url, json=login_payload, timeout=5)

    # API'nin standart dışı olarak hem 200 (OK) hem de 201 (Created)
    # durum kodlarını başarılı giriş için döndürebileceğini keşfettik.
    # Bu yüzden ikisini de kontrol ediyoruz.
    if response.status_code in [200, 201]:
        
        # `response.json()`: Sunucudan gelen JSON metnini bir Python sözlüğüne çevirir.
        # `.get("access_token")`: Bu sözlüğün içinden "access_token" anahtarının
        # değerini güvenli bir şekilde alır. (Anahtar yoksa hata vermez, None döndürür).
        access_token = response.json().get("access_token")
        
        if access_token:
            print("✅ BAŞARILI: Giriş yapıldı ve erişim token'ı alındı.")
        else:
            print("❌ HATA: Giriş başarılı ama yanıtta 'access_token' anahtarı bulunamadı.")
    else:
        # Giriş başarısızsa, hem durum kodunu hem de sunucunun hata mesajını yazdırıyoruz.
        print(f"❌ HATA: Giriş başarısız! Kod: {response.status_code}, Mesaj: {response.text}")

except requests.exceptions.RequestException as e:
    # Bu blok, sunucuya hiç ulaşılamaması (bağlantı hatası, zaman aşımı vb.)
    # gibi ağ ile ilgili sorunları yakalar.
    print(f"❌ KRİTİK HATA: Sunucuya bağlanılamıyor. Docker konteynerinin çalıştığından emin olun. Hata: {e}")
    exit() # Senaryoyu devam ettirmenin bir anlamı olmadığı için programdan çık.

print("-" * 60)
# Bir sonraki adıma geçmeden önce sunucunun kendine gelmesi için 1 saniye bekliyoruz.
time.sleep(1)


# --- ADIM 2: YENİ BİR ÜRÜN OLUŞTURMA (CREATE) ---
# Bu adım, bir önceki adımda token alınıp alınmadığına bağlıdır.
# ------------------------------------------------------------------------------
if access_token:
    print(">>> ADIM 2: Yeni bir test ürünü oluşturuluyor...")
    products_url = f"{BASE_URL}/products"
    
    # HTTP Başlıkları (Headers): Bu, isteğin "zarfıdır".
    # `Authorization` başlığı, sunucuya kim olduğumuzu kanıtlamak için kullanılır.
    # `Bearer` şeması, "bu token'ı taşıyan kişi yetkilidir" anlamına gelir.
    headers = {"Authorization": f"Bearer {access_token}"}
    
    # Analizlerimiz sonucu belirlediğimiz, sunucunun beklediği doğru veri yapısı.
    # 'id', 'created_at' gibi alanları göndermiyoruz, çünkü onları sunucu kendi atar.
    product_payload = {
        "name": f"Otomasyon Test Ürünü - {int(time.time())}", # Her seferinde benzersiz isim
        "description": "Bu ürün Python otomasyon betiği ile oluşturulmuştur.",
        "price": 19.99,
        "stock": 150
    }

    # POST isteğini bu sefer hem `json` (veri) hem de `headers` (kimlik) ile gönderiyoruz.
    response = requests.post(products_url, json=product_payload, headers=headers)

    if response.status_code == 201: # 201 = Created (Başarıyla Oluşturuldu)
        new_product = response.json()
        # Sunucunun yeni oluşturduğu ürüne verdiği ID'yi yakalayıp saklıyoruz.
        product_id_to_test = new_product.get('id')
        print(f"✅ BAŞARILI: Yeni ürün oluşturuldu. Test edilecek ID: {product_id_to_test}")
    else:
        print(f"❌ HATA: Yeni ürün oluşturulamadı! Kod: {response.status_code}, Mesaj: {response.text}")
else:
    # Eğer ilk adımda token alamadıysak, bu adımı atlıyoruz.
    print(">>> ADIM 2 ATLANDI: Token olmadığı için ürün oluşturulamıyor.")

print("-" * 60)
time.sleep(1)


# --- ADIM 3: OLUŞTURULAN ÜRÜNÜ GETİRME (READ) ---
# Bu adım, URL'e eklenen "Yol Parametresi" (Path Parameter) kullanımını gösterir.
# ------------------------------------------------------------------------------
if product_id_to_test:
    print(f">>> ADIM 3: ID'si {product_id_to_test} olan ürünün bilgileri getiriliyor...")
    
    # URL'in sonuna, bir önceki adımda aldığımız ürün ID'sini ekliyoruz.
    product_url = f"{BASE_URL}/products/{product_id_to_test}"
    headers = {"Authorization": f"Bearer {access_token}"}

    # `requests.get()` ile tek bir ürünün detayını istiyoruz.
    response = requests.get(product_url, headers=headers)
    
    if response.status_code == 200: # 200 = OK
        print("✅ BAŞARILI: Ürün bilgileri başarıyla getirildi.")
        print(f"   Gelen Ürün Detayı: {response.json()}")
    else:
        print(f"❌ HATA: Ürün bilgileri getirilemedi! Kod: {response.status_code}, Mesaj: {response.text}")
else:
    print(">>> ADIM 3 ATLANDI: Test edilecek bir ürün ID'si bulunamadı.")
    
print("-" * 60)
time.sleep(1)


# --- ADIM 4: OLUŞTURULAN ÜRÜNÜ SİLME (DELETE) ---
# Bu, veritabanını değiştiren 'yıkıcı' bir işlemdir ve mutlaka yetki gerektirir.
# ------------------------------------------------------------------------------
if product_id_to_test:
    print(f">>> ADIM 4: ID'si {product_id_to_test} olan ürün siliniyor...")
    delete_url = f"{BASE_URL}/products/{product_id_to_test}"
    headers = {"Authorization": f"Bearer {access_token}"}
    
    # `requests.delete()` metodunu kullanarak silme isteği gönderiyoruz.
    response = requests.delete(delete_url, headers=headers)

    # Başarılı bir silme işlemi genellikle 200 (OK) veya 204 (No Content) döner.
    if response.status_code in [200, 204]:
        print(f"✅ BAŞARILI: Ürün silindi! (Durum Kodu: {response.status_code})")
    else:
        print(f"❌ HATA: Ürün silinemedi! Kod: {response.status_code}, Mesaj: {response.text}")
else:
    print(">>> ADIM 4 ATLANDI: Silinecek bir ürün ID'si bulunamadı.")

print("-" * 60)
print("API Test Senaryosu Tamamlandı.")
# ==============================================================================
# --- SON ---
# ==============================================================================