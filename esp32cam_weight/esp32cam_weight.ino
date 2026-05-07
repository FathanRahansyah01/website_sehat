// SmartWeight IoT - ESP32-CAM
// RFID + LCD + Buzzer + Live Preview
// =============================================
// Pin Assignment (pin muxing pada GPIO 14):
// RC522 SPI: SCK=14, MOSI=13, MISO=12, SS=15
// LCD I2C:   SDA=4,  SCL=14 (shared dengan SPI SCK)
// Buzzer:    GPIO 2
// =============================================

#include <WiFi.h>
#include <WebServer.h>
#include <HTTPClient.h>
#include <Wire.h>
#include <SPI.h>
#include <MFRC522.h>
#include <LiquidCrystal_I2C.h>
#include "esp_camera.h"

// --- WiFi ---
const char* ssid = "Barat";
const char* password = "gyfw4773";
const char* serverURL = "http://76.13.23.138:8080/index.php";

// --- Pin ---
// LCD I2C: SDA=2, SCL=14 (SCL shared dengan SPI SCK, pin muxing)
// RC522:   SS=15, SCK=14, MOSI=13, MISO=12
// Buzzer:  tidak ada pin bebas, pakai flash LED (GPIO 4) sebagai pengganti
#define RC522_SS    15
#define RC522_SCK   14
#define RC522_MOSI  13
#define RC522_MISO  12
#define LCD_SDA     2    // Pindah dari GPIO 4 (flash LED conflict)
#define LCD_SCL     14   // Shared dengan SPI SCK (pin muxing)

// Camera (AI-Thinker)
#define PWDN_GPIO_NUM  32
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM   0
#define SIOD_GPIO_NUM  26
#define SIOC_GPIO_NUM  27
#define Y9_GPIO_NUM    35
#define Y8_GPIO_NUM    34
#define Y7_GPIO_NUM    39
#define Y6_GPIO_NUM    36
#define Y5_GPIO_NUM    21
#define Y4_GPIO_NUM    19
#define Y3_GPIO_NUM    18
#define Y2_GPIO_NUM     5
#define VSYNC_GPIO_NUM 25
#define HREF_GPIO_NUM  23
#define PCLK_GPIO_NUM  22

// --- Objects ---
MFRC522 rfid(RC522_SS, -1);
LiquidCrystal_I2C lcd(0x27, 16, 2);
WebServer server(80);
bool spiActive = false;
bool flashOn = false;
bool isCapturing = false;  // Flag: sedang capture (stop mjpeg sementara)
SemaphoreHandle_t camMutex;  // Mutex kamera thread-safe

// =============================================
// Pin Muxing: switch antara SPI dan I2C
// GPIO 14 dipakai bergantian
// =============================================
void switchToSPI() {
    if (spiActive) return;
    Wire.end();
    SPI.begin(RC522_SCK, RC522_MISO, RC522_MOSI, RC522_SS);
    spiActive = true;
}

void switchToI2C() {
    if (!spiActive) return;
    SPI.end();
    Wire.begin(LCD_SDA, LCD_SCL);
    spiActive = false;
}

void lcdPrint(const char* line1, const char* line2) {
    switchToI2C();
    lcd.init();        // Re-init setelah pin muxing dari SPI
    lcd.backlight();
    lcd.clear();
    lcd.setCursor(0, 0);
    lcd.print(line1);
    if (line2) {
        lcd.setCursor(0, 1);
        lcd.print(line2);
    }
    switchToSPI();
}

// Visual feedback pakai flash LED (GPIO 4) karena tidak ada pin untuk buzzer
void beep(int times, int duration) {
    for (int i = 0; i < times; i++) {
        digitalWrite(4, HIGH);  // Flash ON
        delay(duration);
        digitalWrite(4, LOW);   // Flash OFF
        if (i < times - 1) delay(100);
    }
}

