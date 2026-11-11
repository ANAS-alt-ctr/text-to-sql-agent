# Text-to-SQL AI Data Analyst Agent
FROM python:3.12-slim

WORKDIR /app

# System deps: gcc is needed only if a wheel is missing for some package.
RUN apt-get update && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Build the sample database at image build time (fast; ~40 orders).
RUN python scripts/make_sample_db.py --out data/sample.db

EXPOSE 8000 8501

# Default: the FastAPI backend. Run Streamlit separately (see docker-compose.yml).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
