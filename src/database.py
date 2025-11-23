import pymongo
import boto3
from botocore.client import Config as BotoConfig
from src.config import Config

# MongoDB
mongo_client = pymongo.MongoClient(Config.MONGO_URI)
db = mongo_client.academic_db

# S3 / MinIO
s3_client = boto3.client(
    's3',
    endpoint_url=Config.R2_ENDPOINT,
    aws_access_key_id=Config.R2_ACCESS,
    aws_secret_access_key=Config.R2_SECRET,
    config=BotoConfig(signature_version='s3v4'),
    region_name='us-east-1' 
)

# Cria bucket se não existir
try:
    s3_client.create_bucket(Bucket=Config.BUCKET_NAME)
except:
    pass