<?php
// =============================================
// SmartWeight IoT - Weight API (with Image + OCR)
// Backend untuk menerima gambar, OCR, & data berat
// =============================================

// Header CORS & JSON
header('Content-Type: application/json; charset=utf-8');
header('Access-Control-Allow-Origin: *');
header('Access-Control-Allow-Methods: GET, POST, OPTIONS');
header('Access-Control-Allow-Headers: Content-Type');

if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') {
    http_response_code(200);
    exit();
}

require_once 'db_config.php';

// --- Konfigurasi ---
define('UPLOAD_DIR', __DIR__ . '/uploads/');
define('TESSERACT_PATH', 'C:\\Program Files\\Tesseract-OCR\\tesseract.exe');
define('ALLOWED_EXTENSIONS', ['jpg', 'jpeg', 'png']);
define('MAX_FILE_SIZE', 5 * 1024 * 1024); // 5MB

// =============================================
// Routing
// =============================================

$method = $_SERVER['REQUEST_METHOD'];

if ($method === 'POST') {
    // Cek apakah POST ini mengirim gambar atau JSON biasa
    $contentType = isset($_SERVER['CONTENT_TYPE']) ? $_SERVER['CONTENT_TYPE'] : '';

    if (strpos($contentType, 'multipart/form-data') !== false || isset($_FILES['image'])) {
        // ESP32-CAM kirim gambar → OCR
        handleImageUpload($conn);
    } elseif (strpos($contentType, 'image/jpeg') !== false || strpos($contentType, 'image/png') !== false) {
        // ESP32-CAM kirim raw image bytes
        handleRawImageUpload($conn, $contentType);
    } else {
        // Kirim JSON biasa (manual / fallback)
        handlePostWeight($conn);
    }
} elseif ($method === 'GET') {
    $action = isset($_GET['action']) ? $_GET['action'] : 'latest';

    switch ($action) {
        case 'latest':
            handleGetLatest($conn);
            break;
        case 'history':
            $limit = isset($_GET['limit']) ? intval($_GET['limit']) : 10;
            handleGetHistory($conn, $limit);
            break;
        case 'image':
            // Serve gambar berdasarkan ID
            $id = isset($_GET['id']) ? intval($_GET['id']) : 0;
            handleGetImage($conn, $id);
            break;
        default:
            http_response_code(400);
            echo json_encode(['success' => false, 'message' => 'Action tidak dikenal']);
    }
} elseif ($method === 'DELETE') {
    $input = file_get_contents('php://input');
    $data = json_decode($input, true);

    if (isset($data['delete_all']) && $data['delete_all'] === true) {
        handleDeleteAll($conn);
    } elseif (isset($data['id'])) {
        handleDeleteWeight($conn, intval($data['id']));
    } else {
        http_response_code(400);
        echo json_encode(['success' => false, 'message' => 'Parameter id atau delete_all diperlukan']);
    }
} else {
    http_response_code(405);
    echo json_encode(['success' => false, 'message' => 'Method tidak diizinkan']);
}

$conn->close();

// =============================================
// Handler Functions
// =============================================

/**
 * POST - Menerima gambar mentah langsung dari ESP32-CAM
 * ESP32 mengirim JPEG bytes langsung di body request
 */
function handleRawImageUpload($conn, $contentType) {
    $imageData = file_get_contents('php://input');

    if (empty($imageData) || strlen($imageData) < 100) {
        http_response_code(400);
        echo json_encode(['success' => false, 'message' => 'Data gambar kosong atau terlalu kecil']);
        return;
    }

    if (strlen($imageData) > MAX_FILE_SIZE) {
        http_response_code(400);
        echo json_encode(['success' => false, 'message' => 'Ukuran gambar melebihi 5MB']);
        return;
    }

    // Tentukan ekstensi
    $ext = (strpos($contentType, 'png') !== false) ? 'png' : 'jpg';

    // Simpan gambar
    $filename = 'weight_' . date('Ymd_His') . '_' . uniqid() . '.' . $ext;
    $filepath = UPLOAD_DIR . $filename;

    if (!is_dir(UPLOAD_DIR)) {
        mkdir(UPLOAD_DIR, 0755, true);
    }

    file_put_contents($filepath, $imageData);

    // Jalankan OCR
    $weight = runOCR($filepath);

    if ($weight !== null) {
        // Simpan ke database (ocr_weight = weight awal)
        $imagePath = 'uploads/' . $filename;
        saveWeight($conn, $weight, $imagePath, $weight);

        echo json_encode([
            'success' => true,
            'message' => 'Gambar diterima, OCR berhasil',
            'data' => [
                'weight_kg' => $weight,
                'image_path' => $imagePath,
                'ocr_status' => 'success'
            ]
        ]);
    } else {
        // OCR gagal, simpan gambar tapi tanpa berat
        $imagePath = 'uploads/' . $filename;

        echo json_encode([
            'success' => false,
            'message' => 'Gambar disimpan, tapi OCR gagal membaca angka berat',
            'data' => [
                'image_path' => $imagePath,
                'ocr_status' => 'failed',
                'tip' => 'Pastikan gambar fokus pada angka timbangan'
            ]
        ]);
    }
}

