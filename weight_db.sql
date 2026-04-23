-- =============================================
-- SmartWeight IoT - Database Schema
-- Database: iotsehat
-- =============================================

-- Gunakan database iotsehat (sudah dibuat di Laragon)
USE iotsehat;

-- Tabel untuk menyimpan riwayat berat badan
CREATE TABLE IF NOT EXISTS weight_history (
    id INT AUTO_INCREMENT PRIMARY KEY,
    weight_kg DECIMAL(5,2) NOT NULL COMMENT 'Berat badan dalam kilogram',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT 'Waktu pengukuran'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Index untuk mempercepat query pengambilan data terbaru
CREATE INDEX idx_created_at ON weight_history(created_at DESC);
