# --- START OF FILE src/notifier.py ---
import time
import requests
import json
from datetime import datetime, timedelta
from src.database import db
from src.config import Config
from src.utils import parse_smart_date, generate_ascii_tree, get_linked_ids, singularize

print("🔔 Notification Worker (Clean Output) Iniciado...", flush=True)

def get_brt_now():
    utc_now = datetime.utcnow()
    return utc_now - timedelta(hours=3)

def send_telegram_msg(chat_id, text):
    try:
        requests.post(f"https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
    except: pass

def send_discord_msg(user_id, text):
    if not Config.DISCORD_TOKEN: return
    headers = {"Authorization": f"Bot {Config.DISCORD_TOKEN}", "Content-Type": "application/json"}
    try:
        url_dm = "https://discord.com/api/v10/users/@me/channels"
        resp = requests.post(url_dm, json={"recipient_id": str(user_id)}, headers=headers)
        if resp.status_code in [200,201]:
            cid = resp.json()["id"]
            url_msg = f"https://discord.com/api/v10/channels/{cid}/messages"
            chunks = [text[i:i+1900] for i in range(0, len(text), 1900)]
            for c in chunks:
                requests.post(url_msg, json={"content": c}, headers=headers)
                time.sleep(0.5)
    except Exception as e: print(f"Err Discord: {e}")

def send_msg(target_id, text):
    target_id = str(target_id)
    if len(target_id) > 15: send_discord_msg(target_id, text)
    else: send_telegram_msg(target_id, text)

def check_fixed_24h_warning():
    pending = db.provas.find({"sent_24h": {"$ne": True}})
    now = get_brt_now() 
    today = now.date()
    for task in pending:
        dt = parse_smart_date(task.get('data', ''))
        if not dt: continue
        delta = (dt.date() - today).days
        if delta in [0, 1]:
            tit = "🚨 *É HOJE!*" if delta == 0 else "🚨 *É AMANHÃ!*"
            cat_sing = singularize(task.get('tipo', 'Geral'))
            msg = f"{tit}\n*{task['materia']}*\n📂 {cat_sing}\n📅 `{task['data']}`"
            print(f"🚀 24h Alert: {task['user_id']}", flush=True)
            send_msg(task['user_id'], msg)
            db.provas.update_one({"_id": task["_id"]}, {"$set": {"sent_24h": True}})

def check_periodic_reminders():
    users_settings = db.user_settings.find({"periodic_interval": {"$exists": True}})
    now = get_brt_now()

    for setting in users_settings:
        user_id = setting['user_id']
        interval = setting['periodic_interval']
        last_run = setting.get('last_periodic_run')
        mode = setting.get('notify_mode', 'smart')

        if not last_run:
            db.user_settings.update_one({"user_id": user_id}, {"$set": {"last_periodic_run": now}})
            continue

        if now >= last_run + timedelta(seconds=interval):
            print(f"⏰ Notificando {user_id} ({mode})...", flush=True)
            
            is_discord = len(str(user_id)) > 15
            style = 'ansi' if is_discord else 'diff'

            linked_ids = get_linked_ids(user_id)
            all_tasks = list(db.provas.find({"user_id": {"$in": linked_ids}}))
            
            if not all_tasks:
                db.user_settings.update_one({"user_id": user_id}, {"$set": {"last_periodic_run": now}})
                continue

            grouped_tasks = {}
            total_items = 0
            for t in all_tasks:
                d = parse_smart_date(t.get('data', ''))
                if not d: continue
                delta_days = (d.date() - now.date()).days
                if delta_days < 0: continue 
                prio = t.get('prioridade', 'low')
                include = (mode == 'manual') or (prio in ['critical', 'medium'] or delta_days <= 30)
                if include:
                    cat = t.get('tipo', 'Geral')
                    if cat not in grouped_tasks: grouped_tasks[cat] = []
                    grouped_tasks[cat].append((d, t, delta_days))
                    total_items += 1
            
            if total_items == 0:
                print(f"🔇 {user_id}: Vazio.", flush=True)
                db.user_settings.update_one({"user_id": user_id}, {"$set": {"last_periodic_run": now}})
                continue
            
            # --- PARTE 1: RESUMO DE TEXTO ---
            lines = [f"⏰ **Resumo ({mode.title()})**", ""]
            for cat in sorted(grouped_tasks.keys()):
                lines.append(f"📂 **{cat}**")
                items = sorted(grouped_tasks[cat], key=lambda x: x[0])
                for d, t, dias in items:
                    prio_raw = t.get('prioridade', 'low')
                    if prio_raw == 'critical' or dias <= 7: ico = "🚨"
                    elif prio_raw == 'medium' or dias <= 30: ico = "⚠️"
                    else: ico = "🔹"
                    t_str = "HOJE 🔥" if dias == 0 else ("AMANHÃ" if dias == 1 else f"em {dias}d")
                    lines.append(f"{ico} {t['materia']}: {t['data']} ({t_str})")
                lines.append("")

            # Envia o Resumo primeiro
            send_msg(user_id, "\n".join(lines))

            # --- PARTE 2: ÁRVORE VISUAL (Separada para não quebrar o código) ---
            tree = generate_ascii_tree(all_tasks, mode=mode, style=style)
            send_msg(user_id, tree)

            db.user_settings.update_one({"user_id": user_id}, {"$set": {"last_periodic_run": now}})

while True:
    try:
        check_fixed_24h_warning()
        check_periodic_reminders()
    except Exception as e:
        print(f"⚠️ Erro: {e}", flush=True)
    time.sleep(1)