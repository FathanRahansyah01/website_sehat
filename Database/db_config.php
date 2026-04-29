<?php
// =============================================
// SmartWeight IoT - Database Configuration
// Konfigurasi koneksi MySQL (Laragon default)
// =============================================

$db_host = getenv('DB_HOST') ?: 'localhost';
$db_user = getenv('DB_USER') ?: 'root';
$db_pass = getenv('DB_PASS') ?: '';           // Default Laragon: password kosong
$db_name = getenv('DB_NAME') ?: 'iotsehat';

// Buat koneksi menggunakan MySQLi
$conn = new mysqli($db_host, $db_user, $db_pass, $db_name);

// Cek koneksi
if ($conn->connect_error) {
    http_response_code(500);
    echo json_encode([
        'success' => false,
        'message' => 'Koneksi database gagal: ' . $conn->connect_error
    ]);
    exit();
}

// Set charset UTF-8
$conn->set_charset('utf8mb4');
