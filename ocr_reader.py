"""
SmartWeight IoT - OCR Reader v4 (Memory Optimized)
Optimized for: 7-segment display, ESP32-CAM, LOW RAM VPS Docker

Key optimizations vs v3:
- 4 variants instead of 8 (halves RAM)
- Process one variant at a time (don't store all in memory)
- 2x resize instead of 3x (saves ~55% image RAM)
- Aggressive early stop at confidence > 0.85
- Explicit garbage collection after each variant
- No debug image saving by default (saves disk I/O)

Usage: python ocr_reader.py <image_path> [--debug]
Output: JSON on stdout, debug logs on stderr
"""

import sys
import json
import os
import re
import gc
import warnings
warnings.filterwarnings("ignore")


def log(msg):
    sys.stderr.write("[OCR] %s\n" % msg)
    sys.stderr.flush()


def save_debug(name, img):
    """Only save if --debug flag is passed"""
    if "--debug" not in sys.argv:
        return
    try:
        import cv2
        ddir = "/tmp/ocr_debug"
        os.makedirs(ddir, exist_ok=True)
        cv2.imwrite(os.path.join(ddir, "%s.jpg" % name), img)
    except Exception:
        pass


def detect_display(img):
    """Detect display area. Returns single best crop + full image fallback."""
    import cv2
    import numpy as np

    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    mean_bright = float(np.mean(gray))
    log("Brightness: %.0f" % mean_bright)

    # Close-up: image is mostly display already
    if mean_bright < 130:
        # Trim borders slightly
        my, mx = int(h * 0.06), int(w * 0.04)
        trimmed = img[my:h-my, mx:int(w*0.85)]
        if trimmed.shape[0] > 30 and trimmed.shape[1] > 30:
            return trimmed
        return img

    # Find dark rectangular region (display)
    blurred = cv2.GaussianBlur(gray, (7, 7), 0)
    _, dark_mask = cv2.threshold(blurred, 90, 255, cv2.THRESH_BINARY_INV)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (30, 15))
    dark_mask = cv2.morphologyEx(dark_mask, cv2.MORPH_CLOSE, kernel)

    contours, _ = cv2.findContours(dark_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best_crop = None
    best_area = 0
    for c in sorted(contours, key=cv2.contourArea, reverse=True)[:3]:
        area = cv2.contourArea(c)
        if area > (w * h * 0.03) and area > best_area:
            x, y, cw, ch = cv2.boundingRect(c)
            ratio = cw / max(ch, 1)
            if 1.0 < ratio < 6.0 and ch > 25:
                pad = 10
                best_crop = img[max(0,y-pad):min(h,y+ch+pad), max(0,x-pad):min(w,x+cw+pad)]
                best_area = area
                log("Display found: %dx%d (ratio:%.1f)" % (cw, ch, ratio))

    if best_crop is not None:
        return best_crop

    # Fallback: center crop
    cy1, cy2 = int(h * 0.15), int(h * 0.85)
    cx1, cx2 = int(w * 0.1), int(w * 0.9)
    return img[cy1:cy2, cx1:cx2]


def process_variant(display_img, variant_name, reader):
    """
    Create ONE preprocessing variant, run OCR, return results, then free memory.
    This is the key RAM optimization: only one variant in memory at a time.
    """
    import cv2
    import numpy as np

    dh, dw = display_img.shape[:2]
    if dw < 20 or dh < 20:
        return []

    # Target: 2x upscale (not 3x — saves ~55% RAM per image)
    tw = min(dw * 2, 1280)
    th = min(dh * 2, 720)
    gray = cv2.cvtColor(display_img, cv2.COLOR_BGR2GRAY)

    variant = None

    if variant_name == "color":
        variant = cv2.resize(display_img, (tw, th), interpolation=cv2.INTER_LINEAR)

    elif variant_name == "otsu":
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(4, 4))
        enhanced = clahe.apply(gray)
        _, otsu = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        variant = cv2.resize(otsu, (tw, th), interpolation=cv2.INTER_NEAREST)
        variant = cv2.copyMakeBorder(variant, 15, 15, 15, 15, cv2.BORDER_CONSTANT, value=0)
        del enhanced, otsu

    elif variant_name == "inverted":
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(4, 4))
        enhanced = clahe.apply(gray)
        _, otsu = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        inv = cv2.bitwise_not(otsu)
        variant = cv2.resize(inv, (tw, th), interpolation=cv2.INTER_NEAREST)
        variant = cv2.copyMakeBorder(variant, 15, 15, 15, 15, cv2.BORDER_CONSTANT, value=255)
        del enhanced, otsu, inv

    elif variant_name == "adaptive":
        adaptive = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                          cv2.THRESH_BINARY, 25, -5)
        adaptive = cv2.morphologyEx(adaptive, cv2.MORPH_CLOSE,
                                     cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))
        variant = cv2.resize(adaptive, (tw, th), interpolation=cv2.INTER_NEAREST)
        variant = cv2.copyMakeBorder(variant, 15, 15, 15, 15, cv2.BORDER_CONSTANT, value=0)
        del adaptive

    del gray

    if variant is None:
        return []

    save_debug(variant_name, variant)

    # Run EasyOCR
    results = []
    try:
        ocr_out = reader.readtext(
            variant, detail=1, paragraph=False,
            allowlist='0123456789.',
            text_threshold=0.1,
            low_text=0.1,
            link_threshold=0.3,
            mag_ratio=1.0,  # Already upscaled, no extra magnification needed
            width_ths=1.5,
        )
        results = [(str(t).strip(), float(c)) for (_, t, c) in ocr_out if str(t).strip()]
    except Exception as e:
        log("OCR error %s: %s" % (variant_name, str(e)))

    # If digit-only found nothing, try without allowlist
    if not results:
        try:
            ocr_out = reader.readtext(
                variant, detail=1, paragraph=False,
                text_threshold=0.08,
                low_text=0.08,
                mag_ratio=1.0,
            )
            results = [(str(t).strip(), float(c)) for (_, t, c) in ocr_out if str(t).strip()]
        except Exception:
            pass

    # Free variant image from memory immediately
    del variant
    gc.collect()

    return results


