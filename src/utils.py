# --- START OF FILE src/utils.py ---
import re
import shlex
import secrets
import string
from datetime import datetime, timedelta
from src.database import db

# =========================================
#       UTILITÁRIOS GERAIS
# =========================================

def generate_link_code(platform, user_id):
    alphabet = string.ascii_uppercase + string.digits
    token = ''.join(secrets.choice(alphabet) for i in range(6))
    db.pending_links.insert_one({
        "token": token, "platform": platform, "user_id": user_id,
        "created_at": datetime.utcnow()
    })
    return token

def validate_link_code(token, target_platform, target_user_id):
    record = db.pending_links.find_one({"token": token})
    if not record: return False, "⛔ Código inválido ou expirado."
    if record['platform'] == target_platform: return False, "⚠️ Use a outra plataforma."
    
    source_id = record['user_id']
    current_linked = get_linked_ids(target_user_id)
    if source_id in current_linked:
        db.pending_links.delete_one({"_id": record["_id"]})
        return False, "⚠️ **Já conectados!**"

    config = db.user_settings.find_one({"$or": [{"user_id": source_id}, {"aliases": source_id}, {"user_id": target_user_id}, {"aliases": target_user_id}]})
    if not config: db.user_settings.insert_one({"user_id": source_id, "aliases": [target_user_id]})
    else: db.user_settings.update_one({"_id": config["_id"]}, {"$addToSet": {"aliases": {"$each": [source_id, target_user_id]}}})
        
    db.pending_links.delete_one({"_id": record["_id"]})
    return True, "✅ **Contas Vinculadas!**"

def get_linked_ids(user_id):
    config = db.user_settings.find_one({"$or": [{"user_id": user_id}, {"aliases": user_id}]})
    ids = {user_id}
    if config:
        if "user_id" in config: ids.add(config["user_id"])
        if "aliases" in config: ids.update(config["aliases"])
    return list(ids)

def unlink_account(requester_id):
    linked_ids = get_linked_ids(requester_id)
    if len(linked_ids) <= 1: return False, "⚠️ Nenhuma conta vinculada."
    db.user_settings.update_many({"aliases": requester_id}, {"$pull": {"aliases": requester_id}})
    db.user_settings.update_one({"user_id": requester_id}, {"$set": {"aliases": []}})
    return True, "✅ Desvinculado com sucesso."

def unlink_specific(requester_id, target_id_to_remove):
    try: target_id_to_remove = int(target_id_to_remove)
    except: pass
    linked_ids = get_linked_ids(requester_id)
    if target_id_to_remove not in linked_ids: return False, "🚫 ID não vinculado."
    db.user_settings.update_many({"$or": [{"user_id": requester_id}, {"aliases": requester_id}]}, {"$pull": {"aliases": target_id_to_remove}})
    db.user_settings.update_many({"user_id": target_id_to_remove}, {"$pull": {"aliases": requester_id}})
    return True, f"✅ Vínculo com `{target_id_to_remove}` removido."

def get_partners(user_id):
    return [uid for uid in get_linked_ids(user_id) if uid != user_id]

def singularize(text):
    text = text.strip()
    if text.lower() in ["tcc", "atps", "quiz"]: return text 
    if text.endswith("es"): return text[:-2]
    if text.endswith("s"): return text[:-1]
    return text

# =========================================
#       PARSERS DE DATA E TEMPO
# =========================================

def parse_time_string(text):
    if not text: return None
    text = text.lower().strip()
    multipliers = {'s': 1, 'seg': 1, 'min': 60, 'h': 3600, 'd': 86400, 'w': 604800, 'm': 2592000}
    pattern = r'(\d+)\s*(min|seg|s|h|d|w|m)'
    matches = re.findall(pattern, text)
    if not matches: return None
    total = 0
    for valor, unidade in matches:
        if unidade in multipliers: total += int(valor) * multipliers[unidade]
    return total

def format_seconds(seconds):
    if seconds < 60: return f"{seconds}s"
    if seconds < 3600: return f"{seconds//60}min"
    if seconds < 86400: return f"{seconds//3600}h"
    return f"{seconds//86400}d"

def parse_smart_date(date_str):
    if not date_str: return None
    date_str = date_str.replace('-', '/').replace('.', '/')
    clean_str = re.sub(r'[^\d/]', '', date_str)
    try:
        for fmt in ["%d/%m/%Y", "%Y/%m/%d", "%d/%m"]:
            try:
                d = datetime.strptime(clean_str, fmt)
                if fmt == "%d/%m":
                    now = datetime.now()
                    d = d.replace(year=now.year)
                    if d.date() < now.date(): d = d.replace(year=now.year + 1)
                return d.replace(hour=8, minute=0, second=0)
            except: pass
        parts = clean_str.split('/')
        if len(parts) < 2: return None
        now = datetime.now()
        day, month = int(parts[0]), int(parts[1])
        year = int(parts[2]) if len(parts) == 3 else now.year
        if year < 100: year += 2000
        d_obj = datetime(year, month, day, 8, 0, 0)
        if len(parts) == 2 and d_obj.date() < now.date(): d_obj = d_obj.replace(year=year + 1)
        return d_obj
    except: return None

