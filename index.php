<?php
// =============================================
// SmartWeight IoT - Combined Dashboard + API
// Hybrid: render HTML (GET tanpa ?action) atau JSON API
// =============================================

require_once __DIR__ . '/Database/db_config.php';

// --- Konfigurasi ---
define('UPLOAD_DIR', __DIR__ . '/uploads/');
// Tesseract dihapus, hanya pakai EasyOCR via Python
define('ALLOWED_EXTENSIONS', ['jpg', 'jpeg', 'png']);
define('MAX_FILE_SIZE', 5 * 1024 * 1024); // 5MB

// =============================================
// Routing: API atau Halaman?
// =============================================
$method = $_SERVER['REQUEST_METHOD'];
$isApiRequest = ($method !== 'GET' || isset($_GET['action']));

if ($isApiRequest) {
    // === MODE API: respons JSON ===
    header('Content-Type: application/json; charset=utf-8');
    header('Access-Control-Allow-Origin: *');
    header('Access-Control-Allow-Methods: GET, POST, OPTIONS');
    header('Access-Control-Allow-Headers: Content-Type');

    if ($method === 'OPTIONS') {
        http_response_code(200);
        exit();
    }

    if ($method === 'POST') {
        $contentType = isset($_SERVER['CONTENT_TYPE']) ? $_SERVER['CONTENT_TYPE'] : '';
        if (strpos($contentType, 'multipart/form-data') !== false || isset($_FILES['image'])) {
            handleImageUpload($conn);
        } elseif (strpos($contentType, 'image/jpeg') !== false || strpos($contentType, 'image/png') !== false) {
            handleRawImageUpload($conn, $contentType);
        } else {
            handlePostWeight($conn);
        }
    } elseif ($method === 'GET') {
        $action = $_GET['action'];
        switch ($action) {
            case 'latest':
                handleGetLatest($conn);
                break;
            case 'history':
                $limit = isset($_GET['limit']) ? intval($_GET['limit']) : 10;
                handleGetHistory($conn, $limit);
                break;
            case 'image':
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
    exit(); // Stop disini, jangan render HTML
}

// === MODE HALAMAN: render dashboard HTML di bawah ===
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

    $ext = (strpos($contentType, 'png') !== false) ? 'png' : 'jpg';
    $filename = 'weight_' . date('Ymd_His') . '_' . uniqid() . '.' . $ext;
    $filepath = UPLOAD_DIR . $filename;

    if (!is_dir(UPLOAD_DIR)) {
        mkdir(UPLOAD_DIR, 0755, true);
    }

    file_put_contents($filepath, $imageData);
    $imagePath = 'uploads/' . $filename;

    // Jalankan OCR — hasilnya array {weight, confidence, ocr_status}
    $ocrResult = runOCR($filepath);
    $weight = $ocrResult['weight'];
    $ocrStatus = $ocrResult['ocr_status'];
    $confidence = $ocrResult['confidence'];

    // SELALU simpan ke database — apapun hasil OCR
    saveWeight($conn, $weight, $imagePath, $weight, $ocrStatus);

    // Response: selalu success karena gambar tersimpan
    $message = 'Gambar diterima';
    if ($ocrStatus === 'success') {
        $message .= ', OCR berhasil: ' . $weight . ' kg';
    } elseif ($ocrStatus === 'partial') {
        $message .= ', OCR parsial: ' . $weight . ' kg (confidence rendah, bisa dikoreksi manual)';
    } else {
        $message .= ', OCR gagal membaca angka (bisa input manual)';
    }

    echo json_encode([
        'success' => true,
        'message' => $message,
        'data' => [
            'weight_kg' => $weight,
            'image_path' => $imagePath,
            'ocr_status' => $ocrStatus,
            'confidence' => $confidence
        ]
    ]);
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

    if ($file['size'] > MAX_FILE_SIZE) {
        http_response_code(400);
        echo json_encode(['success' => false, 'message' => 'Ukuran file melebihi 5MB']);
        return;
    }

    $ext = strtolower(pathinfo($file['name'], PATHINFO_EXTENSION));
    if (!in_array($ext, ALLOWED_EXTENSIONS)) {
        http_response_code(400);
        echo json_encode(['success' => false, 'message' => 'Format file harus JPG atau PNG']);
        return;
    }

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

    $imagePath = 'uploads/' . $filename;
    $ocrResult = runOCR($filepath);
    $weight = $ocrResult['weight'];
    $ocrStatus = $ocrResult['ocr_status'];

    // SELALU simpan ke database
    saveWeight($conn, $weight, $imagePath, $weight, $ocrStatus);

    $message = ($ocrStatus === 'success') ? 'OCR berhasil: ' . $weight . ' kg' :
               (($ocrStatus === 'partial') ? 'OCR parsial: ' . $weight . ' kg' : 'OCR gagal, input manual diperlukan');

    echo json_encode([
        'success' => true,
        'message' => $message,
        'data' => [
            'weight_kg' => $weight,
            'image_path' => $imagePath,
            'ocr_status' => $ocrStatus
        ]
    ]);
}

/**
 * Jalankan OCR menggunakan EasyOCR (Python)
 * @return array ['weight' => float|null, 'confidence' => float, 'ocr_status' => 'success'|'partial'|'failed']
 */
function runOCR($imagePath) {
    $pythonScript = __DIR__ . '/ocr_reader.py';
    $defaultResult = ['weight' => null, 'confidence' => 0, 'ocr_status' => 'failed'];
    
    if (!file_exists($pythonScript)) {
        error_log("OCR script not found: " . $pythonScript);
        return $defaultResult;
    }

    $pythonBin = 'python3';
    if (strtoupper(substr(PHP_OS, 0, 3)) === 'WIN') {
        $pythonBin = 'python';
    }
    $cmd = $pythonBin . ' "' . $pythonScript . '" "' . $imagePath . '" 2>&1';
    error_log("OCR command: " . $cmd);
    $output = shell_exec($cmd);
    
    error_log("OCR raw output: " . substr($output ?? '', 0, 500));
    
    if (!$output) {
        error_log("OCR: no output from Python");
        return $defaultResult;
    }

    // Cari JSON dari output (scan dari akhir)
    $lines = explode("\n", trim($output));
    $jsonLine = null;
    for ($i = count($lines) - 1; $i >= 0; $i--) {
        $trimmed = trim($lines[$i]);
        if (strpos($trimmed, '{') === 0) {
            $jsonLine = $trimmed;
            break;
        }
        if (strpos($trimmed, '[RESULT] {') !== false) {
            $jsonLine = substr($trimmed, strpos($trimmed, '{'));
            break;
        }
    }
    
    if (!$jsonLine) {
        error_log("OCR: no JSON found in output");
        return $defaultResult;
    }

    $result = json_decode($jsonLine, true);
    if (!$result) {
        error_log("OCR: JSON decode failed: " . $jsonLine);
        return $defaultResult;
    }

    $weight = isset($result['weight']) ? floatval($result['weight']) : null;
    $confidence = isset($result['confidence']) ? floatval($result['confidence']) : 0;
    $ocrStatus = isset($result['ocr_status']) ? $result['ocr_status'] : 'failed';

    // Validasi weight masuk akal (sangat permisif: 1-500 kg)
    if ($weight !== null && ($weight < 1 || $weight > 500)) {
        error_log("OCR: weight out of range: " . $weight);
        $weight = null;
        $ocrStatus = 'failed';
    }

    error_log("OCR result: weight=" . ($weight ?? 'null') . ", status=" . $ocrStatus . ", conf=" . $confidence);
    
    return [
        'weight' => $weight,
        'confidence' => $confidence,
        'ocr_status' => $ocrStatus
    ];
}

/**
 * Simpan data berat ke database
 * SELALU dipanggil — weight bisa null jika OCR gagal total
 */
function saveWeight($conn, $weight, $imagePath = null, $ocrWeight = null, $ocrStatus = 'success') {
    $stmt = $conn->prepare("INSERT INTO weight_history (weight_kg, ocr_weight, image_path) VALUES (?, ?, ?)");
    // Jika weight null (OCR failed), simpan 0 sebagai placeholder
    $weightVal = ($weight !== null) ? $weight : 0;
    $ocrVal = ($ocrWeight !== null) ? $ocrWeight : 0;
    $stmt->bind_param("dds", $weightVal, $ocrVal, $imagePath);
    $stmt->execute();
    $stmt->close();
    error_log("Saved: weight=" . $weightVal . ", ocr=" . $ocrVal . ", status=" . $ocrStatus . ", image=" . $imagePath);
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
?>
<!DOCTYPE html>
<html lang="id">

<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Smart Weight Monitor - IoT Health</title>
    <meta name="description" content="Sistem monitoring berat badan berbasis IoT untuk kesehatan Anda">
    <link rel="stylesheet" href="weight.css">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
</head>

<body>

    <!-- Navbar -->
    <nav class="navbar">
        <div class="nav-container">
            <div class="nav-brand">
                <span class="brand-text">Smart<span class="brand-highlight">Weight</span></span>
            </div>
            <ul class="nav-links">
                <li><a href="index.php" class="nav-link active">Dashboard</a></li>
                <li><a href="Riwayat/riwayat.html" class="nav-link">Riwayat</a></li>
            </ul>
        </div>
    </nav>

    <!-- Hero Section -->
    <header class="hero">
        <div class="hero-content">
            <div class="hero-text">
                <div class="hero-badge">IoT Connected</div>
                <h1 class="hero-title">Smart Weight <span class="text-gradient">Monitor</span></h1>
                <p class="hero-subtitle">Pantau berat badan Anda secara real-time menggunakan teknologi IoT. Data langsung dari sensor timbangan pintar.</p>
            </div>
        </div>
    </header>

    <!-- Main Content -->
    <main class="main-content">

        <!-- Status Bar -->
        <div class="status-bar">
            <div class="status-item">
                <span class="status-dot online"></span>
                <span class="status-text">Sensor Aktif</span>
            </div>
            <div class="status-item">
                <span class="status-text-muted">Terakhir update:</span>
                <span class="status-text" id="lastUpdate">-- : -- : --</span>
            </div>
        </div>

        <!-- Cards Grid -->
        <div class="cards-grid">

            <!-- Current Weight Card -->
            <div class="card card-primary">
                <div class="card-header">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="rgba(255,255,255,0.85)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6.5 6.5h11l-.5 9.5a2 2 0 0 1-2 2H9a2 2 0 0 1-2-2L6.5 6.5z"/><path d="M8 6.5V5a4 4 0 0 1 8 0v1.5"/><circle cx="12" cy="13" r="2"/></svg>
                    <span class="card-label">Berat Saat Ini</span>
                </div>
                <div class="card-body">
                    <div class="weight-display">
                        <span class="weight-value" id="currentWeight">--</span>
                        <span class="weight-unit">kg</span>
                    </div>
                    <p class="card-note">Data real-time dari sensor</p>
                </div>
                <div class="card-footer">
                    <span class="badge badge-waiting">Menunggu data sensor...</span>
                </div>
            </div>

            <!-- BMI Card -->
            <div class="card">
                <div class="card-header">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="var(--tel-red)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
                    <span class="card-label">Indeks Massa Tubuh (BMI)</span>
                </div>
                <div class="card-body">
                    <div class="weight-display">
                        <span class="weight-value" id="bmiValue">--</span>
                        <span class="weight-unit">BMI</span>
                    </div>
                    <div class="input-inline">
                        <label for="inputHeight">Tinggi Badan:</label>
                        <input type="number" id="inputHeight" class="inline-input" value="170" min="100" max="250" step="1">
                        <span class="input-suffix">cm</span>
                    </div>
                </div>
                <div class="card-footer">
                    <span class="badge badge-neutral">Belum tersedia</span>
                </div>
            </div>

            <!-- Target Weight Card -->
            <div class="card">
                <div class="card-header">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="var(--tel-red)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/></svg>
                    <span class="card-label">Target Berat</span>
                </div>
                <div class="card-body">
                    <div class="weight-display">
                        <span class="weight-value" id="targetWeightDisplay">65</span>
                        <span class="weight-unit">kg</span>
                    </div>
                    <div class="input-inline">
                        <label for="inputTarget">Atur Target:</label>
                        <input type="number" id="inputTarget" class="inline-input" value="65" min="20" max="200" step="0.1">
                        <span class="input-suffix">kg</span>
                    </div>
                </div>
                <div class="card-footer">
                    <span class="badge badge-info">Selisih: -- kg</span>
                </div>
            </div>

            <!-- Last Session Card -->
            <div class="card">
                <div class="card-header">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="var(--tel-red)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
                    <span class="card-label">Pengukuran Terakhir</span>
                </div>
                <div class="card-body">
                    <div class="weight-display">
                        <span class="weight-value" id="lastWeight">--</span>
                        <span class="weight-unit">kg</span>
                    </div>
                    <p class="card-note">Belum ada pengukuran sebelumnya</p>
                </div>
                <div class="card-footer">
                    <span class="badge badge-neutral">Belum ada riwayat</span>
                </div>
            </div>

        </div>

        <!-- Live Camera Stream Section -->
        <section class="camera-section" id="liveCameraSection">
            <div class="section-title-row">
                <h2 class="section-title">Live Kamera ESP32</h2>
                <div class="esp-ip-row">
                    <label class="camera-info-label">IP ESP32-CAM:</label>
                    <input type="text" id="esp32IpInput" class="inline-input esp-ip-input" placeholder="cth: 172.19.163.99" value="">
                    <button id="btnConnectEsp" class="btn btn-primary" style="padding: 8px 16px;">Hubungkan</button>
                </div>
            </div>

            <div class="live-camera-card">
                <!-- Stream Preview -->
                <div class="camera-preview" id="livePreview">
                    <div class="camera-placeholder" id="livePlaceholder">
                        <span class="camera-placeholder-icon">📷</span>
                        <p>Masukkan IP ESP32-CAM<br>lalu klik Hubungkan</p>
                    </div>
                    <img id="liveStream" class="camera-img" src="" alt="Live Stream ESP32" style="display:none;">
                </div>

                <!-- Controls & Status -->
                <div class="camera-info">
                    <div class="camera-info-row">
                        <span class="camera-info-label">Status Stream:</span>
                        <span class="badge badge-neutral" id="streamStatus">Tidak terhubung</span>
                    </div>
                    <div class="camera-info-row">
                        <span class="camera-info-label">Alamat Stream:</span>
                        <span class="camera-info-value" id="streamUrl" style="font-size:11px; word-break:break-all;">--</span>
                    </div>
                    <div class="camera-info-row">
                        <span class="camera-info-label">Hasil Capture OCR:</span>
                        <span class="camera-info-value camera-weight" id="liveOcrResult" style="color: var(--grey-400);">-- kg</span>
                    </div>

                    <!-- Action Buttons -->
                    <div class="live-btn-row">
                        <button id="btnLiveCapture" class="btn btn-primary" disabled>
                            📸 Capture
                        </button>
                        <button id="btnLiveFlash" class="btn btn-secondary">
                            💡 Flash
                        </button>
                    </div>

                    <p id="liveCaptureStatus" class="live-status-text"></p>
                </div>
            </div>
        </section>

        <!-- Camera Capture Section -->
        <section class="camera-section">
            <h2 class="section-title">Hasil Capture Kamera</h2>
            <div class="camera-card">
                <div class="camera-preview">
                    <div class="camera-placeholder" id="cameraPlaceholder">
                        <p>Menunggu gambar dari ESP32-CAM...</p>
                    </div>
                    <img id="cameraImage" class="camera-img" src="" alt="Capture timbangan" style="display:none;">
                </div>
                <div class="camera-info">
                    <div class="camera-info-row">
                        <span class="camera-info-label">Status OCR:</span>
                        <span class="badge badge-neutral" id="ocrStatus">Menunggu</span>
                    </div>
                    <div class="camera-info-row">
                        <span class="camera-info-label">Waktu Capture:</span>
                        <span class="camera-info-value" id="captureTime">--</span>
                    </div>
                    <div class="camera-info-row">
                        <span class="camera-info-label">Berat Terbaca OCR:</span>
                        <span class="camera-info-value camera-weight" id="ocrWeight" style="color: var(--grey-400);">-- kg</span>
                    </div>
                    <div class="camera-info-row">
                        <span class="camera-info-label">Berat Final:</span>
                        <span class="camera-info-value camera-weight" id="finalWeight" style="color: var(--tel-red); font-weight: 700;">-- kg</span>
                    </div>

                    <div class="camera-info-row" id="manualInputBlock" style="display: flex; margin-top: 8px; flex-direction: column; align-items: flex-start; border-top: 1px dashed var(--grey-200); padding-top: 16px;">
                        <label class="camera-info-label" style="margin-bottom: 10px; font-weight: 600;">Koreksi / Input Manual Berat:</label>
                        <div style="display: flex; gap: 10px; width: 100%;">
                            <input type="number" id="manualWeightInput" step="0.1" class="inline-input" placeholder="Contoh: 65.5" style="flex: 1; min-width: 0;">
                            <button id="btnSaveManual" class="btn btn-primary" style="padding: 8px 18px; white-space: nowrap;">Simpan</button>
                        </div>
                    </div>

                </div>
            </div>
        </section>

    </main>

    <!-- Footer -->
    <footer class="footer">
        <div class="footer-content">
            <p>&copy; 2026 SmartWeight IoT - Telkom University</p>
            <p class="footer-sub">Proyek Magang - Sistem Monitoring Berat Badan</p>
        </div>
    </footer>

    <script>
        // =============================================
        // SmartWeight IoT - Frontend Logic
        // =============================================

        // Konfigurasi
        const API_URL = 'index.php';       // URL backend (file ini sendiri)
        const FETCH_INTERVAL = 3000;

        // Element references
        const elements = {
            currentWeight: document.getElementById('currentWeight'),
            bmiValue: document.getElementById('bmiValue'),
            lastWeight: document.getElementById('lastWeight'),
            lastUpdate: document.getElementById('lastUpdate'),
            inputHeight: document.getElementById('inputHeight'),
            inputTarget: document.getElementById('inputTarget'),
            targetWeightDisplay: document.getElementById('targetWeightDisplay'),
        };

        // State
        let lastWeightData = null;
        let isConnected = false;
        let lastImagePath = null;
        let currentRecordId = null; 

        // --- Load saved settings dari localStorage ---
        function loadSettings() {
            const savedHeight = localStorage.getItem('sw_height');
            const savedTarget = localStorage.getItem('sw_target');
            if (savedHeight) elements.inputHeight.value = savedHeight;
            if (savedTarget) {
                elements.inputTarget.value = savedTarget;
                elements.targetWeightDisplay.textContent = savedTarget;
            }
        }

        function getUserHeight() {
            return parseFloat(elements.inputHeight.value) || 170;
        }
        function getTargetWeight() {
            return parseFloat(elements.inputTarget.value) || 65;
        }

        elements.inputHeight.addEventListener('input', function () {
            localStorage.setItem('sw_height', this.value);
            if (lastWeightData) fetchLatestWeight();
        });

        elements.inputTarget.addEventListener('input', function () {
            localStorage.setItem('sw_target', this.value);
            elements.targetWeightDisplay.textContent = this.value;
            if (lastWeightData) fetchLatestWeight();
        });

        loadSettings();

        function updateTime() {
            const now = new Date();
            const timeStr = now.toLocaleTimeString('id-ID', {
                hour: '2-digit', minute: '2-digit', second: '2-digit'
            });
            elements.lastUpdate.textContent = timeStr;
        }

        function calculateBMI(weightKg, heightCm) {
            const heightM = heightCm / 100;
            return (weightKg / (heightM * heightM)).toFixed(1);
        }

        function getBMICategory(bmi) {
            if (bmi < 18.5) return { label: 'Berat Kurang', class: 'badge-info' };
            if (bmi < 25) return { label: 'Normal', class: 'badge-success' };
            if (bmi < 30) return { label: 'Berat Lebih', class: 'badge-warning' };
            return { label: 'Obesitas', class: 'badge-danger' };
        }

        function updateConnectionStatus(connected) {
            isConnected = connected;
            const statusDot = document.querySelector('.status-dot');
            const statusText = document.querySelector('.status-item .status-text');

            if (connected) {
                if (statusDot) statusDot.classList.add('online');
                if (statusText) statusText.textContent = 'Sensor Aktif';
            } else {
                if (statusDot) statusDot.classList.remove('online');
                if (statusText) statusText.textContent = 'Tidak terhubung';
            }
        }

        function updateDashboard(data) {
            if (!data || !data.data) {
                elements.currentWeight.textContent = '--';
                elements.bmiValue.textContent = '--';
                return;
            }

            const weight = data.data.weight_kg;
            const timestamp = data.data.created_at;
            currentRecordId = data.data.id;

            elements.currentWeight.textContent = weight.toFixed(2);

            const primaryBadge = document.querySelector('.card-primary .badge');
            if (primaryBadge) {
                primaryBadge.textContent = '✅ Data diterima';
                primaryBadge.className = 'badge badge-success';
            }

            updateCameraImage(data.data);

            const bmi = calculateBMI(weight, getUserHeight());
            elements.bmiValue.textContent = bmi;

            const bmiCategory = getBMICategory(parseFloat(bmi));
            const bmiCard = document.querySelectorAll('.card')[1];
            if (bmiCard) {
                const bmiBadge = bmiCard.querySelector('.badge');
                if (bmiBadge) {
                    bmiBadge.textContent = bmiCategory.label;
                    bmiBadge.className = 'badge ' + bmiCategory.class;
                }
                const bmiNote = bmiCard.querySelector('.card-note');
                if (bmiNote) {
                    bmiNote.textContent = 'Berdasarkan tinggi badan ' + getUserHeight() + ' cm';
                }
            }

            const targetCard = document.querySelectorAll('.card')[2];
            if (targetCard) {
                const targetBadge = targetCard.querySelector('.badge');
                if (targetBadge) {
                    const diff = (weight - getTargetWeight()).toFixed(2);
                    if (diff > 0) {
                        targetBadge.textContent = 'Selisih: +' + diff + ' kg';
                        targetBadge.className = 'badge badge-warning';
                    } else if (diff < 0) {
                        targetBadge.textContent = 'Selisih: ' + diff + ' kg';
                        targetBadge.className = 'badge badge-info';
                    } else {
                        targetBadge.textContent = 'Target tercapai!';
                        targetBadge.className = 'badge badge-success';
                    }
                }
            }
        }

        function updateHistory(historyData) {
            if (!historyData || !historyData.data || historyData.data.length < 2) {
                return;
            }

            const prevMeasurement = historyData.data[1];
            elements.lastWeight.textContent = prevMeasurement.weight_kg.toFixed(2);

            const lastCard = document.querySelectorAll('.card')[3];
            if (lastCard) {
                const note = lastCard.querySelector('.card-note');
                if (note) {
                    const date = new Date(prevMeasurement.created_at);
                    note.textContent = date.toLocaleDateString('id-ID', {
                        day: 'numeric', month: 'long', year: 'numeric',
                        hour: '2-digit', minute: '2-digit'
                    });
                }
            }
        }

        async function fetchLatestWeight() {
            try {
                const response = await fetch(API_URL + '?action=latest');
                const data = await response.json();

                if (data.success) {
                    updateConnectionStatus(true);
                    updateDashboard(data);

                    const historyRes = await fetch(API_URL + '?action=history&limit=2');
                    const historyData = await historyRes.json();
                    if (historyData.success) {
                        updateHistory(historyData);
                    }
                }
            } catch (error) {
                console.error('Gagal mengambil data:', error);
                updateConnectionStatus(false);
            }
        }

        function updateCameraImage(data) {
            const img = document.getElementById('cameraImage');
            const placeholder = document.getElementById('cameraPlaceholder');
            const ocrStatus = document.getElementById('ocrStatus');
            const captureTime = document.getElementById('captureTime');
            const ocrWeight = document.getElementById('ocrWeight');
            const finalWeight = document.getElementById('finalWeight');

            if (data && data.image_url) {
                if (lastImagePath !== data.image_url) {
                    lastImagePath = data.image_url;
                    img.src = data.image_url + '?t=' + Date.now();
                    img.style.display = 'block';
                    placeholder.style.display = 'none';
                }

                ocrStatus.textContent = 'Berhasil';
                ocrStatus.className = 'badge badge-success';
                
                if (data.ocr_weight !== null && data.ocr_weight !== undefined) {
                    ocrWeight.textContent = data.ocr_weight.toFixed(2) + ' kg';
                } else {
                    ocrWeight.textContent = data.weight_kg.toFixed(2) + ' kg';
                }
                
                finalWeight.textContent = data.weight_kg.toFixed(2) + ' kg';

                const date = new Date(data.created_at);
                captureTime.textContent = date.toLocaleDateString('id-ID', {
                    day: 'numeric', month: 'short', year: 'numeric',
                    hour: '2-digit', minute: '2-digit', second: '2-digit'
                });
                
                document.getElementById('manualInputBlock').style.display = 'flex';
            } else if (data && !data.image_url) {
                ocrStatus.textContent = 'Input Manual';
                ocrStatus.className = 'badge badge-neutral';
                ocrWeight.textContent = '-';
                finalWeight.textContent = data.weight_kg.toFixed(2) + ' kg';

                const date = new Date(data.created_at);
                captureTime.textContent = date.toLocaleDateString('id-ID', {
                    day: 'numeric', month: 'short', year: 'numeric',
                    hour: '2-digit', minute: '2-digit', second: '2-digit'
                });
                
                document.getElementById('manualInputBlock').style.display = 'flex';
            } else {
                document.getElementById('manualInputBlock').style.display = 'none';
            }
        }

        // --- Event Listener Koreksi / Input Manual ---
        document.getElementById('btnSaveManual').addEventListener('click', async () => {
            const manualInput = document.getElementById('manualWeightInput').value;
            if (!manualInput) return;

            const newWeight = parseFloat(manualInput);
            if (newWeight < 1 || newWeight > 500) {
                alert('Masukkan berat yang valid (1-500 kg)');
                return;
            }

            try {
                const btn = document.getElementById('btnSaveManual');
                btn.textContent = 'Menyimpan...';
                btn.disabled = true;

                // Selalu INSERT baru agar masuk sebagai data baru di riwayat
                const payload = { weight_kg: newWeight };

                const response = await fetch(API_URL, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });

                const result = await response.json();
                if (result.success) {
                    alert('Data berat berhasil disimpan!');
                    document.getElementById('manualWeightInput').value = '';
                    fetchLatestWeight();
                } else {
                    alert('Gagal menyimpan: ' + result.message);
                }
            } catch (error) {
                console.error('Error saving manual weight:', error);
                alert('Terjadi kesalahan jaringan saat menyimpan.');
            } finally {
                const btn = document.getElementById('btnSaveManual');
                btn.textContent = 'Simpan';
                btn.disabled = false;
            }
        });

        // =============================================
        // Live Streaming ESP32-CAM
        // =============================================
        let esp32Ip = localStorage.getItem('sw_esp32_ip') || '';
        let flashOn = false;

        const liveStream   = document.getElementById('liveStream');
        const livePlaceholder = document.getElementById('livePlaceholder');
        const streamStatus = document.getElementById('streamStatus');
        const streamUrl    = document.getElementById('streamUrl');
        const btnCapture   = document.getElementById('btnLiveCapture');
        const btnFlash     = document.getElementById('btnLiveFlash');
        const captureStatus = document.getElementById('liveCaptureStatus');
        const esp32IpInput = document.getElementById('esp32IpInput');

        if (esp32Ip) {
            esp32IpInput.value = esp32Ip;
            connectStream(esp32Ip);
        }

        function connectStream(ip) {
            if (!ip) return;
            const url = 'http://' + ip + '/mjpeg';
            streamUrl.textContent = url;

            liveStream.src = url;
            liveStream.style.display = 'block';
            livePlaceholder.style.display = 'none';

            liveStream.onload = () => {
                streamStatus.textContent = 'Streaming';
                streamStatus.className = 'badge badge-success';
                btnCapture.disabled = false;
            };

            liveStream.onerror = () => {
                streamStatus.textContent = 'Gagal konek';
                streamStatus.className = 'badge badge-danger';
                liveStream.style.display = 'none';
                livePlaceholder.style.display = 'flex';
                livePlaceholder.innerHTML = '<span class="camera-placeholder-icon">⚠️</span><p>ESP32 tidak ditemukan.<br>Cek IP dan pastikan ESP32 menyala.</p>';
                btnCapture.disabled = true;
            };

            streamStatus.textContent = 'Menghubungkan...';
            streamStatus.className = 'badge badge-warning';
        }

        document.getElementById('btnConnectEsp').addEventListener('click', () => {
            const ip = esp32IpInput.value.trim();
            if (!ip) return;
            esp32Ip = ip;
            localStorage.setItem('sw_esp32_ip', ip);
            connectStream(ip);
        });

        btnCapture.addEventListener('click', async () => {
            if (!esp32Ip) return;
            btnCapture.disabled = true;
            btnCapture.textContent = '⏳ Mengirim...';
            captureStatus.style.color = '#CA8A04';
            captureStatus.textContent = 'Mengirim perintah capture ke ESP32...';

            try {
                const res = await fetch('http://' + esp32Ip + '/capture');
                const data = await res.json();

                if (data.success) {
                    captureStatus.style.color = '#16A34A';
                    captureStatus.textContent = '✅ Capture berhasil! Memuat data...';
                    document.getElementById('liveOcrResult').textContent =
                        data.weight ? data.weight + ' kg' : data.data?.weight_kg ? data.data.weight_kg + ' kg' : '-- kg';
                    document.getElementById('liveOcrResult').style.color = 'var(--tel-red)';
                    setTimeout(fetchLatestWeight, 1000);
                } else {
                    captureStatus.style.color = '#DC2626';
                    captureStatus.textContent = '❌ ' + (data.message || 'Capture gagal');
                }
            } catch (e) {
                captureStatus.style.color = '#DC2626';
                captureStatus.textContent = '❌ Tidak dapat terhubung ke ESP32. Cek IP!';
            }

            btnCapture.disabled = false;
            btnCapture.textContent = '📸 Capture';
        });

        btnFlash.addEventListener('click', async () => {
            if (!esp32Ip) return;
            try {
                const res = await fetch('http://' + esp32Ip + '/flash');
                const data = await res.json();
                flashOn = data.flash;
                btnFlash.textContent = flashOn ? '💡 Flash ON' : '💡 Flash';
                btnFlash.classList.toggle('btn-flash-on', flashOn);
            } catch (e) {
                captureStatus.style.color = '#DC2626';
                captureStatus.textContent = '❌ Gagal toggle flash';
            }
        });

        updateTime();
        setInterval(updateTime, 1000);

        fetchLatestWeight();
        setInterval(fetchLatestWeight, FETCH_INTERVAL);
    </script>

</body>

</html>
