"""
SmartWeight IoT - OCR Reader (EasyOCR + OpenCV)
Membaca angka berat dari gambar timbangan digital (7-segment display)

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
    import cv2
    import numpy as np

    h, w = img.shape[:2]
    results = []
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    mean_bright = np.mean(gray)

    if mean_bright < 120:
        results.append(("closeup", img))
        margin_y = int(h * 0.05)
        margin_x = int(w * 0.03)
        cropped = img[margin_y:h-margin_y, margin_x:int(w*0.82)]
        results.append(("closeup_trim", cropped))
        return results

    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
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

    if not results:
        results.append(("full", img))

    return results


def make_variants(display_img):
    import cv2
    import numpy as np

    results = []
    dh, dw = display_img.shape[:2]
    if dw < 10 or dh < 10:
        return results

    clahe = cv2.createCLAHE(clipLimit=4.0, tileGridSize=(4, 4))
    scale = 3

    big = cv2.resize(display_img, (dw * 2, dh * 2), interpolation=cv2.INTER_CUBIC)
    results.append(("color_2x", big))

    gray = cv2.cvtColor(display_img, cv2.COLOR_BGR2GRAY)

    gray_cl = clahe.apply(gray)
    _, otsu = cv2.threshold(gray_cl, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    otsu_big = cv2.resize(otsu, (dw * scale, dh * scale), interpolation=cv2.INTER_CUBIC)
    _, otsu_big = cv2.threshold(otsu_big, 127, 255, cv2.THRESH_BINARY)
    otsu_big = cv2.copyMakeBorder(otsu_big, 15, 15, 15, 15, cv2.BORDER_CONSTANT, value=0)
    results.append(("otsu", otsu_big))

    inv = cv2.bitwise_not(otsu)
    inv_big = cv2.resize(inv, (dw * scale, dh * scale), interpolation=cv2.INTER_CUBIC)
    _, inv_big = cv2.threshold(inv_big, 127, 255, cv2.THRESH_BINARY)
    inv_big = cv2.copyMakeBorder(inv_big, 15, 15, 15, 15, cv2.BORDER_CONSTANT, value=255)
    results.append(("inverted", inv_big))

    adaptive = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                      cv2.THRESH_BINARY, 31, -8)
    adapt_big = cv2.resize(adaptive, (dw * scale, dh * scale), interpolation=cv2.INTER_CUBIC)
    _, adapt_big = cv2.threshold(adapt_big, 127, 255, cv2.THRESH_BINARY)
    adapt_big = cv2.copyMakeBorder(adapt_big, 15, 15, 15, 15, cv2.BORDER_CONSTANT, value=0)
    results.append(("adaptive", adapt_big))

    _, bright = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY)
    bright = cv2.morphologyEx(bright, cv2.MORPH_CLOSE,
                               cv2.getStructuringElement(cv2.MORPH_RECT, (3,3)))
    bright_big = cv2.resize(bright, (dw * scale, dh * scale), interpolation=cv2.INTER_CUBIC)
    _, bright_big = cv2.threshold(bright_big, 127, 255, cv2.THRESH_BINARY)
    bright_big = cv2.copyMakeBorder(bright_big, 15, 15, 15, 15, cv2.BORDER_CONSTANT, value=0)
    results.append(("bright", bright_big))

    return results


def parse_weight(text):
    cleaned = text.strip().replace(',', '.').replace(' ', '')
    for old, new in [('O', '0'), ('o', '0'), ('Q', '0'), ('D', '0'),
                     ('l', '1'), ('I', '1'), ('|', '1'), ('!', '1'),
                     ('Z', '2'), ('z', '2'), ('E', '3'),
                     ('H', '4'), ('h', '4'), ('Y', '4'), ('A', '4'),
                     ('S', '5'), ('s', '5'), ('G', '6'), ('C', '6'),
                     ('T', '7'), ('B', '8'), ('b', '8'),
                     ('g', '9'), ('q', '9'), ('P', '9'),
                     ('U', '0'), ('n', '0')]:
        cleaned = cleaned.replace(old, new)
    cleaned = re.sub(r'[^\d.]', '', cleaned)

    if not cleaned or len(cleaned) < 2:
        return None

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

    digits = re.sub(r'[^\d]', '', cleaned)
    if not digits or len(digits) < 2:
        return None

    candidates = []
    try:
        direct = float(digits)
        if 20 <= direct <= 250:
            candidates.append((direct, 5))
    except ValueError:
        pass

    if len(digits) == 4:
        try:
            val = float(digits[:2] + '.' + digits[2:])
            if 5 <= val <= 250:
                candidates.append((val, 20))
        except ValueError:
            pass

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
    try:
        import cv2
        import easyocr

        img = cv2.imread(image_path)
        if img is None:
            return {"success": False, "weight": None, "message": "Gagal membaca gambar"}

        h, w = img.shape[:2]
        sys.stderr.write("[OCR] Image: %dx%d\n" % (w, h))

        displays = detect_display(img)
        sys.stderr.write("[OCR] Displays: %s\n" % str([d[0] for d in displays]))

        all_variants = []
        for dlabel, dimg in displays:
            for vlabel, vimg in make_variants(dimg):
                all_variants.append(("%s/%s" % (dlabel, vlabel), vimg))

        sys.stderr.write("[OCR] Total variants: %d\n" % len(all_variants))

        reader = easyocr.Reader(['en'], gpu=False, verbose=False)

        best_weight = None
        best_conf = 0.0
        best_source = ""

        for label, variant in all_variants:
            # Early stop jika sudah dapat match bagus
            if best_weight is not None and best_conf > 0.4:
                break

            try:
                results = reader.readtext(
                    variant, detail=1, paragraph=False,
                    allowlist='0123456789.',
                    text_threshold=0.15, low_text=0.15,
                    mag_ratio=1.5,
                )
            except Exception:
                continue

            for (bbox, text, conf) in results:
                text = str(text).strip()
                if not text or len(text) < 2:
                    continue

                w_val = parse_weight(text)
                if w_val is not None and 10 <= w_val <= 200:
                    sys.stderr.write("[OCR] '%s' -> %.2f kg (%s, conf:%.2f)\n" % (text, w_val, label, float(conf)))
                    if float(conf) > best_conf:
                        best_weight = float(round(w_val, 2))
                        best_conf = float(conf)
                        best_source = label

        if best_weight is not None:
            result = {
                "success": True,
                "weight": best_weight,
                "confidence": round(best_conf, 3),
                "source": best_source
            }
            sys.stderr.write("[OCR] RESULT: %.2f kg (conf:%.3f)\n" % (best_weight, best_conf))
            return result

        return {"success": False, "weight": None, "message": "Tidak bisa membaca angka berat"}

    except Exception as e:
        return {"success": False, "weight": None, "message": str(e)}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        r = {"success": False, "message": "Usage: python ocr_reader.py <image>"}
    elif not os.path.exists(sys.argv[1]):
        r = {"success": False, "message": "File tidak ditemukan: " + sys.argv[1]}
    else:
        r = read_weight(sys.argv[1])

    # Output JSON - tulis ke stderr DAN stdout
    out = json.dumps(r)
    sys.stderr.write("[RESULT] " + out + "\n")
    sys.stderr.flush()
    sys.stdout.write(out + "\n")
    sys.stdout.flush()
