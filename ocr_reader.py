"""
SmartWeight IoT - OCR Reader v3 (EasyOCR + OpenCV)
Optimized for: 7-segment white-on-dark display, ESP32-CAM 640x480, CPU-only Docker

Strategy:
1. Detect display area (dark region with bright digits)
2. Generate 8 preprocessing variants tuned for 7-segment
3. Run EasyOCR on each variant (with early stop on high confidence)
4. Parse + vote for best weight reading
5. Save debug images to /tmp/ocr_debug/ for troubleshooting

Usage: python ocr_reader.py <image_path> [--debug]
Output: JSON on stdout, debug logs on stderr
"""

import sys
import json
import os
import re
import warnings
warnings.filterwarnings("ignore")

DEBUG_DIR = "/tmp/ocr_debug"


def log(msg):
    """Write debug message to stderr"""
    sys.stderr.write("[OCR] %s\n" % msg)
    sys.stderr.flush()


def save_debug(name, img):
    """Save debug image if debug dir exists"""
    try:
        import cv2
        os.makedirs(DEBUG_DIR, exist_ok=True)
        path = os.path.join(DEBUG_DIR, "%s.jpg" % name)
        cv2.imwrite(path, img)
    except Exception:
        pass


def detect_display(img):
    """
    Deteksi area display timbangan dari gambar ESP32-CAM.
    Return list of (label, cropped_img).
    """
    import cv2
    import numpy as np

    h, w = img.shape[:2]
    results = []
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    mean_bright = float(np.mean(gray))
    log("Mean brightness: %.1f" % mean_bright)

    # --- Case 1: Gambar close-up display (mayoritas gelap) ---
    if mean_bright < 130:
        results.append(("full", img))
        # Crop pinggir sedikit (kadang ada border hitam)
        my = int(h * 0.08)
        mx = int(w * 0.05)
        trimmed = img[my:h-my, mx:int(w*0.85)]
        if trimmed.shape[0] > 20 and trimmed.shape[1] > 20:
            results.append(("trimmed", trimmed))
        return results

    # --- Case 2: Gambar lebih luas, cari display ---
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

    # Cari area gelap besar (display background)
    blurred = cv2.GaussianBlur(gray, (7, 7), 0)
    _, dark_mask = cv2.threshold(blurred, 90, 255, cv2.THRESH_BINARY_INV)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (30, 15))
    dark_mask = cv2.morphologyEx(dark_mask, cv2.MORPH_CLOSE, kernel)

    contours, _ = cv2.findContours(dark_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for c in sorted(contours, key=cv2.contourArea, reverse=True)[:2]:
        area = cv2.contourArea(c)
        if area > (w * h * 0.03):
            x, y, cw, ch = cv2.boundingRect(c)
            ratio = cw / max(ch, 1)
            if 1.0 < ratio < 6.0 and ch > 30:
                pad = 15
                crop = img[max(0,y-pad):min(h,y+ch+pad), max(0,x-pad):min(w,x+cw+pad)]
                results.append(("display", crop))
                log("Found display: %dx%d at (%d,%d), ratio=%.1f" % (cw, ch, x, y, ratio))

    # Cari area biru (beberapa timbangan punya backlight biru)
    blue_mask = cv2.inRange(hsv, np.array([90, 40, 40]), np.array([135, 255, 255]))
    blue_mask = cv2.morphologyEx(blue_mask, cv2.MORPH_CLOSE,
                                  cv2.getStructuringElement(cv2.MORPH_RECT, (20, 10)))
    contours_b, _ = cv2.findContours(blue_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for c in sorted(contours_b, key=cv2.contourArea, reverse=True)[:1]:
        if cv2.contourArea(c) > (w * h * 0.02):
            x, y, cw, ch = cv2.boundingRect(c)
            pad = 5
            crop = img[max(0,y-pad):min(h,y+ch+pad), max(0,x-pad):min(w,x+cw+pad)]
            results.append(("blue", crop))

    # Fallback: full image + center crop
    if not results:
        results.append(("full", img))
        # Center 70% crop
        cy1 = int(h * 0.15)
        cy2 = int(h * 0.85)
        cx1 = int(w * 0.1)
        cx2 = int(w * 0.9)
        results.append(("center", img[cy1:cy2, cx1:cx2]))

    return results


def make_variants(display_img, label=""):
    """
    Generate preprocessing variants optimized for 7-segment display.
    Focus on: white digits on dark background, various contrast conditions.
    Returns max 8 variants per display.
    """
    import cv2
    import numpy as np

    variants = []
    dh, dw = display_img.shape[:2]
    if dw < 20 or dh < 20:
        return variants

    # Target size: 3x upscale for better OCR
    tw = min(dw * 3, 1920)
    th = min(dh * 3, 1080)

    gray = cv2.cvtColor(display_img, cv2.COLOR_BGR2GRAY)

    # --- V1: Color upscaled (EasyOCR sometimes works better on color) ---
    color_up = cv2.resize(display_img, (tw, th), interpolation=cv2.INTER_CUBIC)
    variants.append(("color", color_up))
    save_debug("%s_v1_color" % label, color_up)

    # --- V2: CLAHE + OTSU (best for uneven lighting) ---
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(4, 4))
    enhanced = clahe.apply(gray)
    _, otsu = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    otsu_up = cv2.resize(otsu, (tw, th), interpolation=cv2.INTER_NEAREST)
    otsu_up = cv2.copyMakeBorder(otsu_up, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=0)
    variants.append(("otsu", otsu_up))
    save_debug("%s_v2_otsu" % label, otsu_up)

    # --- V3: Inverted OTSU (dark text on white bg - EasyOCR prefers this) ---
    inv_otsu = cv2.bitwise_not(otsu)
    inv_up = cv2.resize(inv_otsu, (tw, th), interpolation=cv2.INTER_NEAREST)
    inv_up = cv2.copyMakeBorder(inv_up, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
    variants.append(("inverted", inv_up))
    save_debug("%s_v3_inverted" % label, inv_up)

    # --- V4: High threshold (isolate bright digits from dark background) ---
    mean_val = float(np.mean(gray))
    thresh_val = max(int(mean_val + 40), 120)
    _, bright = cv2.threshold(gray, thresh_val, 255, cv2.THRESH_BINARY)
    # Clean up noise
    bright = cv2.morphologyEx(bright, cv2.MORPH_OPEN,
                               cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2)))
    bright = cv2.morphologyEx(bright, cv2.MORPH_CLOSE,
                               cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))
    bright_up = cv2.resize(bright, (tw, th), interpolation=cv2.INTER_NEAREST)
    bright_up = cv2.copyMakeBorder(bright_up, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=0)
    variants.append(("bright", bright_up))
    save_debug("%s_v4_bright" % label, bright_up)

    # --- V5: Inverted bright (dark digits on white - another EasyOCR preference) ---
    inv_bright = cv2.bitwise_not(bright)
    inv_bright_up = cv2.resize(inv_bright, (tw, th), interpolation=cv2.INTER_NEAREST)
    inv_bright_up = cv2.copyMakeBorder(inv_bright_up, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
    variants.append(("inv_bright", inv_bright_up))
    save_debug("%s_v5_inv_bright" % label, inv_bright_up)

    # --- V6: Adaptive threshold (handles gradients/shadows) ---
    adaptive = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                      cv2.THRESH_BINARY, 25, -5)
    adaptive = cv2.morphologyEx(adaptive, cv2.MORPH_CLOSE,
                                 cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))
    adap_up = cv2.resize(adaptive, (tw, th), interpolation=cv2.INTER_NEAREST)
    adap_up = cv2.copyMakeBorder(adap_up, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=0)
    variants.append(("adaptive", adap_up))
    save_debug("%s_v6_adaptive" % label, adap_up)

    # --- V7: Strong CLAHE on grayscale (maximum contrast) ---
    strong_clahe = cv2.createCLAHE(clipLimit=8.0, tileGridSize=(3, 3))
    strong = strong_clahe.apply(gray)
    strong_up = cv2.resize(strong, (tw, th), interpolation=cv2.INTER_CUBIC)
    strong_up = cv2.copyMakeBorder(strong_up, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=0)
    variants.append(("clahe_strong", strong_up))
    save_debug("%s_v7_clahe" % label, strong_up)

    # --- V8: Morphological gradient (edge-based, good for broken segments) ---
    morph_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    gradient = cv2.morphologyEx(gray, cv2.MORPH_GRADIENT, morph_kernel)
    _, grad_thresh = cv2.threshold(gradient, 30, 255, cv2.THRESH_BINARY)
    # Dilate to connect broken segments
    grad_thresh = cv2.dilate(grad_thresh, cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2)))
    grad_up = cv2.resize(grad_thresh, (tw, th), interpolation=cv2.INTER_NEAREST)
    grad_up = cv2.copyMakeBorder(grad_up, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=0)
    variants.append(("gradient", grad_up))
    save_debug("%s_v8_gradient" % label, grad_up)

    return variants