/**
 * POST - Menerima gambar via multipart form-data
 */
function handleImageUpload($conn) {
    if (!isset($_FILES['image']) || $_FILES['image']['error'] !== UPLOAD_ERR_OK) {
        http_response_code(400);
        $errorMsg = isset($_FILES['image']) ? 'Upload error: ' . $_FILES['image']['error'] : 'Field "image" tidak ditemukan';
        echo json_encode(['success' => false, 'message' => $errorMsg]);
        return;
    }

    $file = $_FILES['image'];

    // Validasi ukuran
    if ($file['size'] > MAX_FILE_SIZE) {
        http_response_code(400);
        echo json_encode(['success' => false, 'message' => 'Ukuran file melebihi 5MB']);
        return;
    }

    // Validasi tipe file
    $ext = strtolower(pathinfo($file['name'], PATHINFO_EXTENSION));
    if (!in_array($ext, ALLOWED_EXTENSIONS)) {
        http_response_code(400);
        echo json_encode(['success' => false, 'message' => 'Format file harus JPG atau PNG']);
        return;
    }

    // Simpan file
    $filename = 'weight_' . date('Ymd_His') . '_' . uniqid() . '.' . $ext;
    $filepath = UPLOAD_DIR . $filename;

    if (!is_dir(UPLOAD_DIR)) {
        mkdir(UPLOAD_DIR, 0755, true);
    }

    if (!move_uploaded_file($file['tmp_name'], $filepath)) {
        http_response_code(500);
        echo json_encode(['success' => false, 'message' => 'Gagal menyimpan gambar']);
        return;
    }

    // Jalankan OCR
    $weight = runOCR($filepath);

    if ($weight !== null) {
        $imagePath = 'uploads/' . $filename;
        saveWeight($conn, $weight, $imagePath);

        echo json_encode([
            'success' => true,
            'message' => 'Gambar diterima, OCR berhasil',
            'data' => [
                'weight_kg' => $weight,
                'image_path' => $imagePath,
                'ocr_status' => 'success'
            ]
        ]);
    } else {
        $imagePath = 'uploads/' . $filename;

        echo json_encode([
            'success' => false,
            'message' => 'Gambar disimpan, tapi OCR gagal membaca angka',
            'data' => [
                'image_path' => $imagePath,
                'ocr_status' => 'failed',
                'tip' => 'Pastikan gambar fokus pada angka timbangan'
            ]
        ]);
    }
}

/**
 * Jalankan OCR menggunakan EasyOCR (Python)
 * Fallback ke Tesseract jika Python gagal
 * @return float|null Berat yang terbaca, atau null jika gagal
 */
function runOCR($imagePath) {
    // --- Metode 1: EasyOCR via Python (lebih akurat) ---
    $pythonScript = __DIR__ . '/ocr_reader.py';
    
    if (file_exists($pythonScript)) {
        $cmd = 'python "' . $pythonScript . '" "' . $imagePath . '" 2>&1';
        $output = shell_exec($cmd);
        
        error_log("EasyOCR output: " . $output);
        
        if ($output) {
            // Parse JSON dari output Python
            // Ambil baris terakhir yang berisi JSON (skip warnings)
            $lines = explode("\n", trim($output));
            $jsonLine = end($lines);
            $result = json_decode($jsonLine, true);
            
            if ($result && isset($result['success']) && $result['success'] && isset($result['weight'])) {
                $weight = floatval($result['weight']);
                if ($weight >= 20 && $weight <= 300) {
                    error_log("EasyOCR berhasil: {$weight} kg");
                    return $weight;
                }
            }
        }
        
        error_log("EasyOCR gagal, mencoba Tesseract...");
    }
    
    // --- Metode 2: Fallback ke Tesseract ---
    $tesseract = TESSERACT_PATH;
    
    if (!file_exists($tesseract)) {
        error_log("Tesseract juga tidak ditemukan");
        return null;
    }
    
    $outputFile = tempnam(sys_get_temp_dir(), 'ocr_');
    $cmd = '"' . $tesseract . '" "' . $imagePath . '" "' . $outputFile . '" --psm 7 -c tessedit_char_whitelist=0123456789.';
    exec($cmd . ' 2>&1', $output, $returnCode);
    
    $ocrResult = '';
    if (file_exists($outputFile . '.txt')) {
        $ocrResult = trim(file_get_contents($outputFile . '.txt'));
        unlink($outputFile . '.txt');
    }
    if (file_exists($outputFile)) {
        unlink($outputFile);
    }
    
    error_log("Tesseract result: '$ocrResult'");
    
    if (!empty($ocrResult) && preg_match('/(\d+\.?\d*)/', $ocrResult, $matches)) {
        $weight = floatval($matches[1]);
        if ($weight >= 20 && $weight <= 300) {
            return $weight;
        }
    }
    
    return null;
}