// =============================================
// HTML
// =============================================
const char HTML_PAGE[] PROGMEM = R"rawliteral(
<!DOCTYPE html>
<html><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SmartWeight Kamera</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Segoe UI',sans-serif;background:#1a1a1a;color:#fff;display:flex;flex-direction:column;align-items:center}
.hdr{padding:12px;text-align:center;width:100%;background:#E4002B}
.cam{position:relative;width:100%;max-width:640px;background:#000;margin:8px auto;min-height:240px}
.cam img{width:100%;display:block}
.guide{position:absolute;bottom:15%;left:10%;right:10%;height:30%;border:2px dashed rgba(0,255,0,.5);border-radius:8px}
.gt{position:absolute;bottom:5%;width:100%;text-align:center;font-size:11px;color:rgba(0,255,0,.7)}
.ctrl{padding:12px;display:flex;gap:8px;justify-content:center;width:100%;max-width:640px}
.btn{padding:12px 20px;border:none;border-radius:8px;font-size:16px;font-weight:700;cursor:pointer}
.bc{background:#E4002B;color:#fff;flex:2}
.bc:disabled{background:#666}
.bf{background:#333;color:#fff;flex:1}
.bf.on{background:#F59E0B;color:#000}
.st{padding:8px;text-align:center;font-size:13px}
.ok{color:#22C55E}.er{color:#EF4444}.ld{color:#F59E0B}
.res{padding:12px;text-align:center}
.w{font-size:48px;font-weight:800;color:#22C55E}
.u{font-size:18px;color:#999}
.inf{font-size:11px;color:#888;padding:4px}
</style></head><body>
<div class="hdr"><h3>SmartWeight Kamera</h3></div>
<div class="cam"><img id="s" src="/mjpeg"><div class="guide"></div><div class="gt">Posisikan angka timbangan di sini</div></div>
<div class="ctrl">
<button class="btn bc" id="b" onclick="cap()">&#128248; Capture</button>
<button class="btn bf" id="f" onclick="fl()">&#128161; Flash</button>
</div>
<div class="st" id="st">Siap. Tap RFID atau klik Capture.</div>
<div class="res" id="r" style="display:none"><div>Berat:</div><span class="w" id="wt">--</span><span class="u">kg</span></div>
<script>
async function cap(){
let b=document.getElementById('b'),s=document.getElementById('st'),r=document.getElementById('r');
b.disabled=1;b.textContent='⏳...';s.className='st ld';s.textContent='Mengirim...';r.style.display='none';
try{let res=await fetch('/capture'),d=await res.json();
if(d.success){s.className='st ok';s.textContent='✅ '+d.message;if(d.weight){r.style.display='block';document.getElementById('wt').textContent=d.weight}}
else{s.className='st er';s.textContent='❌ '+d.message}}
catch(e){s.className='st er';s.textContent='❌ '+e.message}
b.disabled=0;b.textContent='📸 Capture'}
async function fl(){let b=document.getElementById('f');
try{let r=await fetch('/flash'),d=await r.json();b.classList.toggle('on',d.flash);b.textContent=d.flash?'💡 ON':'💡 Flash'}catch(e){}}
</script></body></html>
)rawliteral";

// =============================================
// Setup
// =============================================
void setup() {
    Serial.begin(115200);
    Serial.println("\n=== SmartWeight IoT ===");

    // Init flash LED (GPIO 4) untuk feedback visual
    pinMode(4, OUTPUT);
    digitalWrite(4, LOW);

    // Bersihkan I2C bus sebelum camera init (cegah SCCB "bus busy")
    Wire.begin(LCD_SDA, LCD_SCL);
    delay(50);
    Wire.end();
    delay(100);

    // --- Kamera ---
    camera_config_t config;
    config.ledc_channel = LEDC_CHANNEL_0;
    config.ledc_timer = LEDC_TIMER_0;
    config.pin_d0 = Y2_GPIO_NUM;
    config.pin_d1 = Y3_GPIO_NUM;
    config.pin_d2 = Y4_GPIO_NUM;
    config.pin_d3 = Y5_GPIO_NUM;
    config.pin_d4 = Y6_GPIO_NUM;
    config.pin_d5 = Y7_GPIO_NUM;
    config.pin_d6 = Y8_GPIO_NUM;
    config.pin_d7 = Y9_GPIO_NUM;
    config.pin_xclk = XCLK_GPIO_NUM;
    config.pin_pclk = PCLK_GPIO_NUM;
    config.pin_vsync = VSYNC_GPIO_NUM;
    config.pin_href = HREF_GPIO_NUM;
    config.pin_sccb_sda = SIOD_GPIO_NUM;
    config.pin_sccb_scl = SIOC_GPIO_NUM;
    config.pin_pwdn = PWDN_GPIO_NUM;
    config.pin_reset = RESET_GPIO_NUM;
    config.xclk_freq_hz = 20000000;
    config.pixel_format = PIXFORMAT_JPEG;
    config.frame_size = FRAMESIZE_CIF;    // 400x296 — lebih jelas, tetap lancar
    config.jpeg_quality = 12;
    config.fb_count = 2;

    if (esp_camera_init(&config) != ESP_OK) {
        Serial.println("❌ Kamera gagal!");
        return;
    }
    Serial.println("✅ Kamera OK");

    sensor_t *s = esp_camera_sensor_get();
    s->set_hmirror(s, 0);
    s->set_vflip(s, 1);
    s->set_brightness(s, 1);
    s->set_contrast(s, 1);

    // --- LCD (I2C mode) ---
    Wire.begin(LCD_SDA, LCD_SCL);
    lcd.init();
    lcd.backlight();
    lcd.setCursor(0, 0);
    lcd.print("  SmartWeight");
    lcd.setCursor(0, 1);
    lcd.print("  Starting...");
    Serial.println("✅ LCD OK");
    spiActive = false;

    // --- RFID (SPI mode) ---
    // Force lepas I2C dari pin 14 dulu
    Wire.end();
    delay(50);

    // Set manual pin states sebelum SPI init
    pinMode(RC522_SCK, OUTPUT);
    pinMode(RC522_MOSI, OUTPUT);
    pinMode(RC522_MISO, INPUT);
    pinMode(RC522_SS, OUTPUT);
    digitalWrite(RC522_SS, HIGH);  // CS inactive

    SPI.begin(RC522_SCK, RC522_MISO, RC522_MOSI, RC522_SS);
    spiActive = true;
    delay(200);  // Beri waktu RC522 power up

    rfid.PCD_Init();
    delay(200);

    // Coba sampai 3x jika gagal
    byte v = rfid.PCD_ReadRegister(rfid.VersionReg);
    if (v == 0x00 || v == 0xFF) {
        // Retry
        rfid.PCD_Reset();
        delay(200);
        rfid.PCD_Init();
        delay(200);
        v = rfid.PCD_ReadRegister(rfid.VersionReg);
    }

    if (v == 0x00 || v == 0xFF) {
        Serial.println("⚠️ RFID tidak terdeteksi! Cek wiring.");
        lcdPrint("RFID ERROR!", "Cek wiring...");
    } else {
        Serial.printf("✅ RFID OK (v:0x%02X)\n", v);
    }

    // --- WiFi ---
    WiFi.begin(ssid, password);
    Serial.print("WiFi...");

    int att = 0;
    while (WiFi.status() != WL_CONNECTED && att < 30) {
        delay(500);
        Serial.print(".");
        att++;
    }

    if (WiFi.status() == WL_CONNECTED) {
        Serial.printf("\n✅ WiFi OK: http://%s\n", WiFi.localIP().toString().c_str());
        lcdPrint("WiFi Connected!", WiFi.localIP().toString().c_str());
        beep(1, 100);
    } else {
        Serial.println("\n❌ WiFi gagal!");
        lcdPrint("WiFi GAGAL!", "");
        return;
    }

    // --- Web Server ---
    server.on("/", handleRoot);
    server.on("/mjpeg", handleMjpeg);
    server.on("/capture", handleCapture);
    server.on("/flash", handleFlash);
    server.begin();
    Serial.println("✅ Server OK");

    // Jalankan web server di Core 0 (FreeRTOS task)
    // RFID polling tetap di Core 1 (loop utama)
    camMutex = xSemaphoreCreateMutex();
    xTaskCreatePinnedToCore(
        [](void*) {
            while (true) {
                server.handleClient();
                delay(1);
            }
        },
        "WebServerTask",
        8192,   // Stack size
        NULL,
        1,      // Priority
        NULL,
        0       // Core 0
    );
    Serial.println("✅ Web server task berjalan di Core 0");

    delay(2000);
    lcdPrint("SmartWeight IoT", "Tap kartu RFID");
}

// =============================================
// Loop — RFID polling (Core 1, tidak diblok stream)
// =============================================
void loop() {
    // Pastikan SPI aktif untuk baca RFID
    if (!spiActive) switchToSPI();

    if (!isCapturing && rfid.PICC_IsNewCardPresent() && rfid.PICC_ReadCardSerial()) {
        String uid = "";
        for (byte i = 0; i < rfid.uid.size; i++) {
            uid += String(rfid.uid.uidByte[i], HEX);
        }
        Serial.println("🪪 RFID: " + uid);

        beep(2, 150);
        lcdPrint("Capturing...", ("RFID:" + uid.substring(0, 10)).c_str());

        isCapturing = true;
        doCaptureAndSend();
        isCapturing = false;

        rfid.PICC_HaltA();
        rfid.PCD_StopCrypto1();
        delay(3000);
        lcdPrint("SmartWeight IoT", "Tap kartu RFID");
    }

    delay(50);
}

// =============================================
// Capture & Send
// =============================================
void doCaptureAndSend() {
    sensor_t *s = esp_camera_sensor_get();
    s->set_framesize(s, FRAMESIZE_VGA);
    s->set_quality(s, 10);
    delay(300);

    // Buang buffer lama (pakai mutex agar tidak bentrok dengan MJPEG stream)
    for (int i = 0; i < 3; i++) {
        if (xSemaphoreTake(camMutex, pdMS_TO_TICKS(200)) == pdTRUE) {
            camera_fb_t *d = esp_camera_fb_get();
            if (d) esp_camera_fb_return(d);
            xSemaphoreGive(camMutex);
        }
    }

    camera_fb_t *fb = NULL;
    if (xSemaphoreTake(camMutex, pdMS_TO_TICKS(500)) == pdTRUE) {
        fb = esp_camera_fb_get();
        xSemaphoreGive(camMutex);
    }

    // Kembalikan ke CIF (konsisten dengan resolusi default)
    s->set_framesize(s, FRAMESIZE_CIF);
    s->set_quality(s, 12);

    if (!fb) {
        Serial.println("Capture gagal!");
        lcdPrint("Capture GAGAL!", "Coba lagi...");
        beep(3, 100);
        return;
    }

    Serial.printf("Capture: %d bytes (%dx%d)\n", fb->len, fb->width, fb->height);
    lcdPrint("Mengirim...", (String(fb->len) + " bytes").c_str());

    String response = sendImage(fb->buf, fb->len);
    esp_camera_fb_return(fb);
    showResult(response);
}

void showResult(String response) {
    float weight = 0;
    bool ok = false;

    int idx = response.indexOf("\"weight_kg\":");
    if (idx == -1) idx = response.indexOf("\"weight\":");
    if (idx != -1) {
        int c = response.indexOf(":", idx);
        int e = response.indexOf(",", c);
        if (e == -1) e = response.indexOf("}", c);
        String ws = response.substring(c + 1, e);
        ws.trim();
        weight = ws.toFloat();
        if (weight >= 20 && weight <= 300) ok = true;
    }

    if (ok) {
        lcdPrint("Berat terbaca:", (String(weight, 1) + " kg").c_str());
        Serial.printf("✅ %.1f kg\n", weight);
        beep(1, 200);
    } else {
        lcdPrint("OCR Gagal :(", "Coba lagi...");
        Serial.println("❌ OCR gagal");
        beep(3, 80);
    }
}

// =============================================
// Web Handlers
// =============================================
void handleRoot() {
    server.send_P(200, "text/html", HTML_PAGE);
}

// MJPEG stream di Core 0 (tidak blok loop/RFID di Core 1)
void handleMjpeg() {
    WiFiClient client = server.client();
    client.println("HTTP/1.1 200 OK");
    client.println("Content-Type: multipart/x-mixed-replace; boundary=frame");
    client.println("Cache-Control: no-cache");
    client.println("Access-Control-Allow-Origin: *");
    client.println();

    while (client.connected() && !isCapturing) {
        if (xSemaphoreTake(camMutex, pdMS_TO_TICKS(100)) == pdTRUE) {
            camera_fb_t *fb = esp_camera_fb_get();
            xSemaphoreGive(camMutex);

            if (!fb) break;

            client.println("--frame");
            client.println("Content-Type: image/jpeg");
            client.printf("Content-Length: %d\r\n\r\n", fb->len);
            client.write(fb->buf, fb->len);
            client.println();

            esp_camera_fb_return(fb);
        }
        delay(50);  // ~20 FPS
    }
}

void handleCapture() {
    Serial.println("Capture via browser");
    beep(2, 150);
    lcdPrint("Capturing...", "(from browser)");

    isCapturing = true;  // Hentikan MJPEG stream sementara

    sensor_t *s = esp_camera_sensor_get();
    s->set_framesize(s, FRAMESIZE_VGA);
    s->set_quality(s, 10);
    delay(300);

    for (int i = 0; i < 3; i++) {
        if (xSemaphoreTake(camMutex, pdMS_TO_TICKS(200)) == pdTRUE) {
            camera_fb_t *d = esp_camera_fb_get();
            if (d) esp_camera_fb_return(d);
            xSemaphoreGive(camMutex);
        }
    }

    camera_fb_t *fb = NULL;
    if (xSemaphoreTake(camMutex, pdMS_TO_TICKS(500)) == pdTRUE) {
        fb = esp_camera_fb_get();
        xSemaphoreGive(camMutex);
    }

    // Kembalikan ke CIF
    s->set_framesize(s, FRAMESIZE_CIF);
    s->set_quality(s, 12);

    if (!fb) {
        isCapturing = false;
        server.send(500, "application/json", "{\"success\":false,\"message\":\"Capture gagal\"}");
        return;
    }

    String result = sendImage(fb->buf, fb->len);
    esp_camera_fb_return(fb);
    isCapturing = false;  // Stream boleh jalan lagi
    showResult(result);

    server.sendHeader("Access-Control-Allow-Origin", "*");
    server.send(200, "application/json", result);
    delay(3000);
    lcdPrint("SmartWeight IoT", "Tap kartu RFID");
}

void handleFlash() {
    flashOn = !flashOn;
    // Matikan I2C dulu agar tidak konflik dengan GPIO 4
    Wire.end();
    digitalWrite(4, flashOn ? HIGH : LOW);
    // Kalau perlu LCD lagi, switchToI2C() akan mengaktifkan kembali
    spiActive = true;  // Reset state supaya switchToI2C() bisa jalan

    Serial.printf("💡 Flash: %s\n", flashOn ? "ON" : "OFF");
    String json = "{\"flash\":" + String(flashOn ? "true" : "false") + "}";
    server.sendHeader("Access-Control-Allow-Origin", "*");
    server.send(200, "application/json", json);
}

// =============================================
// Send Image (dengan retry & WiFi recovery)
// =============================================
void ensureWiFi() {
    if (WiFi.status() == WL_CONNECTED) return;
    Serial.println("⚠️ WiFi terputus, reconnecting...");
    WiFi.disconnect();
    delay(500);
    WiFi.begin(ssid, password);
    int att = 0;
    while (WiFi.status() != WL_CONNECTED && att < 20) {
        delay(500);
        Serial.print(".");
        att++;
    }
    if (WiFi.status() == WL_CONNECTED) {
        Serial.printf("\n✅ WiFi reconnected: %s\n", WiFi.localIP().toString().c_str());
    } else {
        Serial.println("\n❌ WiFi reconnect gagal!");
    }
}

String sendImage(uint8_t *data, size_t len) {
    const int MAX_RETRIES = 3;
    String resp = "";

    // Delay agar WiFi stabil setelah kamera capture
    Serial.println("⏳ Menunggu WiFi stabil...");
    delay(1000);

    for (int attempt = 1; attempt <= MAX_RETRIES; attempt++) {
        // Pastikan WiFi masih konek
        ensureWiFi();
        if (WiFi.status() != WL_CONNECTED) {
            Serial.println("❌ WiFi tidak tersedia, skip pengiriman");
            return "{\"success\":false,\"message\":\"WiFi disconnected\"}";
        }

        Serial.printf("📡 [Attempt %d/%d] Mengirim %d bytes ke %s ...\n", attempt, MAX_RETRIES, len, serverURL);
        Serial.printf("   Free heap: %d bytes\n", ESP.getFreeHeap());

        HTTPClient http;
        http.begin(serverURL);
        http.addHeader("Content-Type", "image/jpeg");
        http.addHeader("Connection", "close");
        http.setTimeout(90000);  // 90 detik (EasyOCR lambat pertama kali load model)

        int code = http.POST(data, len);

        if (code > 0) {
            resp = http.getString();
            Serial.printf("✅ HTTP %d | %s\n", code, resp.substring(0, 150).c_str());
            http.end();
            return resp;  // Berhasil, langsung return
        } else {
            resp = "{\"success\":false,\"message\":\"" + http.errorToString(code) + "\"}";
            Serial.printf("❌ Attempt %d GAGAL! Code:%d | %s\n", attempt, code, http.errorToString(code).c_str());
            http.end();

            if (attempt < MAX_RETRIES) {
                int waitTime = attempt * 2000;  // 2s, 4s
                Serial.printf("⏳ Retry dalam %d ms...\n", waitTime);
                delay(waitTime);
            }
        }
    }

    Serial.println("❌ Semua attempt gagal!");
    return resp;
}
