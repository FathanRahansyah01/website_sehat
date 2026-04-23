"""
SmartWeight IoT - OCR Reader (EasyOCR)
Membaca angka berat dari gambar timbangan digital (7-segment display)
Usage: python ocr_reader.py <path_ke_gambar>
Output: JSON {"success": true/false, "weight": 65.0}
"""

import sys
import json
import os
import re


def preprocess_variants(image_path):
    """
    Buat beberapa versi preprocessing dari gambar.
    Return list of (label, path) untuk dicoba EasyOCR.
    """
    try:
        from PIL import Image, ImageEnhance, ImageOps, ImageFilter

        img = Image.open(image_path)
        w, h = img.size
        results = []

        # --- Variant 1: Original (tidak diubah) ---
        results.append(("original", image_path))

        # --- Variant 2: Crop area display + high contrast ---
        crop = img.crop((int(w * 0.05), int(h * 0.15), int(w * 0.95), int(h * 0.65)))
        crop = ImageEnhance.Contrast(crop).enhance(3.0)
        crop = ImageEnhance.Sharpness(crop).enhance(2.0)
        crop = ImageEnhance.Brightness(crop).enhance(1.3)
        p2 = image_path.rsplit('.', 1)[0] + '_crop_enhance.png'
        crop.save(p2)
        results.append(("crop_enhance", p2))

        # --- Variant 3: Grayscale + adaptive threshold ---
        gray = ImageOps.grayscale(crop)
        # Threshold rendah (untuk display gelap)
        thresh_low = gray.point(lambda x: 255 if x > 80 else 0, 'L')
        p3 = image_path.rsplit('.', 1)[0] + '_thresh_low.png'
        thresh_low.save(p3)
        results.append(("thresh_low", p3))

        # --- Variant 4: Threshold tinggi (untuk display terang) ---
        thresh_high = gray.point(lambda x: 255 if x > 140 else 0, 'L')
        p4 = image_path.rsplit('.', 1)[0] + '_thresh_high.png'
        thresh_high.save(p4)
        results.append(("thresh_high", p4))

        # --- Variant 5: Invert (angka putih di background hitam) ---
        inverted = ImageOps.invert(thresh_low)
        p5 = image_path.rsplit('.', 1)[0] + '_inverted.png'
        inverted.save(p5)
        results.append(("inverted", p5))

        # --- Variant 6: Sharpen + resize 2x (untuk angka kecil) ---
        big = crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS)
        big = big.filter(ImageFilter.SHARPEN)
        big = ImageEnhance.Contrast(big).enhance(2.0)
        p6 = image_path.rsplit('.', 1)[0] + '_upscale.png'
        big.save(p6)
        results.append(("upscale", p6))

        return results

    except Exception as e:
        print(json.dumps({"debug": f"Preprocessing error: {e}"}), file=sys.stderr)
        return [("original", image_path)]