/**
 * Simpan data berat ke database
 */
function saveWeight($conn, $weight, $imagePath = null, $ocrWeight = null) {
    $stmt = $conn->prepare("INSERT INTO weight_history (weight_kg, ocr_weight, image_path) VALUES (?, ?, ?)");
    $stmt->bind_param("dds", $weight, $ocrWeight, $imagePath);
    $stmt->execute();
    $stmt->close();
}

/**
 * POST - Menerima data berat via JSON (manual / fallback)
 */
function handlePostWeight($conn) {
    $input = file_get_contents('php://input');
    $data = json_decode($input, true);

    if (!$data || !isset($data['weight_kg'])) {
        http_response_code(400);
        echo json_encode(['success' => false, 'message' => 'Field "weight_kg" wajib diisi']);
        return;
    }

    $weight = floatval($data['weight_kg']);

    if ($weight < 1 || $weight > 500) {
        http_response_code(400);
        echo json_encode(['success' => false, 'message' => 'Berat harus antara 1 - 500 kg']);
        return;
    }

    // Jika ada ID, lakukan UPDATE (Koreksi manual)
    if (isset($data['id'])) {
        $id = intval($data['id']);
        $stmt = $conn->prepare("UPDATE weight_history SET weight_kg = ? WHERE id = ?");
        $stmt->bind_param("di", $weight, $id);
        
        if ($stmt->execute()) {
            http_response_code(200);
            echo json_encode([
                'success' => true,
                'message' => 'Data berat berhasil dikoreksi secara manual',
                'data' => [
                    'id' => $id,
                    'weight_kg' => $weight,
                    'is_manual' => true
                ]
            ]);
        } else {
            http_response_code(500);
            echo json_encode(['success' => false, 'message' => 'Gagal mengoreksi data']);
        }
    } else {
        // Jika tidak ada ID, lakukan INSERT baru
        $stmt = $conn->prepare("INSERT INTO weight_history (weight_kg) VALUES (?)");
        $stmt->bind_param("d", $weight);

        if ($stmt->execute()) {
            http_response_code(201);
            echo json_encode([
                'success' => true,
                'message' => 'Data berat berhasil disimpan',
                'data' => [
                    'id' => $stmt->insert_id,
                    'weight_kg' => $weight,
                    'created_at' => date('Y-m-d H:i:s')
                ]
            ]);
        } else {
            http_response_code(500);
            echo json_encode(['success' => false, 'message' => 'Gagal menyimpan data']);
        }
    }

    $stmt->close();
}

/**
 * GET - Data berat terbaru
 */
function handleGetLatest($conn) {
    $result = $conn->query(
        "SELECT id, weight_kg, ocr_weight, image_path, created_at 
         FROM weight_history 
         ORDER BY created_at DESC 
         LIMIT 1"
    );

    if ($result && $result->num_rows > 0) {
        $row = $result->fetch_assoc();
        $data = [
            'id' => intval($row['id']),
            'weight_kg' => floatval($row['weight_kg']),
            'ocr_weight' => $row['ocr_weight'] !== null ? floatval($row['ocr_weight']) : null,
            'image_path' => $row['image_path'],
            'image_url' => $row['image_path'] ? $row['image_path'] : null,
            'created_at' => $row['created_at']
        ];
        echo json_encode(['success' => true, 'data' => $data]);
    } else {
        echo json_encode(['success' => true, 'data' => null, 'message' => 'Belum ada data']);
    }
}

/**
 * GET - Riwayat berat badan
 */