def parse_weight(text):
    """
    Parse weight from OCR text. Handle 7-segment misreads.
    Returns float weight in kg, or None if invalid.
    """
    if not text:
        return None

    cleaned = text.strip()

    # Remove common non-digit suffixes (kg, lb, etc)
    cleaned = re.sub(r'[kK][gG]$', '', cleaned)
    cleaned = re.sub(r'[lL][bB]$', '', cleaned)
    cleaned = cleaned.strip()

    # Replace comma with dot
    cleaned = cleaned.replace(',', '.')

    # 7-segment character substitutions
    subs = {
        'O': '0', 'o': '0', 'Q': '0', 'D': '0', 'U': '0',
        'l': '1', 'I': '1', '|': '1', '!': '1', 'i': '1', 'J': '1',
        'Z': '2', 'z': '2', 'R': '2',
        'E': '3', 'e': '3',
        'H': '4', 'h': '4', 'Y': '4', 'A': '4', 'y': '4',
        'S': '5', 's': '5',
        'G': '6', 'C': '6', 'c': '6',
        'T': '7', 't': '7',
        'B': '8', 'b': '8',
        'g': '9', 'q': '9', 'P': '9', 'p': '9',
        'n': '0', 'N': '0',
    }
    for old, new in subs.items():
        cleaned = cleaned.replace(old, new)

    # Remove anything that's not digit or dot
    cleaned = re.sub(r'[^\d.]', '', cleaned)

    # Remove multiple dots, keep first one
    if cleaned.count('.') > 1:
        first_dot = cleaned.index('.')
        cleaned = cleaned[:first_dot+1] + cleaned[first_dot+1:].replace('.', '')

    # Remove leading zeros but keep "0.xx"
    cleaned = cleaned.lstrip('0') or '0'
    if cleaned.startswith('.'):
        cleaned = '0' + cleaned

    if not cleaned or len(cleaned) < 2:
        return None

    # --- Try direct parse ---
    if '.' in cleaned:
        try:
            val = float(cleaned)
            if 3.0 <= val <= 300.0:
                return round(val, 2)
        except ValueError:
            pass

    # --- Try inserting decimal point ---
    digits = re.sub(r'[^\d]', '', cleaned)
    if not digits or len(digits) < 2:
        return None

    candidates = []

    # 2 digits: XX.0 (e.g., "65" -> 65.0)
    if len(digits) == 2:
        val = float(digits)
        if 10 <= val <= 250:
            candidates.append((val, 10))

    # 3 digits: X.XX or XX.X (e.g., "486" -> 48.6)
    if len(digits) == 3:
        # XX.X most common for body weight
        val1 = float(digits[:2] + '.' + digits[2])
        if 10 <= val1 <= 250:
            candidates.append((val1, 18))
        # X.XX
        val2 = float(digits[0] + '.' + digits[1:])
        if 3 <= val2 <= 9.99:
            candidates.append((val2, 5))

    # 4 digits: XX.XX (e.g., "4865" -> 48.65)
    if len(digits) == 4:
        val = float(digits[:2] + '.' + digits[2:])
        if 10 <= val <= 250:
            candidates.append((val, 25))  # Highest priority - body weight format

    # 5 digits: XX.XX + noise (e.g., "04865" -> 48.65, "69753" -> 69.75)
    if len(digits) == 5:
        # Skip leading 0: 0XXXX -> XX.XX
        if digits[0] == '0':
            val = float(digits[1:3] + '.' + digits[3:5])
            if 10 <= val <= 250:
                candidates.append((val, 22))
        # XXX.XX -> take first 4 digits as XX.XX
        val2 = float(digits[:2] + '.' + digits[2:4])
        if 10 <= val2 <= 250:
            candidates.append((val2, 15))
        # XX.XXX -> take XX.XX
        val3 = float(digits[:2] + '.' + digits[2:4])
        if 10 <= val3 <= 250:
            candidates.append((val3, 12))

    # 6+ digits: try XX.XX from various positions
    if len(digits) >= 6:
        for start in range(len(digits) - 3):
            try:
                val = float(digits[start:start+2] + '.' + digits[start+2:start+4])
                if 10 <= val <= 250:
                    candidates.append((val, 8))
            except (ValueError, IndexError):
                pass

    if candidates:
        # Sort by priority (highest first), then by closeness to typical body weight
        candidates.sort(key=lambda x: (-x[1], abs(x[0] - 65)))
        return round(candidates[0][0], 2)

    return None


