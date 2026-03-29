# ===== BASE IMAGE =====
FROM python:3.11-slim

# ===== WORKDIR =====
WORKDIR /app

# ===== SYSTEM DEPENDENCIES  =====
RUN apt-get update && apt-get install -y \
    gcc \
    default-libmysqlclient-dev \
    pkg-config \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# ===== COPY REQUIREMENTS =====
COPY requirements.txt .

# ===== INSTALL PYTHON DEPENDENCIES =====
RUN pip install --no-cache-dir -r requirements.txt

# ===== COPY APP =====
COPY . .

# ===== RUN APP (TEMP - DEV ONLY) =====
CMD ["python", "app.py"]
