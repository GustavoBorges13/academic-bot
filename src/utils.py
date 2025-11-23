# --- START OF FILE src/utils.py ---
import re
import shlex
from datetime import datetime

def parse_time_string(text):
    """Converte '1h 30m', '2s' para segundos."""
    if not text: return None
    text = text.lower().strip()
    multipliers = {'s': 1, 'seg': 1, 'min': 60, 'h': 3600, 'd': 86400, 'w': 604800, 'm': 2592000}
    pattern = r'(\d+)\s*(min|seg|s|h|d|w|m)'
    matches = re.findall(pattern, text)
    
    if not matches: return None
        
    total = 0
    for valor, unidade in matches:
        if unidade in multipliers:
            total += int(valor) * multipliers[unidade]
    return total

def format_seconds(seconds):
    if seconds < 60: return f"{seconds}s"
    if seconds < 3600: return f"{seconds//60}min"
    if seconds < 86400: return f"{seconds//3600}h"
    return f"{seconds//86400}d"

def parse_smart_date(date_str):
    """Converte '10/12', '10-12-2025' -> datetime object."""
    if not date_str: return None
    
    # 1. Normaliza separadores (troca - ou . por /)
    date_str = date_str.replace('-', '/').replace('.', '/')
    
    # 2. Remove qualquer coisa que NÃO seja número ou barra (ex: aspas extras)
    clean_str = re.sub(r'[^\d/]', '', date_str)
    
    try:
        # Tenta formatos com a string limpa
        for fmt in ["%d/%m/%Y", "%Y/%m/%d", "%d/%m"]:
            try:
                d = datetime.strptime(clean_str, fmt)
                
                # Lógica para data sem ano (dd/mm)
                if fmt == "%d/%m":
                    now = datetime.now()
                    d = d.replace(year=now.year)
                    # Se a data já passou este ano, joga pro ano que vem
                    if d.date() < now.date():
                        d = d.replace(year=now.year + 1)
                
                return d.replace(hour=8, minute=0, second=0)
            except: pass
            
        # Fallback manual (caso o strptime falhe)
        parts = clean_str.split('/')
        if len(parts) < 2: return None

        now = datetime.now()
        day, month = int(parts[0]), int(parts[1])
        
        # Se tiver 3 partes, usa o ano fornecido. Se tiver 2, usa lógica smart.
        if len(parts) == 3:
            year = int(parts[2])
        else:
            year = now.year
            
        # Corrige ano com 2 dígitos (ex: 26 vira 2026)
        if year < 100: year += 2000
        
        d_obj = datetime(year, month, day, 8, 0, 0)
        
        # Se for sem ano e já passou, +1 ano
        if len(parts) == 2 and d_obj.date() < now.date():
             d_obj = d_obj.replace(year=year + 1)
             
        return d_obj
    except:
        return None

def parse_cli_args(text):
    """Separa argumentos e flags (-alta, -obs). Suporta aspas."""
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


def generate_ascii_tree(tasks, mode='smart'):
    if not tasks: return "📭 *Lista vazia!*"
    
    now = datetime.now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    dados = {}
    for p in tasks:
        tipo = p.get('tipo', 'Geral')
        if tipo not in dados: dados[tipo] = {}
        if p['materia'] not in dados[tipo]: dados[tipo][p['materia']] = []
        dados[tipo][p['materia']].append(p)

    lines = ["🌲 *Visão Geral*", "```diff"]
    
    tipos = sorted(dados.keys())
    for tipo in tipos:
        lines.append(f"+ : : {tipo.upper()} : :")
        
        materias = sorted(dados[tipo].keys())
        for j, materia in enumerate(materias):
            prefix = "└──" if j == len(materias)-1 else "├──"
            
            # MUDANÇA 1: Colocamos '#' na Matéria para ela ficar Cinza
            # # + 2 espaços = 3 caracteres. Alinha com os filhos.
            lines.append(f"#  {prefix} {materia}")
            
            docs = sorted(dados[tipo][materia], key=lambda x: parse_smart_date(x['data']) or datetime.max)
            
            indent = "    " if prefix == "└──" else "│   "
            
            for k, d in enumerate(docs):
                conn = "└──" if k == len(docs)-1 else "├──"
                
                dt_obj = parse_smart_date(d['data'])
                delta_days = (dt_obj - today).days if dt_obj else 999
                prio = d.get('prioridade', 'low')
                
                if prio == 'critical': tag = "[URG]"
                elif prio == 'medium': tag = "[MED]"
                else: tag = "[LOW]"
                
                obs = d.get('observacoes', '')
                obs_str = f"{obs} " if obs else ""
                
                content = f"{conn} {obs_str}{d['data']} {tag}"

                # --- DECISÃO DE COR ---
                color_type = "none" # Padrão agora é SEM COR (Branco/Normal)
                
                if delta_days < 0:
                    color_type = "gray" # Passado continua Cinza (opcional)
                else:
                    if mode == 'manual':
                        if prio == 'critical': color_type = "red"
                        elif prio == 'medium': color_type = "orange"
                        else: color_type = "none" # Baixa manual = Sem cor
                    else: # Smart
                        if prio == 'critical' or delta_days <= 7: color_type = "red"
                        elif prio == 'medium' or delta_days <= 30: color_type = "orange"
                        else: color_type = "none" # Seguro smart = Sem cor

                # --- MONTAGEM VISUAL ---
                
                if color_type == "orange":
                    # Laranja (Aspas) -> '  │   └── ...'
                    lines.append(f"'  {indent}{content}'")
                    
                elif color_type == "none":
                    # Sem cor (Espaços) -> '   │   └── ...'
                    # 3 espaços para alinhar com o '#  ' ou '-  '
                    lines.append(f"   {indent}{content}")
                    
                else:
                    # Símbolos (- ou #) -> '-  │   └── ...'
                    sym = "-" if color_type == "red" else "#"
                    lines.append(f"{sym}  {indent}{content}")

    lines.append("```")
    return "\n".join(lines)