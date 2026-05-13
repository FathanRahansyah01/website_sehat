-- =============================================
-- SmartWeight IoT - Database Init (Docker)
-- Dijalankan otomatis saat container MySQL pertama kali dibuat
-- =============================================

USE iotsehat;

-- Tabel utama dengan semua kolom terbaru
CREATE TABLE IF NOT EXISTS weight_history (
    id INT AUTO_INCREMENT PRIMARY KEY,
    weight_kg DECIMAL(5,2) DEFAULT NULL COMMENT 'Berat final (setelah koreksi manual, atau = ocr_weight)',
    ocr_weight DECIMAL(5,2) DEFAULT NULL COMMENT 'Berat asli terbaca OCR (sebelum koreksi)',
    image_path VARCHAR(255) DEFAULT NULL COMMENT 'Path gambar dari ESP32-CAM',
    ocr_status ENUM('success','partial','failed','manual') DEFAULT 'failed' COMMENT 'Status OCR: success/partial/failed/manual',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT 'Waktu pengukuran'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Index untuk query data terbaru
CREATE INDEX IF NOT EXISTS idx_created_at ON weight_history(created_at DESC);