def parse_weight(text):
    """Parse weight from OCR text with 7-segment corrections."""
    if not text:
        return None

    cleaned = text.strip()
    cleaned = re.sub(r'[kK][gG]$', '', cleaned).strip()
    cleaned = cleaned.replace(',', '.')

    # 7-segment substitutions
    for old, new in [
        ('O', '0'), ('o', '0'), ('Q', '0'), ('D', '0'), ('U', '0'),
        ('l', '1'), ('I', '1'), ('|', '1'), ('!', '1'), ('i', '1'), ('J', '1'),
        ('Z', '2'), ('z', '2'), ('R', '2'),
        ('E', '3'), ('e', '3'),
        ('H', '4'), ('h', '4'), ('Y', '4'), ('A', '4'), ('y', '4'),
        ('S', '5'), ('s', '5'),
        ('G', '6'), ('C', '6'), ('c', '6'),
        ('T', '7'), ('t', '7'),
        ('B', '8'), ('b', '8'),
        ('g', '9'), ('q', '9'), ('P', '9'), ('p', '9'),
        ('n', '0'), ('N', '0'),
    ]:
        cleaned = cleaned.replace(old, new)

    cleaned = re.sub(r'[^\d.]', '', cleaned)

    # Fix multiple dots
    if cleaned.count('.') > 1:
        first_dot = cleaned.index('.')
        cleaned = cleaned[:first_dot+1] + cleaned[first_dot+1:].replace('.', '')

    cleaned = cleaned.lstrip('0') or '0'
    if cleaned.startswith('.'):
        cleaned = '0' + cleaned

    if not cleaned or len(cleaned) < 2:
        return None

    # Direct parse
    if '.' in cleaned:
        try:
            val = float(cleaned)
            if 3.0 <= val <= 300.0:
                return round(val, 2)
        except ValueError:
            pass

    # Insert decimal point
    digits = re.sub(r'[^\d]', '', cleaned)
    if not digits or len(digits) < 2:
        return None

    candidates = []

    if len(digits) == 2:
        val = float(digits)
        if 10 <= val <= 250:
            candidates.append((val, 10))

    if len(digits) == 3:
        val = float(digits[:2] + '.' + digits[2])
        if 10 <= val <= 250:
            candidates.append((val, 18))

    if len(digits) == 4:
        val = float(digits[:2] + '.' + digits[2:])
        if 10 <= val <= 250:
            candidates.append((val, 25))

    if len(digits) == 5:
        if digits[0] == '0':
            val = float(digits[1:3] + '.' + digits[3:5])
            if 10 <= val <= 250:
                candidates.append((val, 22))
        val2 = float(digits[:2] + '.' + digits[2:4])
        if 10 <= val2 <= 250:
            candidates.append((val2, 15))

    if len(digits) >= 6:
        for s in range(len(digits) - 3):
            try:
                val = float(digits[s:s+2] + '.' + digits[s+2:s+4])
                if 10 <= val <= 250:
                    candidates.append((val, 8))
            except (ValueError, IndexError):
                pass

    if candidates:
        candidates.sort(key=lambda x: (-x[1], abs(x[0] - 65)))
        return round(candidates[0][0], 2)
    return None


