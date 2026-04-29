-- =============================================
-- SmartWeight IoT - Database Schema Updates
-- Database: iotsehat
-- =============================================

USE iotsehat;

-- Tambah kolom image_path (jika belum ada)
ALTER TABLE weight_history 
ADD COLUMN IF NOT EXISTS image_path VARCHAR(255) DEFAULT NULL COMMENT 'Path gambar dari ESP32-CAM'
AFTER weight_kg;

-- Tambah kolom ocr_weight untuk menyimpan berat asli hasil OCR
ALTER TABLE weight_history 
ADD COLUMN IF NOT EXISTS ocr_weight DECIMAL(5,1) DEFAULT NULL COMMENT 'Berat asli terbaca OCR (sebelum koreksi)'
AFTER weight_kg;