function handleGetHistory($conn, $limit) {
    $limit = max(1, min(100, $limit));
    $stmt = $conn->prepare(
        "SELECT id, weight_kg, ocr_weight, image_path, created_at 
         FROM weight_history 
         ORDER BY created_at DESC 
         LIMIT ?"
    );
    $stmt->bind_param("i", $limit);
    $stmt->execute();
    $result = $stmt->get_result();

    $history = [];
    while ($row = $result->fetch_assoc()) {
        $history[] = [
            'id' => intval($row['id']),
            'weight_kg' => floatval($row['weight_kg']),
            'ocr_weight' => $row['ocr_weight'] !== null ? floatval($row['ocr_weight']) : null,
            'image_path' => $row['image_path'],
            'image_url' => $row['image_path'] ? $row['image_path'] : null,
            'created_at' => $row['created_at']
        ];
    }

    echo json_encode(['success' => true, 'count' => count($history), 'data' => $history]);
    $stmt->close();
}

/**
 * GET - Serve gambar berdasarkan ID record
 */
function handleGetImage($conn, $id) {
    if ($id <= 0) {
        http_response_code(400);
        echo json_encode(['success' => false, 'message' => 'ID tidak valid']);
        return;
    }

    $stmt = $conn->prepare("SELECT image_path FROM weight_history WHERE id = ?");
    $stmt->bind_param("i", $id);
    $stmt->execute();
    $result = $stmt->get_result();

    if ($result && $row = $result->fetch_assoc()) {
        if ($row['image_path'] && file_exists(__DIR__ . '/' . $row['image_path'])) {
            echo json_encode([
                'success' => true,
                'data' => ['image_url' => $row['image_path']]
            ]);
        } else {
            echo json_encode(['success' => true, 'data' => ['image_url' => null], 'message' => 'Tidak ada gambar']);
        }
    } else {
        http_response_code(404);
        echo json_encode(['success' => false, 'message' => 'Data tidak ditemukan']);
    }

    $stmt->close();
}

/**
 * DELETE - Hapus satu data berat berdasarkan ID
 */
function handleDeleteWeight($conn, $id) {
    if ($id <= 0) {
        http_response_code(400);
        echo json_encode(['success' => false, 'message' => 'ID tidak valid']);
        return;
    }

    // Ambil image_path dulu untuk hapus file
    $stmt = $conn->prepare("SELECT image_path FROM weight_history WHERE id = ?");
    $stmt->bind_param("i", $id);
    $stmt->execute();
    $result = $stmt->get_result();

    if ($result && $row = $result->fetch_assoc()) {
        // Hapus file gambar jika ada
        if ($row['image_path'] && file_exists(__DIR__ . '/' . $row['image_path'])) {
            unlink(__DIR__ . '/' . $row['image_path']);
        }
        // Hapus juga file preprocessed (jika ada)
        $base = __DIR__ . '/' . pathinfo($row['image_path'], PATHINFO_DIRNAME) . '/' . pathinfo($row['image_path'], PATHINFO_FILENAME);
        foreach (['_crop_enhance.png', '_thresh_low.png', '_thresh_high.png', '_inverted.png', '_upscale.png'] as $suffix) {
            if (file_exists($base . $suffix)) unlink($base . $suffix);
        }
    }
    $stmt->close();

    // Hapus dari database
    $stmt = $conn->prepare("DELETE FROM weight_history WHERE id = ?");
    $stmt->bind_param("i", $id);

    if ($stmt->execute()) {
        echo json_encode(['success' => true, 'message' => 'Data berhasil dihapus']);
    } else {
        http_response_code(500);
        echo json_encode(['success' => false, 'message' => 'Gagal menghapus data']);
    }
    $stmt->close();
}

/**
 * DELETE - Hapus semua data riwayat
 */
function handleDeleteAll($conn) {
    // Hapus semua file gambar di uploads
    $result = $conn->query("SELECT image_path FROM weight_history WHERE image_path IS NOT NULL");
    while ($row = $result->fetch_assoc()) {
        if ($row['image_path'] && file_exists(__DIR__ . '/' . $row['image_path'])) {
            unlink(__DIR__ . '/' . $row['image_path']);
        }
    }

    // Hapus semua data
    if ($conn->query("DELETE FROM weight_history")) {
        echo json_encode(['success' => true, 'message' => 'Semua data riwayat berhasil dihapus']);
    } else {
        http_response_code(500);
        echo json_encode(['success' => false, 'message' => 'Gagal menghapus data']);
    }
}