def read_weight(image_path):
    """
    Main OCR function: read weight from scale image.
    Returns dict with success, weight, confidence.
    """
    try:
        import cv2
        import easyocr

        img = cv2.imread(image_path)
        if img is None:
            return {"success": False, "weight": None, "message": "Gagal membaca gambar"}

        h, w = img.shape[:2]
        log("Image: %dx%d, file: %s" % (w, h, os.path.basename(image_path)))
        save_debug("00_original", img)

        # Step 1: Detect display regions
        displays = detect_display(img)
        log("Display regions: %d -> %s" % (len(displays), [d[0] for d in displays]))

        # Step 2: Generate preprocessing variants
        all_variants = []
        for dlabel, dimg in displays:
            save_debug("01_display_%s" % dlabel, dimg)
            for vlabel, vimg in make_variants(dimg, dlabel):
                all_variants.append(("%s/%s" % (dlabel, vlabel), vimg))

        log("Total variants: %d" % len(all_variants))

        # Step 3: Initialize EasyOCR
        reader = easyocr.Reader(['en'], gpu=False, verbose=False)

        # Step 4: Run OCR on each variant, collect votes
        weight_votes = {}  # weight_value -> [(confidence, source), ...]
        all_raw = []       # all raw OCR texts for fallback

        for label, variant in all_variants:
            # Early stop: already have 3+ votes for same weight with good confidence
            for wk, wv in weight_votes.items():
                if len(wv) >= 3 and max(c for c, _ in wv) > 0.5:
                    log("Early stop: %.2f kg has %d votes" % (wk, len(wv)))
                    break
            else:
                # No early stop, continue processing
                pass

            # Try with digit allowlist first (most accurate for 7-segment)
            ocr_results = []
            try:
                ocr_results = reader.readtext(
                    variant, detail=1, paragraph=False,
                    allowlist='0123456789.',
                    text_threshold=0.1,
                    low_text=0.1,
                    link_threshold=0.3,
                    mag_ratio=1.5,
                    width_ths=1.5,
                )
            except Exception as e:
                log("EasyOCR error on %s: %s" % (label, str(e)))

            # If nothing found, try without allowlist (more permissive)
            if not ocr_results:
                try:
                    ocr_results = reader.readtext(
                        variant, detail=1, paragraph=False,
                        text_threshold=0.08,
                        low_text=0.08,
                        mag_ratio=1.5,
                    )
                except Exception:
                    pass

            for (bbox, text, conf) in ocr_results:
                text = str(text).strip()
                if not text:
                    continue

                conf = float(conf)
                all_raw.append({"text": text, "conf": conf, "src": label})
                log("  Raw: '%s' (conf:%.3f, src:%s)" % (text, conf, label))

                # Try to parse as weight
                w_val = parse_weight(text)
                if w_val is not None:
                    log("  -> Parsed: %.2f kg" % w_val)
                    key = round(w_val, 1)  # Group by 0.1 kg
                    weight_votes.setdefault(key, []).append((conf, label))

        # Step 5: Vote for best weight
        log("--- Voting ---")
        log("Candidates: %s" % {str(k): len(v) for k, v in weight_votes.items()})

        if weight_votes:
            # Score = votes * avg_confidence (weighted towards more votes)
            scored = []
            for wval, votes in weight_votes.items():
                n = len(votes)
                avg_conf = sum(c for c, _ in votes) / n
                max_conf = max(c for c, _ in votes)
                # Score favors: more votes AND higher confidence
                score = n * (avg_conf * 0.6 + max_conf * 0.4) + (n - 1) * 0.2
                scored.append((wval, score, n, avg_conf, max_conf))
                log("  %.1f kg: votes=%d, avg=%.3f, max=%.3f, score=%.3f" %
                    (wval, n, avg_conf, max_conf, score))

            scored.sort(key=lambda x: -x[1])
            best = scored[0]
            best_weight = float(best[0])
            best_conf = float(best[4])  # max confidence

            log("WINNER: %.2f kg (score:%.3f, votes:%d, conf:%.3f)" %
                (best_weight, best[1], best[2], best_conf))

            return {
                "success": True,
                "weight": best_weight,
                "confidence": round(best_conf, 3),
                "votes": int(best[2]),
                "source": str(weight_votes[best[0]][0][1]),  # source of first vote
            }

        # Step 6: Fallback - combine all raw texts and try to parse
        log("--- Fallback: combining raw texts ---")
        combined = ' '.join(item["text"] for item in all_raw if item["conf"] > 0.05)
        log("Combined raw: '%s'" % combined)
        if combined:
            w_val = parse_weight(combined)
            if w_val is not None:
                log("Fallback parsed: %.2f kg" % w_val)
                return {
                    "success": True,
                    "weight": float(round(w_val, 2)),
                    "confidence": 0.2,
                    "votes": 1,
                    "source": "fallback_combined",
                }

        # Nothing worked
        raw_summary = ["%s(%.2f)" % (r["text"], r["conf"]) for r in all_raw[:10]]
        log("FAILED - raw texts: %s" % raw_summary)
        return {
            "success": False,
            "weight": None,
            "message": "Tidak bisa membaca angka berat",
            "raw_texts": raw_summary,
        }

    except ImportError as e:
        return {"success": False, "weight": None, "message": "Missing module: %s" % str(e)}
    except Exception as e:
        log("EXCEPTION: %s" % str(e))
        return {"success": False, "weight": None, "message": str(e)}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        r = {"success": False, "message": "Usage: python ocr_reader.py <image>"}
    elif not os.path.exists(sys.argv[1]):
        r = {"success": False, "message": "File tidak ditemukan: " + sys.argv[1]}
    else:
        if "--debug" in sys.argv:
            log("Debug mode: saving images to %s" % DEBUG_DIR)
        r = read_weight(sys.argv[1])

    out = json.dumps(r)
    sys.stderr.write("[RESULT] " + out + "\n")
    sys.stderr.flush()
    sys.stdout.write(out + "\n")
    sys.stdout.flush()
