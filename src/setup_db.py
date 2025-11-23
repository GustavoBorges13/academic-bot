from src.database import db
# Cria índice que apaga documentos após 300 segundos (5 min) baseado no campo created_at
db.pending_links.create_index("created_at", expireAfterSeconds=300)
print("Índice TTL criado!")