def parse_cli_args(text):
    try: tokens = shlex.split(text)
    except: tokens = text.split()
    args = []
    flags = {"prio": None, "obs": ""}
    i = 0
    while i < len(tokens):
        token = tokens[i]
        lower_t = token.lower()
        if lower_t in ["-alta", "-high", "-urgente", "-critical"]: flags["prio"] = "critical"
        elif lower_t in ["-media", "-medium"]: flags["prio"] = "medium"
        elif lower_t in ["-baixa", "-low"]: flags["prio"] = "low"
        elif lower_t == "-obs":
            if i + 1 < len(tokens):
                flags["obs"] = tokens[i+1]
                i += 1 
        elif token.startswith("-"): pass
        else: args.append(token)
        i += 1
    return args, flags

# =========================================
#       GERADOR DE ÁRVORE (DUAL STYLE)
# =========================================

# --- Em src/utils.py ---

def generate_ascii_tree(tasks, mode='smart', style='diff'):
    if not tasks: return "📭 *Lista vazia!*"
    
    now = datetime.now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    dados = {}
    for p in tasks:
        tipo = p.get('tipo', 'Geral')
        if tipo not in dados: dados[tipo] = {}
        if p['materia'] not in dados[tipo]: dados[tipo][p['materia']] = []
        dados[tipo][p['materia']].append(p)

    lines = []
    
    # --- CONFIGURAÇÃO DE ESTILO ---
    if style == 'ansi':
        ESC = "\u001b["
        RESET = f"{ESC}0m"
        
        # CORES ATUALIZADAS
        COR_TITULO    = f"{ESC}1;37m" # BRANCO (Categorias)
        COR_TAG_TEXT  = f"{ESC}1;37m" # BRANCO (Flags [URG])
        
        COR_ESTRUTURA = f"{ESC}0;34m" # Azul Escuro (Árvore)
        COR_MATERIA   = f"{ESC}1;35m" # Roxo (Eventos)
        
        COR_URGENTE   = f"{ESC}1;31m" # Vermelho (Para a DATA)
        COR_MEDIO     = f"{ESC}1;33m" # Amarelo (Para a DATA)
        COR_BAIXO     = f"{ESC}0;34m" # Azul (Para a DATA e Obs)
        
        lines.append("```ansi")
    else:
        # Telegram (Diff)
        lines.append("🌲 *Visão Geral*")
        lines.append("```diff")
    
    tipos = sorted(dados.keys())
    priority = ["Provas", "Trabalhos"]
    tipos = priority + [x for x in tipos if x not in priority]
    tipos = [t for t in tipos if t in dados]

    for tipo in tipos:
        if style == 'ansi':
            lines.append(f"\n{COR_TITULO}: : {tipo.upper()} : :{RESET}")
        else:
            lines.append(f"+ : : {tipo.upper()} : :")
        
        materias = sorted(dados[tipo].keys())
        for j, materia in enumerate(materias):
            is_last_mat = (j == len(materias)-1)
            prefix = "└──" if is_last_mat else "├──"
            
            if style == 'ansi':
                # Estrutura Azul, Matéria Roxa
                lines.append(f"{COR_ESTRUTURA}{prefix} {RESET}{COR_MATERIA}{materia}{RESET}")
            else:
                lines.append(f"#  {prefix} {materia}")
            
            docs = sorted(dados[tipo][materia], key=lambda x: parse_smart_date(x['data']) or datetime.max)
            indent = "    " if is_last_mat else "│   "
            
            for k, d in enumerate(docs):
                conn = "└──" if k == len(docs)-1 else "├──"
                dt_obj = parse_smart_date(d['data'])
                delta_days = (dt_obj - today).days if dt_obj else 999
                prio = d.get('prioridade', 'low')
                
                tag = "[LOW]"
                if prio == 'critical': tag = "[URG]"
                elif prio == 'medium': tag = "[MED]"
                
                obs = d.get('observacoes', '')
                obs_str = f"({obs}) " if obs else ""
                
                if style == 'ansi':
                    # Lógica de Cor para a DATA (Smart)
                    date_color = COR_BAIXO # Padrão Azul
                    
                    if delta_days < 0 or prio == 'critical': 
                        date_color = COR_URGENTE
                    elif mode == 'smart' and delta_days <= 7:
                        date_color = COR_URGENTE
                    elif prio == 'medium' or (mode == 'smart' and delta_days <= 30):
                        date_color = COR_MEDIO
                    
                    # MONTAGEM DA LINHA (Obs -> Data -> Tag)
                    # Obs: Azul
                    # Data: Colorida (Vermelho/Amarelo/Azul)
                    # Tag: Branca
                    
                    str_line = (
                        f"{COR_ESTRUTURA}{indent}{conn} {RESET}"
                        f"{COR_BAIXO}{obs_str}{RESET}"
                        f"{date_color}{d['data']}{RESET} "
                        f"{COR_TAG_TEXT}{tag}{RESET}"
                    )
                    lines.append(str_line)
                
                else:
                    # Lógica Diff (Telegram) - Mantém estrutura padrão pois Diff não suporta cores livres
                    content = f"{conn} {obs_str}{d['data']} {tag}"
                    color_type = "none"
                    if delta_days < 0: color_type = "gray"
                    else:
                        if mode == 'manual':
                            if prio == 'critical': color_type = "red"
                            elif prio == 'medium': color_type = "orange"
                        else:
                            if prio == 'critical' or delta_days <= 7: color_type = "red"
                            elif prio == 'medium' or delta_days <= 30: color_type = "orange"

                    if color_type == "orange": lines.append(f"'  {indent}{content}'")
                    elif color_type == "red": lines.append(f"-  {indent}{content}")
                    elif color_type == "gray": lines.append(f"-  {indent}{content}")
                    else: lines.append(f"   {indent}{content}")

    lines.append("```")
    return "\n".join(lines)