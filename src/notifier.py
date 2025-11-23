# --- START OF FILE src/notifier.py ---

import time
import requests
from datetime import datetime, timedelta
from src.database import db
from src.config import Config
from src.utils import parse_smart_date, generate_ascii_tree

print("🔔 Notification Worker (Timezone Fixed) Iniciado...", flush=True)

def get_brt_now():
    """Retorna a data/hora atual ajustada para BRT (UTC-3)"""
    # Pega hora UTC do container
    utc_now = datetime.utcnow()
    # Subtrai 3 horas
    return utc_now - timedelta(hours=3)

def send_msg(chat_id, text):
    try:
        requests.post(
            f"https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
        )
    except Exception as e:
        print(f"❌ Erro API: {e}")

def singularize(text):
    text = text.strip()
    if text.lower() in ["tcc", "atps", "quiz"]: return text 
    if text.endswith("es"): return text[:-2]
    if text.endswith("s"): return text[:-1]
    return text

# =========================================
# 1. AVISO IMEDIATO (HOJE/AMANHÃ)
# =========================================
def check_fixed_24h_warning():
    pending = db.provas.find({"sent_24h": {"$ne": True}})
    now = get_brt_now() # Usa hora BRT
    today = now.date()

    for task in pending:
        dt_evento = parse_smart_date(task.get('data', ''))
        if not dt_evento: continue
        
        event_date = dt_evento.date()
        delta_days = (event_date - today).days

        titulo = ""
        if delta_days == 0: titulo = "🚨 *ATENÇÃO: É HOJE!* 🚨"
        elif delta_days == 1: titulo = "🚨 *ATENÇÃO: É AMANHÃ!* 🚨"
        
        if titulo:
            cat_full = task.get('tipo', 'Geral')
            cat_sing = singularize(cat_full)
            msg = (
                f"{titulo}\n\n"
                f"O evento: *{task['materia']}*\n"
                f"📂 Categoria: {cat_sing}\n"
                f"📅 Data: `{task['data']}`\n"
                f"Prepare-se!"
            )
            send_msg(task['user_id'], msg)
            db.provas.update_one({"_id": task["_id"]}, {"$set": {"sent_24h": True}})

# =========================================
# 2. RESUMO PERIÓDICO
# =========================================
# Em src/notifier.py

def check_periodic_reminders():
    users = db.user_settings.find({"periodic_interval": {"$exists": True}})
    now = get_brt_now()

    for user in users:
        user_id = user['user_id']
        interval = user['periodic_interval']
        last_run = user.get('last_periodic_run')
        mode = user.get('notify_mode', 'smart')

        if not last_run:
            db.user_settings.update_one({"user_id": user_id}, {"$set": {"last_periodic_run": now}})
            continue

        next_run = last_run + timedelta(seconds=interval)
        
        # Se chegou a hora de notificar
        if now >= next_run:
            all_tasks = list(db.provas.find({"user_id": user_id}))
            
            if not all_tasks: 
                db.user_settings.update_one({"user_id": user_id}, {"$set": {"last_periodic_run": now}})
                continue

            # 1. Filtra e Agrupa
            grouped_tasks = {}
            total_items = 0

            for t in all_tasks:
                d = parse_smart_date(t.get('data', ''))
                if not d: continue
                
                delta_days = (d.date() - now.date()).days
                if delta_days < 0: continue 

                prio = t.get('prioridade', 'low')
            
                # --- CORREÇÃO DE INDENTAÇÃO AQUI (Tudo dentro do For) ---
                # Lógica de Inclusão
                include = False
                if mode == 'manual':
                    include = True
                else:
                    # SMART: 
                    # Inclui se for: Critical, Medium OU <= 30 dias
                    if prio in ['critical', 'medium'] or delta_days <= 30:
                        include = True
                
                if include:
                    cat = t.get('tipo', 'Geral')
                    if cat not in grouped_tasks: grouped_tasks[cat] = []
                    grouped_tasks[cat].append((d, t, delta_days))
                    total_items += 1
            
            # (Fim do loop for)

            if total_items == 0:
                db.user_settings.update_one({"user_id": user_id}, {"$set": {"last_periodic_run": now}})
                continue
            
            # 2. Gera o Texto
            lines = ["⏰ *Resumo das pendências!*", ""]
            
            for cat in sorted(grouped_tasks.keys()):
                lines.append(f"📂 *{cat}*")
                
                # Ordena por Data
                items = sorted(grouped_tasks[cat], key=lambda x: x[0])
                
                for d, t, dias in items:
                    prio_raw = t.get('prioridade', 'low')
                    
                    # Ícones Inteligentes
                    if prio_raw == 'critical' or dias <= 7:
                        ico = "🚨"
                    elif prio_raw == 'medium' or dias <= 30:
                        ico = "⚠️"
                    else:
                        ico = "🔹"
                        
                    t_str = "HOJE 🔥" if dias == 0 else ("AMANHÃ" if dias == 1 else f"em {dias}d")
                    lines.append(f"{ico} {t['materia']}: {t['data']} ({t_str})")
                
                lines.append("")

            # 3. Anexa Árvore
            lines.append(generate_ascii_tree(all_tasks, mode=mode))

            send_msg(user_id, "\n".join(lines))
            
            # Atualiza tempo da última execução
            db.user_settings.update_one({"user_id": user_id}, {"$set": {"last_periodic_run": now}})

while True:
    try:
        check_fixed_24h_warning()
        check_periodic_reminders()
    except Exception as e:
        print(f"⚠️ Erro Loop Notifier: {e}")
    time.sleep(1)