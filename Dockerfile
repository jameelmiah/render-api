# A tiny Linux with Python
FROM python:3.11-slim

# Install system tools needed by MoviePy:
# - ffmpeg (video/audio processing)
# - imagemagick (TextClip renders text)
# - fonts (we'll use DejaVu Sans Bold)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    imagemagick \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Optional: ImageMagick temp directory (helps on some hosts)
ENV MAGICK_TEMPORARY_PATH=/tmp

# App setup
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the app code
COPY . .

# Create a temp renders folder (where MP4s will be written)
RUN mkdir -p /tmp/renders

EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
