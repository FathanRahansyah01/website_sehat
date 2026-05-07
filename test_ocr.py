#!/usr/bin/env python3
"""
Test script: cek apakah semua dependency OCR tersedia + test baca gambar
Jalankan di dalam Docker container:
  docker compose exec web python /var/www/html/test_ocr.py
"""
import sys
import json

print("=== OCR Dependency Check ===")

# 1. Python version
print(f"Python: {sys.version}")

# 2. OpenCV
try:
    import cv2
    print(f"✅ OpenCV: {cv2.__version__}")
except ImportError as e:
    print(f"❌ OpenCV GAGAL: {e}")

# 3. EasyOCR
try:
    import easyocr
    print(f"✅ EasyOCR: tersedia")
except ImportError as e:
    print(f"❌ EasyOCR GAGAL: {e}")

# 4. PIL
try:
    from PIL import Image
    print(f"✅ Pillow: tersedia")
except ImportError as e:
    print(f"❌ Pillow GAGAL: {e}")

# 5. NumPy
try:
    import numpy as np
    print(f"✅ NumPy: {np.__version__}")
except ImportError as e:
    print(f"❌ NumPy GAGAL: {e}")

# 6. Test baca gambar terbaru di uploads
import os
import glob

uploads_dir = "/var/www/html/uploads"
if os.path.isdir(uploads_dir):
    images = sorted(glob.glob(os.path.join(uploads_dir, "weight_*.jpg")))
    if images:
        latest = images[-1]
        print(f"\n=== Test OCR pada: {os.path.basename(latest)} ===")
        
        img = cv2.imread(latest)
        if img is not None:
            h, w = img.shape[:2]
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            mean_b = np.mean(gray)
            print(f"Image size: {w}x{h}")
            print(f"Mean brightness: {mean_b:.1f}")
            
            # Test display detection
            from ocr_reader import detect_display, make_variants
            displays = detect_display(img)
            print(f"Display regions: {len(displays)} -> {[d[0] for d in displays]}")
            for label, dimg in displays:
                dh, dw = dimg.shape[:2]
                print(f"  - {label}: {dw}x{dh}")
            
            # Test variants
            total_variants = 0
            for dlabel, dimg in displays:
                variants = make_variants(dimg)
                total_variants += len(variants)
            print(f"Total variants: {total_variants}")
            
            # Test full OCR
            print(f"\n--- Running OCR (ini bisa 20-60 detik) ---")
            from ocr_reader import read_weight
            result = read_weight(latest)
            print(f"\nOCR Result: {json.dumps(result, indent=2)}")
        else:
            print(f"❌ Gagal baca gambar: {latest}")
    else:
        print("Tidak ada gambar di uploads/")
else:
    print(f"Folder {uploads_dir} tidak ditemukan")
    # Coba folder lokal
    local_uploads = os.path.join(os.path.dirname(__file__), "uploads")
    if os.path.isdir(local_uploads):
        images = sorted(glob.glob(os.path.join(local_uploads, "weight_*.jpg")))
        if images:
            latest = images[-1]
            print(f"\n=== Test OCR lokal: {os.path.basename(latest)} ===")
            from ocr_reader import read_weight
            result = read_weight(latest)
            print(f"OCR Result: {json.dumps(result, indent=2)}")
