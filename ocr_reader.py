"""
SmartWeight IoT - OCR Reader (EasyOCR + OpenCV Display Detection)
Membaca angka berat dari gambar timbangan digital (7-segment display)

Perbaikan utama dari versi lama:
- OpenCV auto-detect area display (tidak hardcode crop lagi)
- Crop hanya angka utama (buang angka kecil suhu dll)
- Multiple preprocessing untuk handle berbagai kondisi cahaya
- Voting dari beberapa hasil OCR

Usage: python ocr_reader.py <path_ke_gambar>
Output: JSON {"success": true/false, "weight": 65.0}
"""

import sys
import json
import os
import re
import warnings
warnings.filterwarnings("ignore")


def detect_display(img):
    """
    Deteksi area display timbangan otomatis pakai OpenCV.
    Coba deteksi display biru, hijau, atau gelap.
    Returns: list of (label, cropped_img)
    """
    import cv2
    import numpy as np

    h, w = img.shape[:2]
    results = []
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

    # --- Display BIRU (backlit biru) ---
    blue_mask = cv2.inRange(hsv, np.array([90, 30, 30]), np.array([140, 255, 255]))
    blue_mask = cv2.morphologyEx(blue_mask, cv2.MORPH_CLOSE,
                                  cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15)))
    contours, _ = cv2.findContours(blue_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for c in sorted(contours, key=cv2.contourArea, reverse=True)[:1]:
        if cv2.contourArea(c) > (w * h * 0.02):
            x, y, cw, ch = cv2.boundingRect(c)
            if 1.0 < (cw / max(ch, 1)) < 5.0:
                pad = 5
                crop = img[max(0, y - pad):min(h, y + ch + pad),
                           max(0, x - pad):min(w, x + cw + pad)]
                results.append(("blue", crop))

    # --- Display HIJAU ---
    green_mask = cv2.inRange(hsv, np.array([35, 30, 30]), np.array([85, 255, 255]))
    green_mask = cv2.morphologyEx(green_mask, cv2.MORPH_CLOSE,
                                   cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15)))
    contours_g, _ = cv2.findContours(green_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for c in sorted(contours_g, key=cv2.contourArea, reverse=True)[:1]:
        if cv2.contourArea(c) > (w * h * 0.02):
            x, y, cw, ch = cv2.boundingRect(c)
            if 1.0 < (cw / max(ch, 1)) < 5.0:
                pad = 5
                crop = img[max(0, y - pad):min(h, y + ch + pad),
                           max(0, x - pad):min(w, x + cw + pad)]
                results.append(("green", crop))

    # --- Display GELAP (dark rectangle) ---
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, dark_thresh = cv2.threshold(cv2.GaussianBlur(gray, (5, 5), 0),
                                    80, 255, cv2.THRESH_BINARY_INV)
    dark_closed = cv2.morphologyEx(dark_thresh, cv2.MORPH_CLOSE,
                                    cv2.getStructuringElement(cv2.MORPH_RECT, (25, 25)))
    contours_d, _ = cv2.findContours(dark_closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for c in sorted(contours_d, key=cv2.contourArea, reverse=True)[:1]:
        if cv2.contourArea(c) > (w * h * 0.05):
            x, y, cw, ch = cv2.boundingRect(c)
            if 1.2 < (cw / max(ch, 1)) < 5.0:
                pad = 10
                crop = img[max(0, y - pad):min(h, y + ch + pad),
                           max(0, x - pad):min(w, x + cw + pad)]
                results.append(("dark", crop))

    # --- Fallback: center-lower crop ---
    results.append(("fallback", img[int(h * 0.25):int(h * 0.85),
                                    int(w * 0.05):int(w * 0.95)]))

    return results


def make_variants(display_img):
    """
    Buat beberapa versi preprocessing dari display.
    Returns: list of (label, image) untuk dicoba EasyOCR.
    """
    import cv2
    import numpy as np

    results = []
    dh, dw = display_img.shape[:2]
    if dw < 10 or dh < 10:
        return results

    clahe = cv2.createCLAHE(clipLimit=4.0, tileGridSize=(4, 4))
    sharp_kernel = np.array([[-1, -1, -1], [-1, 9, -1], [-1, -1, -1]])

    # Crop variations: full display dan left-only (buang angka kecil di kanan)
    crop_configs = [
        ("full", display_img),
        ("left65", display_img[:, :int(dw * 0.65)]),
    ]

    for clabel, crop in crop_configs:
        ch, cw = crop.shape[:2]
        if cw < 10 or ch < 10:
            continue

        # V1: Original + upscale
        big = cv2.resize(crop, (cw * 2, ch * 2), interpolation=cv2.INTER_CUBIC)
        results.append((f"{clabel}_orig", big))

        # V2: Enhanced contrast + sharpen
        enhanced = cv2.filter2D(big, -1, sharp_kernel)
        lab = cv2.cvtColor(enhanced, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        l = clahe.apply(l)
        enhanced = cv2.cvtColor(cv2.merge([l, a, b]), cv2.COLOR_LAB2BGR)
        results.append((f"{clabel}_enhanced", enhanced))

        # V3: Grayscale + OTSU threshold + upscale
        gray = clahe.apply(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY))
        _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        otsu_big = cv2.resize(otsu, (cw * 3, ch * 3), interpolation=cv2.INTER_CUBIC)
        _, otsu_big = cv2.threshold(otsu_big, 127, 255, cv2.THRESH_BINARY)
        otsu_big = cv2.copyMakeBorder(otsu_big, 15, 15, 15, 15,
                                       cv2.BORDER_CONSTANT, value=0)
        results.append((f"{clabel}_otsu", otsu_big))

        # V4: L-channel threshold (bagus untuk display berwarna)
        l_ch = clahe.apply(cv2.cvtColor(crop, cv2.COLOR_BGR2LAB)[:, :, 0])
        _, l_bin = cv2.threshold(l_ch, 140, 255, cv2.THRESH_BINARY)
        l_big = cv2.resize(l_bin, (cw * 3, ch * 3), interpolation=cv2.INTER_CUBIC)
        _, l_big = cv2.threshold(l_big, 127, 255, cv2.THRESH_BINARY)
        l_big = cv2.copyMakeBorder(l_big, 15, 15, 15, 15,
                                    cv2.BORDER_CONSTANT, value=0)
        results.append((f"{clabel}_lbin", l_big))

        # V5: Inverted (untuk display gelap dgn angka gelap di bg terang)
        inv = cv2.bitwise_not(otsu)
        inv_big = cv2.resize(inv, (cw * 3, ch * 3), interpolation=cv2.INTER_CUBIC)
        _, inv_big = cv2.threshold(inv_big, 127, 255, cv2.THRESH_BINARY)
        inv_big = cv2.copyMakeBorder(inv_big, 15, 15, 15, 15,
                                      cv2.BORDER_CONSTANT, value=0)
        results.append((f"{clabel}_inv", inv_big))

    return results


def parse_weight(text):
    """
    Parse angka berat dari teks OCR.
    Handle kebiasaan 7-segment: O→0, l→1, S→5, dll.
    """
    cleaned = text.strip().replace(',', '.').replace(' ', '')
    # Substitusi karakter mirip 7-segment
    for old, new in [('O', '0'), ('o', '0'), ('l', '1'), ('I', '1'),
                     ('|', '1'), ('S', '5'), ('s', '5'), ('B', '8'),
                     ('b', '8'), ('G', '6'), ('g', '9'), ('q', '9'),
                     ('D', '0'), ('Z', '2'), ('T', '7'), ('A', '4')]:
        cleaned = cleaned.replace(old, new)
    cleaned = re.sub(r'[^\d.]', '', cleaned)

    if not cleaned or len(cleaned) < 3:
        return None

    # Sudah ada titik desimal
    if '.' in cleaned:
        parts = [p for p in cleaned.split('.') if p]
        if len(parts) >= 2:
            for i in range(len(parts) - 1):
                try:
                    val = float(parts[i] + '.' + parts[i + 1][:2])
                    if 20 <= val <= 250:
                        return val
                except ValueError:
                    pass

    # Hanya digit — coba sisipkan titik desimal
    digits = re.sub(r'[^\d]', '', cleaned)
    if not digits or len(digits) < 2:
        return None

    candidates = []

    # Langsung sebagai angka
    try:
        direct = float(digits)
        if 20 <= direct <= 250:
            candidates.append((direct, 5))
    except ValueError:
        pass

    # Sisipkan titik di berbagai posisi
    for i in range(1, min(len(digits), 4)):
        dec = digits[i:i + 2]
        if not dec:
            continue
        try:
            val = float(digits[:i] + '.' + dec)
            if 20 <= val <= 250:
                # XX.XX paling umum untuk berat badan
                priority = {2: 15, 3: 12, 1: 8}.get(i, 3)
                candidates.append((val, priority))
        except ValueError:
            pass

    if candidates:
        candidates.sort(key=lambda x: (-x[1], abs(x[0] - 60)))
        return candidates[0][0]

    return None


def read_weight(image_path):
    """Baca angka berat dari gambar timbangan menggunakan EasyOCR"""
    try:
        import cv2
        import easyocr

        img = cv2.imread(image_path)
        if img is None:
            return {"success": False, "weight": None,
                    "message": "Gagal membaca gambar"}

        # Step 1: Deteksi area display
        displays = detect_display(img)

        # Step 2: Buat preprocessing variants
        all_variants = []
        for dlabel, dimg in displays:
            for vlabel, vimg in make_variants(dimg):
                all_variants.append((f"{dlabel}/{vlabel}", vimg))
        # Tambahkan gambar asli sebagai fallback
        big_orig = cv2.resize(img, (img.shape[1] * 2, img.shape[0] * 2),
                               interpolation=cv2.INTER_CUBIC)
        all_variants.append(("original", big_orig))

        # Step 3: Jalankan EasyOCR
        reader = easyocr.Reader(['en'], gpu=False, verbose=False)

        all_texts = []
        weight_votes = {}  # weight (1 desimal) → list of confidence

        for label, variant in all_variants:
            try:
                results = reader.readtext(
                    variant, detail=1, paragraph=False,
                    allowlist='0123456789.',
                    text_threshold=0.2, low_text=0.2,
                    mag_ratio=1.5,
                )
            except Exception:
                try:
                    results = reader.readtext(variant, detail=1, paragraph=False)
                except Exception:
                    continue

            for (bbox, text, conf) in results:
                text = text.strip()
                if not text or len(text) < 2:
                    continue

                all_texts.append({
                    "source": label, "text": text,
                    "confidence": round(conf, 3)
                })

                w = parse_weight(text)
                if w is not None:
                    key = round(w, 1)
                    weight_votes.setdefault(key, []).append(conf)

        # Step 4: Voting — pilih berat dengan vote terbanyak
        if weight_votes:
            best_w = max(weight_votes,
                         key=lambda k: len(weight_votes[k]) * (
                             sum(weight_votes[k]) / len(weight_votes[k])
                             + 0.05 * len(weight_votes[k])))
            confs = weight_votes[best_w]
            avg_conf = sum(confs) / len(confs)

            return {
                "success": True,
                "weight": best_w,
                "confidence": round(min(avg_conf, 1.0), 3),
                "votes": len(confs),
                "all_candidates": {str(k): len(v)
                                   for k, v in sorted(weight_votes.items(),
                                                      key=lambda x: -len(x[1]))[:10]},
                "all_detected": all_texts[:15]
            }

        # Fallback: gabungkan semua teks per sumber
        by_src = {}
        for item in all_texts:
            src = item["source"].split("/")[0]
            by_src.setdefault(src, "")
            by_src[src] += item["text"]

        for src, combined in by_src.items():
            w = parse_weight(combined)
            if w is not None:
                return {
                    "success": True,
                    "weight": round(w, 2),
                    "confidence": 0.3,
                    "all_detected": all_texts[:15]
                }

        return {
            "success": False, "weight": None,
            "message": "Tidak bisa membaca angka berat dari display",
            "all_detected": all_texts[:15]
        }

    except ImportError:
        return {"success": False, "weight": None,
                "message": "EasyOCR belum terinstall (pip install easyocr)"}
    except Exception as e:
        return {"success": False, "weight": None, "message": str(e)}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"success": False,
                           "message": "Usage: python ocr_reader.py <image_path>"}))
        sys.exit(1)

    image_path = sys.argv[1]
    if not os.path.exists(image_path):
        print(json.dumps({"success": False,
                           "message": f"File tidak ditemukan: {image_path}"}))
        sys.exit(1)

    result = read_weight(image_path)
    print(json.dumps(result))
