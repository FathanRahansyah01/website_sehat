# =============================================
# SmartWeight IoT - PHP + Apache + Python OCR
# =============================================
FROM php:8.2-apache

# Install PHP extensions untuk MySQL
RUN docker-php-ext-install mysqli pdo pdo_mysql

# Set timezone PHP ke WIB
RUN echo "date.timezone = Asia/Jakarta" > /usr/local/etc/php/conf.d/timezone.ini
ENV TZ=Asia/Jakarta

# Install Python + dependensi sistem untuk OCR
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Install packages OCR langsung (tanpa venv, Docker sudah isolated)
RUN pip3 install --no-cache-dir --break-system-packages \
    easyocr \
    Pillow \
    opencv-python-headless

# Enable Apache mod_rewrite
RUN a2enmod rewrite

# Set AllowOverride All agar .htaccess bisa jalan
RUN sed -i '/<Directory \/var\/www\/html>/,/<\/Directory>/ s/AllowOverride None/AllowOverride All/' /etc/apache2/sites-available/000-default.conf

# Copy semua file project
COPY . /var/www/html/

# Hapus file yang tidak perlu di container
RUN rm -rf /var/www/html/esp32cam_weight \
    /var/www/html/.venv \
    /var/www/html/.git \
    /var/www/html/docker \
    /var/www/html/Dockerfile \
    /var/www/html/docker-compose.yml \
    /var/www/html/.dockerignore

# Buat folder uploads dengan permission yang benar
RUN mkdir -p /var/www/html/uploads \
    && chown -R www-data:www-data /var/www/html \
    && chmod -R 755 /var/www/html/uploads

# Symlink agar 'python' = python3
RUN ln -sf /usr/bin/python3 /usr/local/bin/python

# Verifikasi: build GAGAL kalau OCR deps tidak ada
RUN python -c "import cv2; import easyocr; import numpy; print('✅ OCR deps OK:', cv2.__version__)"

EXPOSE 80
