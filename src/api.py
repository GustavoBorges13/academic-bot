from fastapi import FastAPI, Header, HTTPException
import pika
import json
import time
from collections import defaultdict
from prometheus_fastapi_instrumentator import Instrumentator
from src.config import Config
from src.database import db

app = FastAPI(
    title="Academic Bot Master",
    docs_url=None,
    redoc_url=None
)
Instrumentator().instrument(app).expose(app)

# ==========================================
#       🛡️ SISTEMA ANTI-SPAM INTELIGENTE
# ==========================================

RATE_LIMIT_COUNT = 10    # Mensagens permitidas
RATE_LIMIT_WINDOW = 15   # Janela de tempo (segundos)
BASE_BLOCK_TIME = 10     # Tempo de bloqueio inicial (segundos)
PENALTY_DECAY = 300      # 5 Minutos (Se ficar 5 min sem spam, reseta o nível da pena)

class RateLimiter:
    def __init__(self):
        self.history = defaultdict(list)
        self.blocked_until = defaultdict(float)
        self.penalty_level = defaultdict(int)
        self.last_infraction = defaultdict(float)

    # MUDANÇA 1: Agora retorna 3 valores: Status, Duração, Nível
    def check(self, user_id: int):
        if not user_id: return "OK", 0, 0
        
        now = time.time()

        if user_id in self.blocked_until:
            if now < self.blocked_until[user_id]:
                return "BLOCKED", 0, 0
            else:
                del self.blocked_until[user_id]

        if now - self.last_infraction[user_id] > PENALTY_DECAY:
            self.penalty_level[user_id] = 0

        self.history[user_id] = [t for t in self.history[user_id] if now - t < RATE_LIMIT_WINDOW]

        if len(self.history[user_id]) >= RATE_LIMIT_COUNT:
            self.penalty_level[user_id] += 1
            current_level = self.penalty_level[user_id]
            
            # --- ALTERAÇÃO AQUI ---
            # Antes era: block_duration = BASE_BLOCK_TIME * current_level
            # Agora usamos potência de 2:
            
            block_duration = BASE_BLOCK_TIME * (2 ** (current_level - 1))
            
            # Opcional: Colocar um teto máximo (ex: 1 hora) para não virar um número infinito
            if block_duration > 3600: 
                block_duration = 3600

            print(f"🚫 SPAM: Bloqueando {user_id} por {block_duration}s (Nível {current_level})")
            
            self.blocked_until[user_id] = now + block_duration
            self.last_infraction[user_id] = now
            self.history[user_id] = [] 
            
            # Retorna o Nível também
            return "JUST_BLOCKED", block_duration, current_level

        self.history[user_id].append(now)
        return "OK", 0, 0

limiter = RateLimiter()

# ==========================================
#       🐰 RABBITMQ
# ==========================================

def publish_to_rabbit(msg):
    try:
        creds = pika.PlainCredentials(Config.RABBIT_USER, Config.RABBIT_PASS)
        conn = pika.BlockingConnection(pika.ConnectionParameters(host=Config.RABBIT_HOST, credentials=creds))
        ch = conn.channel()
        ch.queue_declare(queue=Config.QUEUE_NAME, durable=True)
        ch.basic_publish(
            exchange='', routing_key=Config.QUEUE_NAME, 
            body=json.dumps(msg), properties=pika.BasicProperties(delivery_mode=2)
        )
        conn.close()
    except Exception as e:
        print(f"❌ Erro Rabbit: {e}")

@app.post("/webhook/telegram")
async def telegram_webhook(
    request: dict, 
    x_telegram_bot_api_secret_token: str = Header(None)
):
    if x_telegram_bot_api_secret_token != Config.TG_WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        user_id = None
        chat_id = None
        
        if "message" in request:
            user_id = request["message"]["from"]["id"]
            chat_id = request["message"]["chat"]["id"]
        elif "callback_query" in request:
            user_id = request["callback_query"]["from"]["id"]
            chat_id = request["callback_query"]["message"]["chat"]["id"]

        # MUDANÇA 2: Recebe o nível
        status, duration, level = limiter.check(user_id)
        
        if status == "BLOCKED":
            return {"status": "ignored_spam"}
        
        elif status == "JUST_BLOCKED":
            # MUDANÇA 3: Envia o nível para o Worker
            publish_to_rabbit({
                "action": "spam_warning",
                "chat_id": chat_id,
                "duration": duration,
                "level": level  # <--- Enviando o nível
            })
            return {"status": "blocked_alert_sent"}

        payload = {
            "action": "process_update",
            "raw_update": request,
            "chat_id": chat_id
        }
        publish_to_rabbit(payload)
        return {"status": "queued"}
        
    except Exception as e:
        print(f"⚠️ Erro API: {e}")
        return {"status": "error"}

# ==========================================
#       🔗 ROTA DE EXPORTAÇÃO (JSON)
# ==========================================
@app.get("/export/{token}")
async def export_json_via_link(token: str):
    # 1. Busca quem é o dono desse token
    user_settings = db.user_settings.find_one({"export_token": token})
    
    if not user_settings:
        raise HTTPException(status_code=404, detail="Token inválido ou revogado.")
    
    user_id = user_settings["user_id"]
    
    # 2. Busca as provas desse usuário (Limpa dados sensíveis)
    tasks = list(db.provas.find(
        {"user_id": user_id}, 
        {"_id": 0, "user_id": 0, "sent_24h": 0}
    ))
    
    # 3. Retorna JSON formatado
    return {
        "status": "success",
        "user_id_hash": str(hash(user_id)), # Apenas para referência, não expõe o ID real
        "generated_at": time.time(),
        "total_items": len(tasks),
        "data": tasks
    }