def read_weight(image_path):
    """
    Main OCR: detect display, run 4 variants sequentially, vote for best weight.
    Memory-optimized: only one variant image in RAM at a time.
    """
    try:
        import cv2
        import easyocr

        img = cv2.imread(image_path)
        if img is None:
            return {"success": False, "weight": None, "message": "Gagal membaca gambar"}

        h, w = img.shape[:2]
        log("Image: %dx%d" % (w, h))
        save_debug("original", img)

        # Step 1: Detect display (returns single crop)
        display = detect_display(img)
        dh, dw = display.shape[:2]
        log("Display crop: %dx%d" % (dw, dh))
        save_debug("display", display)

        # Free original image — no longer needed
        del img
        gc.collect()

        # Step 2: Initialize EasyOCR once (biggest RAM cost, ~500MB)
        log("Loading EasyOCR...")
        reader = easyocr.Reader(['en'], gpu=False, verbose=False)
        log("EasyOCR ready")

        # Step 3: Process variants ONE AT A TIME (key RAM optimization)
        # Order: most effective first → early stop saves time + RAM
        variant_names = ["color", "otsu", "inverted", "adaptive"]

        weight_votes = {}
        found_high_conf = False

        for vname in variant_names:
            if found_high_conf:
                log("Skipping %s (early stop)" % vname)
                continue

            log("Processing: %s" % vname)
            ocr_results = process_variant(display, vname, reader)

            for text, conf in ocr_results:
                if len(text) < 2:
                    continue

                w_val = parse_weight(text)
                if w_val is not None:
                    log("  '%s' -> %.2f kg (conf:%.3f)" % (text, w_val, conf))
                    key = round(w_val, 1)
                    weight_votes.setdefault(key, []).append((conf, vname))

                    # EARLY STOP: high confidence + valid weight
                    if conf > 0.85 and 10 <= w_val <= 200:
                        log("  HIGH CONFIDENCE -> early stop!")
                        found_high_conf = True
                        break
                else:
                    log("  '%s' (conf:%.3f) -> no valid weight" % (text, conf))

        # Step 4: Vote for best weight
        log("Candidates: %s" % {str(k): len(v) for k, v in weight_votes.items()})

        if weight_votes:
            scored = []
            for wval, votes in weight_votes.items():
                n = len(votes)
                avg_conf = sum(c for c, _ in votes) / n
                max_conf = max(c for c, _ in votes)
                score = n * (avg_conf * 0.6 + max_conf * 0.4) + (n - 1) * 0.2
                scored.append((wval, score, n, max_conf, votes[0][1]))

            scored.sort(key=lambda x: -x[1])
            best = scored[0]

            log("RESULT: %.2f kg (votes:%d, conf:%.3f)" % (best[0], best[2], best[3]))

            # Cleanup before returning
            del reader, display
            gc.collect()

            return {
                "success": True,
                "weight": float(best[0]),
                "confidence": round(float(best[3]), 3),
                "votes": int(best[2]),
                "source": str(best[4]),
            }

        # Cleanup
        del reader, display
        gc.collect()

        return {"success": False, "weight": None, "message": "Tidak bisa membaca angka berat"}

    except Exception as e:
        gc.collect()
        log("ERROR: %s" % str(e))
        return {"success": False, "weight": None, "message": str(e)}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        r = {"success": False, "message": "Usage: python ocr_reader.py <image>"}
    elif not os.path.exists(sys.argv[1]):
        r = {"success": False, "message": "File tidak ditemukan: " + sys.argv[1]}
    else:
        r = read_weight(sys.argv[1])

    out = json.dumps(r)
    sys.stderr.write("[RESULT] " + out + "\n")
    sys.stderr.flush()
    sys.stdout.write(out + "\n")
    sys.stdout.flush()