def parse_weight_from_text(text):
    """
    Parse berat dari teks OCR.
    Handle 7-segment quirks: titik desimal hilang, 7→1, 0→O, dll.
    """
    # Bersihkan teks
    cleaned = text.strip()
    cleaned = cleaned.replace(',', '.').replace(' ', '')
    cleaned = cleaned.replace('O', '0').replace('o', '0')
    cleaned = cleaned.replace('l', '1').replace('I', '1')
    cleaned = cleaned.replace('S', '5').replace('s', '5')
    cleaned = cleaned.replace('B', '8').replace('b', '8')  # fix: b mirip 8 bukan 6
    cleaned = cleaned.replace('g', '9').replace('q', '9')
    cleaned = re.sub(r'[^\d.]', '', cleaned)

    if not cleaned or len(cleaned) < 2:
        return None

    # Kalau sudah ada titik desimal yang valid
    if '.' in cleaned:
        parts = cleaned.split('.')
        # Filter out noise: hanya ambil yang masuk akal
        for i in range(len(parts) - 1):
            try:
                val = float(parts[i] + '.' + parts[i + 1])
                if 5 <= val <= 250:
                    return val
            except ValueError:
                pass

    # Hanya digit, coba berbagai posisi desimal
    digits_only = re.sub(r'[^\d]', '', cleaned)
    if not digits_only:
        return None

    candidates = []

    # Angka langsung (misal "65" → 65.0)
    try:
        direct = float(digits_only)
        if 5 <= direct <= 250:
            candidates.append((direct, 10))  # (value, priority)
    except ValueError:
        pass

    # Coba sisipkan titik desimal
    for i in range(1, len(digits_only)):
        try:
            val = float(digits_only[:i] + '.' + digits_only[i:])
            if 5 <= val <= 250:
                # Prioritas: 2 digit sebelum desimal (XX.X atau XX.XX)
                priority = 5 if i in (2, 3) else 3
                candidates.append((val, priority))
        except ValueError:
            pass

    if candidates:
        # Sort by priority (highest first), lalu by value paling "normal" (40-120 kg)
        candidates.sort(key=lambda x: (-x[1], abs(x[0] - 70)))
        return candidates[0][0]

    return None


def read_weight(image_path):
    """Baca angka berat dari gambar timbangan menggunakan EasyOCR"""
    try:
        import easyocr

        # Buat preprocessing variants
        variants = preprocess_variants(image_path)

        # Inisialisasi reader
        reader = easyocr.Reader(['en'], gpu=False, verbose=False)

        all_texts = []
        best_weight = None
        best_confidence = 0

        for label, img_path in variants:
            try:
                results = reader.readtext(
                    img_path,
                    detail=1,
                    paragraph=False,
                    allowlist='0123456789.',  # Hanya angka dan titik
                    text_threshold=0.3,       # Lebih sensitif
                    low_text=0.3,
                )
            except Exception:
                # Fallback tanpa allowlist (beberapa versi EasyOCR beda)
                results = reader.readtext(img_path, detail=1, paragraph=False)

            for (bbox, text, confidence) in results:
                all_texts.append({
                    "source": label,
                    "text": text,
                    "confidence": round(confidence, 3)
                })

                weight = parse_weight_from_text(text)
                if weight is not None:
                    # Pilih yang confidence lebih tinggi
                    if best_weight is None or confidence > best_confidence:
                        best_weight = weight
                        best_confidence = confidence

            # Kalau sudah dapat dengan confidence tinggi, stop
            if best_weight is not None and best_confidence > 0.5:
                break

        # Kalau belum dapat, coba gabungkan semua teks dari tiap source
        if best_weight is None:
            by_source = {}
            for item in all_texts:
                src = item["source"]
                if src not in by_source:
                    by_source[src] = ""
                by_source[src] += re.sub(r'[^\d.]', '', item["text"])

            for src, combined in by_source.items():
                weight = parse_weight_from_text(combined)
                if weight is not None:
                    best_weight = weight
                    break

        if best_weight is not None:
            return {
                "success": True,
                "weight": round(best_weight, 1),
                "confidence": round(best_confidence, 3),
                "all_detected": all_texts
            }
        else:
            return {
                "success": False,
                "weight": None,
                "message": "Tidak bisa membaca angka berat (20-300 kg)",
                "all_detected": all_texts
            }

    except ImportError:
        return {"success": False, "weight": None, "message": "EasyOCR belum terinstall"}
    except Exception as e:
        return {"success": False, "weight": None, "message": str(e)}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"success": False, "message": "Usage: python ocr_reader.py <image_path>"}))
        sys.exit(1)

    image_path = sys.argv[1]

    if not os.path.exists(image_path):
        print(json.dumps({"success": False, "message": f"File tidak ditemukan: {image_path}"}))
        sys.exit(1)

    result = read_weight(image_path)
    print(json.dumps(result))
