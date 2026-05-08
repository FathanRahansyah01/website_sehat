# =============================================
# SmartWeight IoT - PHP + Apache + Python OCR
# OPTIMIZED: ~4GB → hemat 50%+ dari sebelumnya
# =============================================
FROM php:8.2-apache

# --- Layer 1: PHP extensions (cached, jarang berubah) ---
RUN docker-php-ext-install mysqli pdo pdo_mysql

# --- Layer 2: System deps + Python (cached, jarang berubah) ---
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-minimal \
    python3-pip \
    libgl1 \
    libglib2.0-0 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# --- Layer 3: Python OCR packages (cached, paling besar ~3GB) ---
# Install PyTorch CPU-only (JAUH lebih kecil dari default GPU)
# lalu EasyOCR + deps
RUN pip3 install --no-cache-dir --break-system-packages \
    torch torchvision --index-url https://download.pytorch.org/whl/cpu \
    && pip3 install --no-cache-dir --break-system-packages \
    easyocr \
    Pillow \
    opencv-python-headless \
    && rm -rf /root/.cache/pip /tmp/*

# --- Layer 4: Apache config (cached, jarang berubah) ---
RUN a2enmod rewrite \
    && sed -i '/<Directory \/var\/www\/html>/,/<\/Directory>/ s/AllowOverride None/AllowOverride All/' /etc/apache2/sites-available/000-default.conf \
    && echo "date.timezone = Asia/Jakarta" > /usr/local/etc/php/conf.d/timezone.ini \
    && ln -sf /usr/bin/python3 /usr/local/bin/python

ENV TZ=Asia/Jakarta

# --- Layer 5: Application code (berubah sering, di bawah supaya layer atas cached) ---
COPY . /var/www/html/

# Setup permissions
RUN mkdir -p /var/www/html/uploads \
    && chown -R www-data:www-data /var/www/html \
    && chmod -R 755 /var/www/html/uploads

# Verifikasi OCR (build gagal kalau deps missing)
RUN python -c "import cv2; import easyocr; print('OCR OK:', cv2.__version__)"

EXPOSE 80
