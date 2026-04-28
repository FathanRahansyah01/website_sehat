# =============================================
# SmartWeight IoT - PHP + Apache + Python OCR
# =============================================
FROM php:8.2-apache

# Install PHP extensions untuk MySQL
RUN docker-php-ext-install mysqli pdo pdo_mysql

# Install Python + dependensi OCR + Tesseract (fallback)
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    tesseract-ocr \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Buat virtual environment Python dan install packages
RUN python3 -m venv /opt/ocr-venv
RUN /opt/ocr-venv/bin/pip install --no-cache-dir \
    easyocr \
    Pillow

# Enable Apache mod_rewrite
RUN a2enmod rewrite

# Set AllowOverride All agar .htaccess bisa jalan
RUN sed -i '/<Directory \/var\/www\/html>/,/<\/Directory>/ s/AllowOverride None/AllowOverride All/' /etc/apache2/sites-available/000-default.conf

# Symlink Python OCR agar bisa dipanggil sebagai 'python'
RUN ln -sf /opt/ocr-venv/bin/python3 /usr/local/bin/python

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

EXPOSE 80
