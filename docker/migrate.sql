-- =============================================
-- SmartWeight IoT - Database Migration
-- Menambahkan kolom yang belum ada di database Docker lama
-- 
-- JALANKAN DI VPS:
-- docker compose exec db mysql -u smartweight -psmartweight123 iotsehat < /docker-entrypoint-initdb.d/migrate.sql
-- ATAU:
-- docker compose exec db mysql -u smartweight -psmartweight123 iotsehat
-- lalu paste isi file ini
-- =============================================

-- Tambah kolom ocr_weight jika belum ada
ALTER TABLE weight_history 
ADD COLUMN IF NOT EXISTS ocr_weight DECIMAL(5,2) DEFAULT NULL 
COMMENT 'Berat asli terbaca OCR' AFTER weight_kg;

-- Tambah kolom image_path jika belum ada  
ALTER TABLE weight_history 
ADD COLUMN IF NOT EXISTS image_path VARCHAR(255) DEFAULT NULL 
COMMENT 'Path gambar ESP32-CAM' AFTER ocr_weight;

-- Tambah kolom ocr_status jika belum ada
ALTER TABLE weight_history 
ADD COLUMN IF NOT EXISTS ocr_status ENUM('success','partial','failed') DEFAULT 'failed' 
COMMENT 'Status OCR' AFTER image_path;

-- Ubah weight_kg jadi nullable (NULL = OCR gagal total)
ALTER TABLE weight_history 
MODIFY COLUMN weight_kg DECIMAL(5,2) DEFAULT NULL;

-- Update data lama: set ocr_status = 'success' untuk data yang sudah ada weight
UPDATE weight_history SET ocr_status = 'success' WHERE weight_kg > 0 AND ocr_status = 'failed';

-- Verifikasi
SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT 
FROM INFORMATION_SCHEMA.COLUMNS 
WHERE TABLE_SCHEMA = 'iotsehat' AND TABLE_NAME = 'weight_history'
ORDER BY ORDINAL_POSITION;
