"""
SmartWeight IoT - OCR Reader (EasyOCR + OpenCV)
Membaca angka berat dari gambar timbangan digital (7-segment display)

Strategi: 
- Deteksi display → crop → 4-5 preprocessing → EasyOCR → voting
- Maksimal ~15 variant supaya cepat (< 30 detik)

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
    Deteksi area display timbangan. Return list of (label, cropped_img).
    Hanya return 2-3 crop terbaik supaya cepat.
    """
    import cv2
    import numpy as np

    h, w = img.shape[:2]
    results = []
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    mean_bright = np.mean(gray)

    # --- Strategi 1: Gambar sudah close-up display (>50% area gelap, angka terang) ---
    if mean_bright < 120:
        # Langsung pakai full image sebagai display
        results.append(("closeup", img))
        
        # Juga crop sedikit border hitam
        margin_y = int(h * 0.05)
        margin_x = int(w * 0.03)
        cropped = img[margin_y:h-margin_y, margin_x:int(w*0.82)]
        results.append(("closeup_trim", cropped))
        return results

    # --- Strategi 2: Cari area display dari gambar lebih luas ---
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    
    # Display biru
    blue_mask = cv2.inRange(hsv, np.array([90, 30, 30]), np.array([140, 255, 255]))
    blue_mask = cv2.morphologyEx(blue_mask, cv2.MORPH_CLOSE,
                                  cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15)))
    contours, _ = cv2.findContours(blue_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for c in sorted(contours, key=cv2.contourArea, reverse=True)[:1]:
        if cv2.contourArea(c) > (w * h * 0.02):
            x, y, cw, ch = cv2.boundingRect(c)
            pad = 5
            crop = img[max(0,y-pad):min(h,y+ch+pad), max(0,x-pad):min(w,x+cw+pad)]
            results.append(("blue", crop))

    # Display gelap (dark rectangle)
    _, dark_thresh = cv2.threshold(cv2.GaussianBlur(gray, (5,5), 0), 80, 255, cv2.THRESH_BINARY_INV)
    dark_closed = cv2.morphologyEx(dark_thresh, cv2.MORPH_CLOSE,
                                    cv2.getStructuringElement(cv2.MORPH_RECT, (25, 25)))
    contours_d, _ = cv2.findContours(dark_closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for c in sorted(contours_d, key=cv2.contourArea, reverse=True)[:1]:
        if cv2.contourArea(c) > (w * h * 0.05):
            x, y, cw, ch = cv2.boundingRect(c)
            if 1.2 < (cw / max(ch, 1)) < 5.0:
                pad = 10
                crop = img[max(0,y-pad):min(h,y+ch+pad), max(0,x-pad):min(w,x+cw+pad)]
                results.append(("dark", crop))

    # Fallback: full image
    if not results:
        results.append(("full", img))

    return results


def make_variants(display_img):
    """
    Buat 5 versi preprocessing saja (cukup untuk voting, tetap cepat).
    """
    import cv2
    import numpy as np

    results = []
    dh, dw = display_img.shape[:2]
    if dw < 10 or dh < 10:
        return results

    clahe = cv2.createCLAHE(clipLimit=4.0, tileGridSize=(4, 4))
    scale = 3  # Upscale 3x

    # V1: Original upscaled (color)
    big = cv2.resize(display_img, (dw * 2, dh * 2), interpolation=cv2.INTER_CUBIC)
    results.append(("color_2x", big))

    gray = cv2.cvtColor(display_img, cv2.COLOR_BGR2GRAY)

    # V2: OTSU threshold (paling umum berhasil)
    gray_cl = clahe.apply(gray)
    _, otsu = cv2.threshold(gray_cl, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    otsu_big = cv2.resize(otsu, (dw * scale, dh * scale), interpolation=cv2.INTER_CUBIC)
    _, otsu_big = cv2.threshold(otsu_big, 127, 255, cv2.THRESH_BINARY)
    otsu_big = cv2.copyMakeBorder(otsu_big, 15, 15, 15, 15, cv2.BORDER_CONSTANT, value=0)
    results.append(("otsu", otsu_big))

    # V3: Inverted OTSU (kadang EasyOCR lebih suka teks gelap di bg terang)
    inv = cv2.bitwise_not(otsu)
    inv_big = cv2.resize(inv, (dw * scale, dh * scale), interpolation=cv2.INTER_CUBIC)
    _, inv_big = cv2.threshold(inv_big, 127, 255, cv2.THRESH_BINARY)
    inv_big = cv2.copyMakeBorder(inv_big, 15, 15, 15, 15, cv2.BORDER_CONSTANT, value=255)
    results.append(("inverted", inv_big))

    # V4: Adaptive threshold
    adaptive = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                      cv2.THRESH_BINARY, 31, -8)
    adapt_big = cv2.resize(adaptive, (dw * scale, dh * scale), interpolation=cv2.INTER_CUBIC)
    _, adapt_big = cv2.threshold(adapt_big, 127, 255, cv2.THRESH_BINARY)
    adapt_big = cv2.copyMakeBorder(adapt_big, 15, 15, 15, 15, cv2.BORDER_CONSTANT, value=0)
    results.append(("adaptive", adapt_big))

    # V5: High brightness threshold (khusus angka putih terang)
    _, bright = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY)
    # Morphological cleanup
    bright = cv2.morphologyEx(bright, cv2.MORPH_CLOSE, 
                               cv2.getStructuringElement(cv2.MORPH_RECT, (3,3)))
    bright_big = cv2.resize(bright, (dw * scale, dh * scale), interpolation=cv2.INTER_CUBIC)
    _, bright_big = cv2.threshold(bright_big, 127, 255, cv2.THRESH_BINARY)
    bright_big = cv2.copyMakeBorder(bright_big, 15, 15, 15, 15, cv2.BORDER_CONSTANT, value=0)
    results.append(("bright", bright_big))

    return results


def parse_weight(text):
    """
    Parse angka berat dari teks OCR.
    Handle kebiasaan 7-segment: O→0, l→1, S→5, dll.
    """
    cleaned = text.strip().replace(',', '.').replace(' ', '')
    
    # Substitusi karakter mirip 7-segment
    for old, new in [('O', '0'), ('o', '0'), ('Q', '0'), ('D', '0'),
                     ('l', '1'), ('I', '1'), ('|', '1'), ('!', '1'),
                     ('Z', '2'), ('z', '2'),
                     ('E', '3'),
                     ('H', '4'), ('h', '4'), ('Y', '4'), ('A', '4'),
                     ('S', '5'), ('s', '5'),
                     ('G', '6'), ('C', '6'),
                     ('T', '7'),
                     ('B', '8'), ('b', '8'),
                     ('g', '9'), ('q', '9'), ('P', '9'),
                     ('U', '0'), ('n', '0')]:
        cleaned = cleaned.replace(old, new)
    
    cleaned = re.sub(r'[^\d.]', '', cleaned)

    if not cleaned or len(cleaned) < 2:
        return None

    # Sudah ada titik desimal
    if '.' in cleaned:
        parts = [p for p in cleaned.split('.') if p]
        if len(parts) >= 2:
            for i in range(len(parts) - 1):
                try:
                    val = float(parts[i] + '.' + parts[i + 1][:2])
                    if 5 <= val <= 250:
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

    # Khusus 4 digit: paling umum format XX.XX (berat badan)
    if len(digits) == 4:
        try:
            val = float(digits[:2] + '.' + digits[2:])
            if 5 <= val <= 250:
                candidates.append((val, 20))  # Prioritas tertinggi
        except ValueError:
            pass

    # Sisipkan titik di berbagai posisi
    for i in range(1, min(len(digits), 4)):
        dec = digits[i:i + 2]
        if not dec:
            continue
        try:
            val = float(digits[:i] + '.' + dec)
            if 5 <= val <= 250:
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

        h, w = img.shape[:2]
        sys.stderr.write(f"[OCR] Image: {w}x{h}\n")

        # Step 1: Deteksi area display (max 2-3 region)
        displays = detect_display(img)
        sys.stderr.write(f"[OCR] Displays: {[d[0] for d in displays]}\n")

        # Step 2: Buat preprocessing variants (5 per display = ~10-15 total)
        all_variants = []
        for dlabel, dimg in displays:
            for vlabel, vimg in make_variants(dimg):
                all_variants.append((f"{dlabel}/{vlabel}", vimg))

        sys.stderr.write(f"[OCR] Total variants: {len(all_variants)}\n")

        # Step 3: Jalankan EasyOCR
        reader = easyocr.Reader(['en'], gpu=False, verbose=False)

        all_texts = []
        weight_votes = {}

        for label, variant in all_variants:
            try:
                results = reader.readtext(
                    variant, detail=1, paragraph=False,
                    allowlist='0123456789.',
                    text_threshold=0.15, low_text=0.15,
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

                w_val = parse_weight(text)
                if w_val is not None:
                    key = round(w_val, 1)
                    weight_votes.setdefault(key, []).append(conf)
                    sys.stderr.write(f"[OCR] '{text}' -> {w_val} kg ({label}, conf:{conf:.2f})\n")

        # Step 4: Voting — pilih berat dengan vote terbanyak × confidence
        if weight_votes:
            best_w = max(weight_votes,
                         key=lambda k: len(weight_votes[k]) * (
                             sum(weight_votes[k]) / len(weight_votes[k])
                             + 0.05 * len(weight_votes[k])))
            confs = weight_votes[best_w]
            avg_conf = sum(confs) / len(confs)

            sys.stderr.write(f"[OCR] RESULT: {best_w} kg (votes:{len(confs)}, conf:{avg_conf:.2f})\n")

            return {
                "success": True,
                "weight": best_w,
                "confidence": round(min(avg_conf, 1.0), 3),
                "votes": len(confs),
                "all_candidates": {str(k): len(v)
                                   for k, v in sorted(weight_votes.items(),
                                                      key=lambda x: -len(x[1]))[:5]},
                "all_detected": all_texts[:10]
            }

        # Fallback: gabungkan teks per sumber
        by_src = {}
        for item in all_texts:
            src = item["source"].split("/")[0]
            by_src.setdefault(src, "")
            by_src[src] += item["text"]

        for src, combined in by_src.items():
            w_val = parse_weight(combined)
            if w_val is not None:
                return {
                    "success": True,
                    "weight": round(w_val, 2),
                    "confidence": 0.3,
                    "all_detected": all_texts[:10]
                }

        return {
            "success": False, "weight": None,
            "message": "Tidak bisa membaca angka berat dari display",
            "all_detected": all_texts[:10]
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
