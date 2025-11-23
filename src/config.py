import os

class Config:
    MONGO_URI = os.getenv("MONGO_URI")
    RABBIT_HOST = os.getenv("RABBIT_HOST")
    RABBIT_USER = os.getenv("RABBIT_USER")
    RABBIT_PASS = os.getenv("RABBIT_PASS")
    QUEUE_NAME = "q.academic_tasks"
    
    R2_ENDPOINT = os.getenv("R2_ENDPOINT")
    R2_ACCESS = os.getenv("R2_ACCESS_KEY")
    R2_SECRET = os.getenv("R2_SECRET_KEY")
    BUCKET_NAME = os.getenv("BUCKET_NAME", "academic-files")

    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
    TG_WEBHOOK_SECRET = os.getenv("TG_WEBHOOK_SECRET")

    # chave para bypass de tempo
    ADMIN_KEY = os.getenv("ADMIN_KEY")
    API_PUBLIC_URL = os.getenv("API_PUBLIC_URL", "http://localhost:8000")