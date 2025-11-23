# --- START OF FILE src/worker.py ---

import time
import json
import pika
import requests
import os
import re
import shlex 
import uuid
from datetime import datetime, timedelta
from bson import ObjectId
from prometheus_client import start_http_server, Counter, Histogram
from src.config import Config
from src.database import db
from src.utils import (
    parse_time_string, format_seconds, parse_smart_date, 
    parse_cli_args, generate_ascii_tree, singularize, 
    generate_link_code, validate_link_code, get_linked_ids, 
    unlink_account, get_partners, unlink_specific
)
# --- MÉTRICAS ---
TASKS = Counter('academic_tasks_total', 'Total Tarefas', ['action'])
LATENCY = Histogram('task_processing_seconds', 'Tempo Processamento')

print("👷 Worker (CLI V21 - Secure Alerts) Iniciado...", flush=True)
start_http_server(8001)

# =========================================
#       1. UTILITÁRIOS
# =========================================
def get_brt_now():
    return datetime.utcnow() - timedelta(hours=3)

def regex_ci(value):
    return {"$regex": f"^{re.escape(str(value).strip())}$", "$options": "i"}

def send_tg(chat_id, text, buttons=None, msg_id=None, silent=False):
    url_base = f"https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown", "disable_notification": silent}
    if buttons: payload["reply_markup"] = json.dumps(buttons)
    try:
        if msg_id:
            payload["message_id"] = msg_id
            r = requests.post(f"{url_base}/editMessageText", json=payload)
            if not r.json().get("ok"): 
                requests.post(f"{url_base}/sendMessage", json=payload)
        else:
            r = requests.post(f"{url_base}/sendMessage", json=payload)
            # ADICIONE ISSO PARA VERIFICAR ERROS NO LOG
            if not r.json().get("ok"):
                print(f"❌ Erro Telegram: {r.text}", flush=True)
            return r.json().get("result", {}).get("message_id")
    except Exception as e:
        print(f"❌ Exception SendTG: {e}", flush=True) # Imprime o erro real
        return None

def answer_callback(callback_id, text=""):
    try: requests.post(f"https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/answerCallbackQuery", json={"callback_query_id": callback_id, "text": text})
    except: pass

def delete_msg(chat_id, msg_id):
    try: requests.post(f"https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/deleteMessage", json={"chat_id": chat_id, "message_id": msg_id})
    except: pass

def create_grid(buttons, cols=3):
    return [buttons[i:i + cols] for i in range(0, len(buttons), cols)]

def singularize(text):
    text = text.strip()
    if text.lower() in ["tcc", "atps", "quiz"]: return text 
    if text.endswith("es"): return text[:-2]
    if text.endswith("s"): return text[:-1]
    return text

def get_all_cats(user_id):
    defaults = {"Provas", "Trabalhos"}
    used = set(db.provas.distinct("tipo", {"user_id": user_id}))
    cfg = db.user_settings.find_one({"user_id": user_id}) or {}
    saved = set(cfg.get("custom_cats", []))
    return sorted(list(defaults | used | saved))

# =========================================
#       2. PAINEL & LISTAGEM
# =========================================

def get_user_layout(user_id):
    cfg = db.user_settings.find_one({"user_id": user_id})
    return cfg.get("layout", "vertical") if cfg else "vertical"

def toggle_user_layout(user_id):
    current = get_user_layout(user_id)
    new_layout = "vertical" if current == "horizontal" else "horizontal"
    db.user_settings.update_one({"user_id": user_id}, {"$set": {"layout": new_layout}}, upsert=True)
    return new_layout

def format_doc_line(doc):
    prio = doc.get('prioridade', 'low')
    icon = "🚨" if prio == 'critical' else ("⚠️" if prio == 'medium' else "")
    obs = doc.get('observacoes', '')
    
    # --- MUDANÇA AQUI ---
    # Antes estava algo como: obs_str = f"({obs})" if obs else ""
    # Agora basta passar a variável 'obs' direto para a lista.
    # O " ".join já cuida de separar com espaço se tiver conteúdo.
    
    parts = [obs, doc['data'], icon]
    
    # Filtra partes vazias (if p) e junta com espaço
    return " ".join([p for p in parts if p])

def gerar_painel(user_id, provas, layout_override=None):
    if not provas: return "📭 *Sua agenda está vazia!*"
    dados = {}
    for p in provas:
        tipo = p.get('tipo', 'Geral')
        if tipo not in dados: dados[tipo] = {}
        if p['materia'] not in dados[tipo]: dados[tipo][p['materia']] = []
        dados[tipo][p['materia']].append(p)

    lines = ["🎓 *Painel Acadêmico*"]
    tipos = sorted(dados.keys())
    layout_final = layout_override if layout_override else get_user_layout(user_id)

    for tipo in tipos:
        lines.append(f"\n: : *{tipo}* : :")
        materias = sorted(dados[tipo].keys())
        for j, materia in enumerate(materias):
            prefix = "└──" if j == len(materias)-1 else "├──"
            lines.append(f"`{prefix} {materia}`")
            docs = sorted(dados[tipo][materia], key=lambda x: parse_smart_date(x['data']) or datetime.max)
            indent = "    " if prefix == "└──" else "│   "
            
            if layout_final == "horizontal":
                formatted = [format_doc_line(d) for d in docs]
                for k in range(0, len(formatted), 2):
                    chunk = formatted[k:k+2]
                    conn = "└──" if k+2 >= len(formatted) else "├──"
                    content = " - ".join(chunk)
                    lines.append(f"`{indent}{conn} {content}`")
            else:
                for k, d in enumerate(docs):
                    conn = "└──" if k == len(docs)-1 else "├──"
                    content = format_doc_line(d)
                    lines.append(f"`{indent}{conn} {content}`")
    return "\n".join(lines)

def listar_agenda(chat_id, msg_id=None):
    # Busca provas para gerar o TEXTO visual (Arvore/Painel)
    # Pega lista de IDs vinculados
    ids = get_linked_ids(chat_id)
    # Busca provas de todos eles
    provas = list(db.provas.find({"user_id": {"$in": ids}}).sort("data", 1))
    texto = gerar_painel(chat_id, provas)
    
    kb = {"inline_keyboard": []}
    
    # --- AQUI ESTA A MUDANÇA ---
    # Removemos o loop "for p in provas..." que criava botões infinitos aqui.
    
    # Adicionamos o botão que leva para a nova tela de gestão
    if provas:
        kb["inline_keyboard"].append([
            {"text": "⚙️ Gerenciar Eventos (Editar/Apagar)", "callback_data": "manage_init"}
        ])

    # Botões padrão do menu principal
    kb["inline_keyboard"].append([
        {"text": "➕ Criar Novo", "callback_data": "wiz_init"},
        {"text": "🔔 Alertas", "callback_data": "notify_menu"}
    ])
    
    row_utils = [
        {"text": "❓ Ajuda", "callback_data": "ajuda"},
        {"text": "🎨 Layout", "callback_data": "toggle_layout"},
        {"text": "🔄 Atualizar", "callback_data": "menu"}
    ]
    kb["inline_keyboard"].append(row_utils)
    
    send_tg(chat_id, f"{texto}", kb, msg_id)

def menu_gerenciar(chat_id, mode="edit", msg_id=None):
    """
    mode: 'edit' (Lapis) ou 'del' (Lixeira)
    """
    provas = list(db.provas.find({"user_id": chat_id}).sort("data", 1))
    
    if not provas:
        return listar_agenda(chat_id, msg_id)

    # Define o visual baseando no modo atual
    if mode == "edit":
        icon_mode = "✏️"
        text_header = "MODO EDIÇÃO (Toque para editar)"
        next_mode = "del"
        btn_toggle_text = "🔄 Alternar para Modo APAGAR 🗑️"
    else:
        icon_mode = "🗑️"
        text_header = "MODO DELEÇÃO (Toque para apagar)"
        next_mode = "edit"
        btn_toggle_text = "🔄 Alternar para Modo EDITAR ✏️"

    kb = {"inline_keyboard": []}

    # 1. Botão de Toggle (Alternância) no topo
    kb["inline_keyboard"].append([
        {"text": btn_toggle_text, "callback_data": f"manage_mode:{next_mode}"}
    ])

    # 2. Lista os eventos (Botões Compactos)
    for p in provas:
        doc_id = str(p["_id"])
        # Formata: [Icone] Materia (Data)
        label = f"{icon_mode} {p['materia'][:15]} ({p['data'][:5]})"
        
        if mode == "edit":
            callback = f"open:{doc_id}"
        else:
            # MUDANÇA AQUI: Usamos um novo callback 'manage_del_ask' 
            # para saber que o pedido de delete veio dessa lista
            callback = f"manage_del_ask:{doc_id}"
            
        kb["inline_keyboard"].append([{"text": label, "callback_data": callback}])

    # 3. Botão Voltar
    kb["inline_keyboard"].append([{"text": "🔙 Voltar ao Painel", "callback_data": "menu"}])

    send_tg(chat_id, f"⚙️ *Gerenciamento de Eventos*\n\n{text_header}", kb, msg_id)

def enviar_ajuda(chat_id, eh_erro=False, msg_id=None):
    prefixo = "⚠️ *Comando ou sintaxe inválida!*\n\n" if eh_erro else ""
    texto = (
        f"{prefixo}"
        "🐧 *GUIA DE COMANDOS ACADÊMICO* 🎓\n"
        "_Dica: A barra '/' é opcional._\n\n"

        "🌱 *ADICIONAR:*\n"
        "`add Categoria` (Cria pasta - Categoria vazia s/ eventos)\n"
        "`add Cat Evento Data [Flags]`\n"
        "Ex: `add Provas Cálculo 10/12`\n"
        "Ex: `add Trab SO2 15/12 -alta -obs \"Fazendo!\"`\n\n"
        "🏁 *Flags Disponíveis:*\n"
        "`-alta`, `-media`, `-baixa(default)` (Prioridade)\n"
        "`-obs \"texto\"` _ou somente ASPAS (Observação/anotações)_\n\n"

        "✏️ *EDITAR (Use >):*\n"
        "🔹 _Renomear:_\n"
        "`edit Categoria > CategoriaNova`\n"
        "Ex: `edit aushuah > complementares` _(renomeia nome da categoria)_\n"
        "`edit Cat Evento > Cat EventoNovo`\n"
        "Ex: `edit provas SO2 > provas SO1` _(renomeia o nome do Evento)_\n"
        "`edit Cat Event Data > Cat Event DataNova`\n"
        "Ex: `edit provas SO2 23/11/2025 > provas SO2 24/11/2025` _(altera a data de um evento)_\n\n"
        
        "🔹 _Mover:_\n"
        "`edit CategoriaX EventoX > CategoriaY EventoX`\n"
        "Ex1: `edit provas SO2 > trabalhos SO2` _(move evento p/ outra categoria)_\n"
        "Ex2: `edit provas SO2 > trabalhos` _(faz a mesma coisa)_\n"
        "`edit CategoriaX EventoX DataX > CategoriaX EventoY DataX`\n"
        "Ex1: `edit provas SO2 23/11/2025 > provas LFA 23/11/2025` _(move data do eventoX p/ eventoY da mesma categoria)_\n"
        "Ex2: `edit provas SO2 23/11/2025 > provas LFA` _(faz a mesma coisa)_\n\n"
        
        "🔹 _Renomear + Mover (hibrido):_\n"
        "`edit CategoriaX EventoX > CategoriaY EventoY`\n"
        "Ex: `/edit provas SO2 > trabalhos SO3` _(move eventoX p/ outra categoriaY e renomeia o evento)_\n"
        "`edit CategoriaX EventoX DataX > CategoriaX EventoY DataX`\n"
        "Ex: `edit provas SO2 23/11/2025 > provas LFA 24/11/2025` _(move a data do eventoX para o eventoY e ainda renomeia a data)_\n\n"

        "🔹 _Alterar Dados (Data/Flags):_\n"
        "`edit Categoria Evento > -[flags]`\n"
        "Ex1: `/edit trabalhos SO2 > -alta` _(atribui prioridade alta p/ todas datas do evento)_\n"
        "Ex2: `/edit trabalhos SO2 > -obs \"Fazer\"` _(atribui observações p/ todas datas do evento)_\n"
        "Ex2: `/edit trabalhos SO2 > trabalhos SO2 -alta -obs \"Fazer\"` _(faz mesma coisa)_\n"
        "`edit Categoria Evento Data > -[flags]`\n"
        "Ex1: `/edit trabalhos SO2 23/11/2025 > -media` _(atribui prioridade media apenas p/ data especificada)_\n"
        "Ex2: `/edit trabalhos SO2 23/11/2025 > -obs \"Fazer\"` _(atribui observação apenas p/ data especificada)_\n"
        "Ex3: `/edit trabalhos SO2 23/11/2025 > trabalhos SO2 23/11/2025 -alta -obs \"Fazer\"` _(faz mesma coisa)_\n\n\n"

        "🗑️ *DELETAR:*\n"
        "`del Categoria`\n"
        "Ex: `del provas` _(apaga toda a categoria provas e seus componentes)_\n"
        "`del Cat Evento`\n"
        "Ex: `del provas SO2` _(apaga o Evento SO2 e todas suas datas)_\n"
        "`del Cat Evento Data`\n"
        "Ex: `del provas SO2 23/11/2025` _(apaga somente a data especificada)_\n\n"

        "👁️ *VISUAL & SISTEMA:*\n"
        "`list cat` _(lista as categorias existentes incluindo as vazias)_\n"
        "`list event` _(mostra o menu dos eventos)_\n"
        "`tree h` _(mostra arvore horizontal)_\n"
        "`tree v` ou `tree f` _(mostra arvore vertical)_\n"
        "`tree notify` _(mostra arvore formatada pra notificao colorida)_\n"
        "`alert -help` _(notificações comandos)_\n"
        "`export` _(faz backup em arquivo JSON e disponibiliza p/ download e libera API p/ uso)_\n"
        "`import` _(importar arquivo .json, valida, e atualiza o banco de dados)_\n"
        "`menu` _(abre menu)_\n\n"

        "🧩*INTEGRAÇÃO:*\n"
        "`link` _(mostra instruções para vincular conta com discord. Copiar dados telegram > discord)_"
    )
    kb = {"inline_keyboard": [[{"text": "🔙 Voltar ao Menu", "callback_data": "menu"}]]}
    send_tg(chat_id, texto, kb, msg_id)

# =========================================
#       3. LÓGICA AVANÇADA (EDIT/DEL)
# =========================================
# ... (Mantém a função process_complex_edit idêntica à versão anterior) ...
def process_complex_edit(chat_id, body):
    if '>' not in body:
        send_tg(chat_id, "⚠️ Use `>` para editar. Ex: `edit Provas > Trabalhos`")
        return
    parts = body.split('>')
    lhs_str, rhs_str = parts[0].strip(), parts[1].strip()
    args_lhs, _ = parse_cli_args(lhs_str)
    args_rhs, flags_rhs = parse_cli_args(rhs_str)
    if not args_lhs:
        send_tg(chat_id, "⚠️ Origem vazia.")
        return
    scope = "unknown"
    query = {"user_id": chat_id}
    lhs_date = None
    if len(args_lhs) >= 3:
        lhs_date = parse_smart_date(args_lhs[2])
    if len(args_lhs) == 1:
        scope = "category"
        query["tipo"] = regex_ci(args_lhs[0])
        desc = f"Categoria '{args_lhs[0]}'"
    elif len(args_lhs) == 2:
        scope = "event"
        query["tipo"] = regex_ci(args_lhs[0])
        query["materia"] = regex_ci(args_lhs[1])
        desc = f"Evento '{args_lhs[1]}'"
    elif len(args_lhs) >= 3 and lhs_date:
        scope = "item"
        query["tipo"] = regex_ci(args_lhs[0])
        query["materia"] = regex_ci(args_lhs[1])
        query["data"] = lhs_date.strftime("%d/%m/%Y")
        desc = f"Item de {query['data']}"
    else:
        send_tg(chat_id, f"⚠️ Origem inválida.")
        return
    count = db.provas.count_documents(query)
    if count == 0:
        send_tg(chat_id, f"🚫 Nada encontrado para: {desc}")
        return
    update_set = {}
    if scope == "category":
        if len(args_rhs) >= 1:
            new_cat = args_rhs[0].title()
            update_set["tipo"] = new_cat
            db.user_settings.update_one({"user_id": chat_id}, {"$addToSet": {"custom_cats": new_cat}}, upsert=True)
            db.user_settings.update_one({"user_id": chat_id}, {"$pull": {"custom_cats": args_lhs[0]}})
    elif scope == "event":
        if len(args_rhs) >= 1:
            update_set["tipo"] = args_rhs[0].title()
            db.user_settings.update_one({"user_id": chat_id}, {"$addToSet": {"custom_cats": update_set["tipo"]}}, upsert=True)
        if len(args_rhs) >= 2:
            update_set["materia"] = args_rhs[1]
    elif scope == "item":
        if len(args_rhs) >= 1:
            update_set["tipo"] = args_rhs[0].title()
            db.user_settings.update_one({"user_id": chat_id}, {"$addToSet": {"custom_cats": update_set["tipo"]}}, upsert=True)
        if len(args_rhs) >= 2:
            update_set["materia"] = args_rhs[1]
        if len(args_rhs) >= 3:
            new_date = parse_smart_date(args_rhs[2])
            if new_date: update_set["data"] = new_date.strftime("%d/%m/%Y")
    if flags_rhs.get("prio"): update_set["prioridade"] = flags_rhs["prio"]
    if flags_rhs.get("obs"): update_set["observacoes"] = flags_rhs["obs"]
    if not update_set:
        send_tg(chat_id, "⚠️ Nenhuma alteração detectada.")
        return
    res = db.provas.update_many(query, {"$set": update_set})
    send_tg(chat_id, f"✅ *Editado!* {res.modified_count} itens atualizados.")
    listar_agenda(chat_id)

# =========================================
#       4. PROCESSAMENTO
# =========================================

def get_state(user_id): return db.edit_states.find_one({"user_id": user_id})
def set_state(user_id, mode, step, temp_data=None, doc_id=None, prompt_msg_id=None):
    data = {"user_id": user_id, "mode": mode, "step": step, "temp_data": temp_data or {}, "updated_at": datetime.now()}
    if doc_id: data["doc_id"] = doc_id
    if prompt_msg_id: data["prompt_msg_id"] = prompt_msg_id
    db.edit_states.update_one({"user_id": user_id}, {"$set": data}, upsert=True)
def clear_state(user_id): db.edit_states.delete_many({"user_id": user_id})

def processar_texto(chat_id, text, msg_id):
    if not text: return
    text = text.strip()
    parts = text.split(maxsplit=1)
    cmd_raw = parts[0].lower()
    cmd = cmd_raw[1:] if cmd_raw.startswith("/") else cmd_raw
    body = parts[1].strip() if len(parts) > 1 else ""

    state = get_state(chat_id)
    
    if state and cmd not in ["start", "menu", "cancel", "ajuda", "help"]:
        # ... (Lógica do Wizard de Criação mantida) ...
        mode = state['mode']
        if mode == 'create':
            temp = state['temp_data']
            step = state['step']
            if step == 'cat_input':
                nova_cat = text.strip().title()
                all_cats = get_all_cats(chat_id)
                if nova_cat in all_cats:
                    send_tg(chat_id, f"⚠️ A categoria *{nova_cat}* já existe! Selecione ela no menu.")
                    return
                temp['tipo'] = nova_cat
                db.user_settings.update_one({"user_id": chat_id}, {"$addToSet": {"custom_cats": temp['tipo']}}, upsert=True)
                set_state(chat_id, 'create', 'materia', temp, prompt_msg_id=state.get('prompt_msg_id'))
                send_tg(chat_id, f"🏷️ Categoria: *{temp['tipo']}*\n✅ Digite o **NOME** da matéria:", msg_id=state.get('prompt_msg_id'))
                delete_msg(chat_id, msg_id)
            elif step == 'materia':
                temp['materia'] = text
                set_state(chat_id, 'create', 'data', temp)
                send_tg(chat_id, f"✅ Matéria: *{text}*\n📅 Digite a **DATA** (ex: 15/12):", msg_id=msg_id)
            elif step == 'data':
                dt_obj = parse_smart_date(text)
                now = get_brt_now().replace(hour=0,minute=0,second=0,microsecond=0)
                if not dt_obj or dt_obj < now:
                    send_tg(chat_id, "🚫 *Data Inválida ou Passada!*")
                    return 
                temp['data'] = dt_obj.strftime("%d/%m/%Y")
                set_state(chat_id, 'create', 'prio', temp)
                kb = {"inline_keyboard": [[{"text": "🚨 Alta", "callback_data": "wiz_prio:critical"}, {"text": "⚠️ Média", "callback_data": "wiz_prio:medium"}, {"text": "🟢 Baixa", "callback_data": "wiz_prio:low"}]]}
                send_tg(chat_id, f"📅 Data: *{temp['data']}*\nPrioridade:", kb, msg_id=msg_id)
            elif step == 'edit_val':
                doc_id = state['doc_id']
                field = state['temp_data']['field']
                val = text
                if field == 'data':
                    dt_obj = parse_smart_date(text)
                    now = get_brt_now().replace(hour=0,minute=0,second=0,microsecond=0)
                    if not dt_obj or dt_obj < now:
                        send_tg(chat_id, "🚫 Data inválida.")
                        return
                    val = dt_obj.strftime("%d/%m/%Y")
                db.provas.update_one({"_id": ObjectId(doc_id)}, {"$set": {field: val}})
                delete_msg(chat_id, msg_id)
                if state.get('prompt_msg_id'): delete_msg(chat_id, state.get('prompt_msg_id'))
                clear_state(chat_id)
                menu_item(chat_id, doc_id)
            return
        elif mode == 'confirm_del':
            clear_state(chat_id)
            send_tg(chat_id, "❌ Cancelado.")
            return

        elif mode == 'config_alert':
            # Lógica para separar Texto de Tempo e Chave
            # Ex input: "10s -K minha_chave"
            
            raw_text = text.strip()
            bypass_key = None
            time_str = raw_text

            # Verifica se tem a flag -K
            if "-K" in raw_text:
                try:
                    parts = raw_text.split("-K")
                    time_str = parts[0].strip() # "10s"
                    if len(parts) > 1:
                        bypass_key = parts[1].strip() # "minha_chave"
                except:
                    pass

            # Agora converte só a parte do tempo
            secs = parse_time_string(time_str)
            
            if not secs:
                send_tg(chat_id, "🚫 Formato de tempo inválido.")
                return
            
            # Verifica Admin
            is_admin = (Config.ADMIN_KEY and bypass_key == Config.ADMIN_KEY.strip())

            # Validações
            if not is_admin:
                if secs < 3600:
                    send_tg(chat_id, "⚠️ Mínimo: 1 hora.\n(Use a chave Admin para segundos)")
                    return
                if secs > 604800:
                    send_tg(chat_id, "⚠️ Máximo: 7 dias.")
                    return

            # Salva
            db.user_settings.update_one(
                {"user_id": chat_id}, 
                {"$set": {"periodic_interval": secs, "last_periodic_run": get_brt_now()}}, 
                upsert=True
            )
            
            if state.get('prompt_msg_id'): delete_msg(chat_id, state.get('prompt_msg_id'))
            delete_msg(chat_id, msg_id)
            clear_state(chat_id)
            
            send_tg(chat_id, f"✅ Frequência definida: *{format_seconds(secs)}*")
            menu_notificacao(chat_id)
            return

    # --- COMANDOS CLI ---
    if cmd == "add":
        args, flags = parse_cli_args(body)
        if not args: return enviar_ajuda(chat_id, eh_erro=True)
        cat = args[0].title()
        if len(args) == 1:
            all_cats = get_all_cats(chat_id)
            if cat in all_cats: send_tg(chat_id, f"⚠️ A categoria *{cat}* já existe.")
            else:
                db.user_settings.update_one({"user_id": chat_id}, {"$addToSet": {"custom_cats": cat}}, upsert=True)
                send_tg(chat_id, f"✅ Categoria *{cat}* criada.")
            listar_agenda(chat_id)
            return
        if len(args) < 3: return send_tg(chat_id, "⚠️ Use: `add Categoria Evento Data`")
        mat = args[1]
        dt_obj = parse_smart_date(args[2])
        now = get_brt_now()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if not dt_obj or dt_obj < today: return send_tg(chat_id, "🚫 Data inválida ou passada.")
        obs = flags["obs"] or (" ".join(args[3:]) if len(args) >= 4 else "")
        delta_days = (dt_obj.date() - today.date()).days
        is_imminent = delta_days <= 1
        db.provas.insert_one({"user_id": chat_id, "tipo": cat, "materia": mat, "data": dt_obj.strftime("%d/%m/%Y"), "prioridade": flags["prio"] or "low", "observacoes": obs, "sent_24h": is_imminent})
        db.user_settings.update_one({"user_id": chat_id}, {"$addToSet": {"custom_cats": cat}}, upsert=True)
        send_tg(chat_id, f"✅ Agendado: *{mat}*")
        listar_agenda(chat_id)
        if is_imminent:
            titulo = "🚨 *ATENÇÃO: É HOJE!* 🚨" if delta_days == 0 else "🚨 *ATENÇÃO: É AMANHÃ!* 🚨"
            cat_sing = singularize(cat)
            send_tg(chat_id, f"{titulo}\nO evento: *{mat}*\n📂 Categoria: {cat_sing}\n📅 Data: `{dt_obj.strftime('%d/%m/%Y')}`\nPrepare-se!")

    elif cmd == "edit": process_complex_edit(chat_id, body)
    elif cmd == "del":
        args, _ = parse_cli_args(body)
        if not args: return enviar_ajuda(chat_id, eh_erro=True)
        query = {"user_id": chat_id, "tipo": regex_ci(args[0])}
        desc = f"Categoria *{args[0]}*"
        if len(args) >= 2:
            query["materia"] = regex_ci(args[1])
            desc = f"Evento *{args[1]}*"
        if len(args) >= 3:
            d = parse_smart_date(args[2])
            if d: query["data"] = d.strftime("%d/%m/%Y")
        count = db.provas.count_documents(query)
        if count == 0: return send_tg(chat_id, "🚫 Nada encontrado.")
        set_state(chat_id, "confirm_del", "wait", temp_data={"query": query})
        kb = {"inline_keyboard": [[{"text": f"🔥 SIM, Apagar ({count})", "callback_data": "do_delete_cli"}], [{"text": "❌ Cancelar", "callback_data": "cancel_del"}]]}
        send_tg(chat_id, f"⚠️ Apagar {desc}? (Itens: {count})", kb)

    elif cmd == "tree":
        # Divide para pegar o argumento (notify, f, v, h)
        subcmd = body.split()[0].lower() if body else ""
        
        # Se não tiver argumento, retorna aviso
        if not subcmd:
            send_tg(chat_id, "⚠️ *Comando incompleto!*\nUse:\n`tree h` (Horizontal)\n`tree v` (Vertical)\n`tree notify` (Visualização de Alerta)")
            return

        provas = list(db.provas.find({"user_id": chat_id}).sort("data", 1))

        if subcmd == 'notify': 
            send_tg(chat_id, generate_ascii_tree(provas, 'smart'))
        elif subcmd in ['f', 'v']: # v de vertical, f de fixed (legado)
            send_tg(chat_id, gerar_painel(chat_id, provas, "vertical"))
        elif subcmd == 'h': # h de horizontal
            send_tg(chat_id, gerar_painel(chat_id, provas, "horizontal"))
        else:
            send_tg(chat_id, "⚠️ Opção inválida para tree. Use: `h`, `v` ou `notify`.")

    elif cmd == "list":
         sub = body.lower().strip()
         
         # Se digitar apenas /list ou list, manda aviso
         if not sub:
             send_tg(chat_id, "⚠️ *Comando incompleto!*\nUse:\n`list cat` (Ver categorias)\n`list event` (Ver todos eventos)")
             return

         if sub in ["cat", "cats", "categoria", "categorias"]:
             cats = get_all_cats(chat_id)
             if not cats: 
                 send_tg(chat_id, "📂 *Nenhuma categoria encontrada.*")
             else:
                 lines = ["📂 *Categorias Disponíveis:*"]
                 for c in cats:
                     # Conta quantos eventos existem nessa categoria
                     count = db.provas.count_documents({"user_id": chat_id, "tipo": c})
                     status_msg = f"{count} eventos" if count > 0 else "Vazia"
                     lines.append(f"• {c} _({status_msg})_")
                 
                 kb = {"inline_keyboard": [[{"text": "⚙️ Gerenciar", "callback_data": "manage_cats"}]]}
                 send_tg(chat_id, "\n".join(lines), kb)
         
         elif sub in ["event", "events", "evento", "eventos"]:
             # Chama a função que gera o painel com os eventos listados
             listar_agenda(chat_id)
         
         else:
             send_tg(chat_id, "⚠️ Opção desconhecida. Use `list cat` ou `list event`.")

    elif cmd == "list":
         sub = body.lower().strip()
         if sub in ["cat", "cats", "categoria", "categorias"]:
             cats = get_all_cats(chat_id)
             if not cats: send_tg(chat_id, "📂 *Nenhuma categoria encontrada.*")
             else:
                 lines = ["📂 *Categorias Disponíveis:*"]
                 for c in cats:
                     count = db.provas.count_documents({"user_id": chat_id, "tipo": c})
                     lines.append(f"• {c} _({count} itens)_")
                 kb = {"inline_keyboard": [[{"text": "⚙️ Gerenciar", "callback_data": "manage_cats"}]]}
                 send_tg(chat_id, "\n".join(lines), kb)
         else: listar_agenda(chat_id)

    # --- COMANDO ALERT REFINADO ---
    elif cmd == "alert":
        # --- ADICIONE ESTE BLOCO NO INÍCIO DO IF ALERT ---
        if "test" in body.lower():
            # 1. Pega configurações
            cfg = db.user_settings.find_one({"user_id": chat_id}) or {}
            mode = cfg.get("notify_mode", "smart")
            
            # 2. Busca tarefas
            all_tasks = list(db.provas.find({"user_id": chat_id}).sort("data", 1))
            
            if not all_tasks:
                send_tg(chat_id, "📭 Sem eventos para testar.")
                return

            # 3. Gera mensagem simulando o Worker
            lines = [f"🔔 *Teste de Notificação ({mode.title()})*", ""]
            
            # (Opcional) Aqui você poderia repetir a lógica de filtro do notifier.py
            # Mas para simplificar o teste visual, vamos mandar a Árvore Colorida direto
            
            lines.append("_Visualização da Árvore de Alerta:_")
            lines.append(generate_ascii_tree(all_tasks, mode=mode))
            
            send_tg(chat_id, "\n".join(lines))
            return
        # -------------------------------------------------

        if "desativar" in body.lower():
            db.user_settings.update_one({"user_id": chat_id}, {"$unset": {"periodic_interval": ""}})
            send_tg(chat_id, "🔕 Alertas desativados.")
            return
        
        # Help
        if "-help" in body or not body:
            if not body:
                # Se vazio mostra o menu, mas se user digitou -help mostra texto detalhado
                menu_notificacao(chat_id)
                return
            else:
                help_txt = (
                    "🔔 *CONFIGURAÇÃO DE ALERTAS*\n\n"
                    "⚙️ *Parâmetros Principais:*\n"
                    "`-f TEMPO`: Define a frequência.\n"
                    "   _Formatos: h (horas), m (min), d (dias)_\n"
                    "   _Limites: Min 1h | Max 7 dias_\n\n"
                    "`-mode MODO`: Define o que será notificado.\n"
                    "   `manual`: Resumo completo + Árvore completa.\n"
                    "   `smart`: Apenas Urgentes/Próximos + Árvore filtrada.\n\n"
                    "📝 *Exemplos de Tempo:*\n"
                    "• `/alert -f 12h` (A cada 12 horas)\n"
                    "• `/alert -f 1h30m` (Hora + Minuto)\n"
                    "• `/alert -f 1d` (Diário)\n\n"
                    "🔓 *Modo Desenvolvedor (Segundos):*\n"
                    "Para ignorar o limite de 1h, use `-K CHAVE`:\n"
                    "`/alert -f 3s -K a1b2c3...`\n\n"
                    "📌 *Exemplo Completo:*\n"
                    "`/alert -f 1h30m -mode smart`\n\n"
                    "*Ativar & Desativar:*\n"
                    "O alerta ativa automaticamente ao usar `/alert -f ...`\n"
                    "`/alert desativar`"
                )
                send_tg(chat_id, help_txt)
                return

        try:
            args = shlex.split(body)
        except:
            args = body.split()

        update_data = {}
        msg_log = []
        
        # Processamento de Frequência (-f)
        if "-f" in args:
            try:
                idx = args.index("-f") + 1
                if idx < len(args):
                    val = args[idx]
                    secs = parse_time_string(val)
                    
                    if secs:
                        # Lógica de Validação e Bypass
                        bypass_key = None
                        if "-K" in args:
                            k_idx = args.index("-K") + 1
                            if k_idx < len(args):
                                bypass_key = args[k_idx]
                        
                        is_admin = (bypass_key == Config.ADMIN_KEY) and Config.ADMIN_KEY
                        
                        # Limites
                        MIN_SEC = 3600 # 1h
                        MAX_SEC = 604800 # 7d
                        
                        if not is_admin:
                            if secs < MIN_SEC:
                                send_tg(chat_id, "⚠️ Mínimo de frequência: 1 hora.\nPara testes em segundos, contate o admin.")
                                return
                            if secs > MAX_SEC:
                                send_tg(chat_id, "⚠️ Máximo de frequência: 7 dias.")
                                return
                        
                        update_data["periodic_interval"] = secs
                        update_data["last_periodic_run"] = get_brt_now()
                        msg_log.append(f"Freq: {format_seconds(secs)}")
                    else:
                        send_tg(chat_id, "🚫 Formato de tempo inválido.")
                        return
            except Exception as e:
                print(e)
                pass

        # Processamento de Modo (-mode)
        if "-mode" in args:
            try:
                idx = args.index("-mode") + 1
                if idx < len(args):
                    m = args[idx].lower()
                    if m in ["smart", "inteligente"]:
                        update_data["notify_mode"] = "smart"
                        msg_log.append("Modo: Smart 🧠")
                    elif m in ["manual", "padrao", "all"]:
                        update_data["notify_mode"] = "manual"
                        msg_log.append("Modo: Manual 📋")
                    else:
                        send_tg(chat_id, "⚠️ Modo inválido. Use: `manual` ou `smart`.")
                        return
            except: pass

        if update_data:
            db.user_settings.update_one({"user_id": chat_id}, {"$set": update_data}, upsert=True)
            send_tg(chat_id, f"✅ Configurado! " + " | ".join(msg_log))
        else:
            # Se digitou flags mas nao setou nada util
            send_tg(chat_id, "⚠️ Nenhum parâmetro válido identificado.")

    elif cmd == "export":
        # 1. Recupera ou cria um token para o usuário
        user_cfg = db.user_settings.find_one({"user_id": chat_id})
        token = user_cfg.get("export_token")
        
        # Se não tiver token, cria um novo
        if not token:
            token = str(uuid.uuid4())
            db.user_settings.update_one({"user_id": chat_id}, {"$set": {"export_token": token}}, upsert=True)
        
        # 2. Monta o Link usando a URL Pública do Config
        link = f"{Config.API_PUBLIC_URL}/export/{token}"
        
        # 3. Busca os dados para o arquivo físico
        data = list(db.provas.find({"user_id": chat_id}, {"_id": 0, "user_id": 0, "sent_24h": 0}))
        
        if not data:
            send_tg(chat_id, "📭 *Sua agenda está vazia!*")
            return
        
        # 4. Mensagem com o Link
        msg_text = (
            "📦 *Backup & Integração API*\n\n"
            "📄 *Arquivo:* Seu backup em JSON está logo abaixo.\n"
            "🔗 *Link Dinâmico:* Use este link para integrar com Notion, Scriptable ou Apps de terceiros:\n\n"
            f"`{link}`\n\n"
            "⚠️ _Este link contém seus dados. Se vazar, clique em 'Revogar'._"
        )
        
        kb = {"inline_keyboard": [[{"text": "🔄 Revogar/Gerar Novo Token", "callback_data": "revoke_token"}]]}

        # Envia texto + link
        send_tg(chat_id, msg_text, kb)

        # Envia arquivo físico
        json_bytes = json.dumps(data, indent=4, ensure_ascii=False).encode('utf-8')
        try:
            requests.post(
                f"https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/sendDocument", 
                data={"chat_id": chat_id}, 
                files={"document": ("backup_agenda.json", json_bytes)}
            )
        except Exception as e:
            send_tg(chat_id, "❌ Erro ao enviar arquivo.")
            print(f"Erro export: {e}")

    elif cmd == "import":
        set_state(chat_id, "import_wait", "wait_file")
        msg = (
            "📥 *Importar Backup (.json)*\n\n"
            "Envie agora o arquivo `.json` gerado anteriormente pelo comando `/export`.\n"
            "⚠️ _Isso irá adicionar os eventos do arquivo à sua agenda atual._"
        )
        kb = {"inline_keyboard": [[{"text": "❌ Cancelar", "callback_data": "menu"}]]}
        send_tg(chat_id, msg, kb)
            
    elif cmd == "link":
        partners = get_partners(chat_id) # Pega lista de parceiros
        
        # A. STATUS (NOVO)
        if body.lower() == "status":
            if not partners:
                send_tg(chat_id, "🔓 **Status:** Conta Isolada (Sem vínculos).")
            else:
                lines = ["🔗 **Contas Vinculadas:**"]
                for p in partners:
                    lines.append(f"• ID: `{p}`")
                lines.append("\nPara remover uma específica, use:\n`/link desvincular ID`")
                send_tg(chat_id, "\n".join(lines))
            return

        # B. DESVINCULAR (ATUALIZADO)
        if "desvincular" in body.lower():
            parts = body.split()
            # Se o usuário digitou: /link desvincular 123456
            if len(parts) > 1 and parts[1].isdigit():
                target_id = parts[1]
                success, msg = unlink_specific(chat_id, target_id)
                send_tg(chat_id, msg)
            else:
                # Desvincular TUDO (Sair do grupo)
                msg = (
                    "⚠️ *Gerenciar Vínculos*\n\n"
                    f"Você possui {len(partners)} conexões.\n\n"
                    "1️⃣ Para remover **apenas uma conta**, digite:\n"
                    "`/link desvincular ID_DA_CONTA`\n"
                    "(Veja o ID usando `/link status`)\n\n"
                    "2️⃣ Para **sair de tudo** (desvincular-se totalmente):"
                )
                kb = {"inline_keyboard": [
                    [{"text": "🚫 Sair de TODAS as contas", "callback_data": "do_unlink_confirm"}],
                    [{"text": "🔙 Cancelar", "callback_data": "menu"}]
                ]}
                send_tg(chat_id, msg, kb)
            return

        # C. GERAR CÓDIGO (DISCORD)
        if body.lower() == "discord":
            # Verifica se já tem contas (aviso amigável)
            aviso_extra = ""
            if len(partners) > 0:
                aviso_extra = f"\n⚠️ _Nota: Você já tem {len(partners)} conta(s) vinculada(s). Este novo vínculo será adicionado ao grupo existente._"

            token = generate_link_code("telegram", chat_id)
            msg = (
                f"🔐 *Código de Vínculo Gerado*\n"
                f"`{token}`\n\n"
                f"1. Copie este código.\n"
                f"2. Vá no seu Bot do **Discord**.\n"
                f"3. Digite: `!link {token}`\n"
                f"_Válido por 5 minutos._"
                f"{aviso_extra}"
            )
            send_tg(chat_id, msg)
        
        # D. ENTRAR COM CÓDIGO (VALIDAÇÃO)
        elif body and body.lower() not in ["discord", "status", "desvincular"]:
            # Verifica se já tem contas antes (opcional, só info visual)
            token = body.strip()
            success, resp = validate_link_code(token, "telegram", chat_id)
            send_tg(chat_id, resp)
            
        # E. MENU AJUDA DO LINK
        else:
            msg = (
                "🔗 *Central de Vínculos*\n\n"
                "`/link discord` - Gerar código p/ conectar no Discord\n"
                "`/link status` - Ver contas conectadas\n"
                "`/link CÓDIGO` - Colar código vindo do Discord\n"
                "`/link desvincular` - Opções de remoção"
            )
            send_tg(chat_id, msg)

    elif cmd in ["start", "menu", "cancel"]:
        clear_state(chat_id)
        listar_agenda(chat_id)
    elif cmd in ["ajuda", "help"]: 
        enviar_ajuda(chat_id)
    else: 
        # Nova resposta curta para comandos errados
        send_tg(chat_id, "⚠️ *Comando ou sintaxe inválida!*\nUse o menu \"❓ Ajuda\" ou digite `/ajuda` / `/help`")


def processar_documento(chat_id, document, caption, msg_id):
    # Verifica se estava aguardando importação
    state = get_state(chat_id)
    if not state or state.get('mode') != 'import_wait':
        send_tg(chat_id, "⚠️ Para importar um backup, digite `/import` primeiro.", msg_id=msg_id)
        return

    if not document.get("file_name", "").endswith(".json"):
        send_tg(chat_id, "🚫 Formato inválido. Envie um arquivo **.json**.", msg_id=msg_id)
        return

    send_tg(chat_id, "⏳ Lendo arquivo...")

    try:
        # 1. Baixar e Ler
        file_id = document["file_id"]
        r_path = requests.get(f"https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/getFile?file_id={file_id}")
        file_path = r_path.json()["result"]["file_path"]
        r_content = requests.get(f"https://api.telegram.org/file/bot{Config.TELEGRAM_TOKEN}/{file_path}")
        content_utf8 = r_content.content.decode('utf-8')
        data_import = json.loads(content_utf8)
        
        if not isinstance(data_import, list):
            send_tg(chat_id, "🚫 Erro: O JSON deve ser uma lista.")
            return

        # 2. Validar Itens
        valid_items = []
        for item in data_import:
            if "materia" not in item or "data" not in item: continue
            
            # Sanitiza o item para inserção futura
            new_item = item.copy()
            if "_id" in new_item: del new_item["_id"]
            new_item["user_id"] = chat_id
            new_item["origin"] = "import_json"
            if "tipo" not in new_item: new_item["tipo"] = "Geral"
            if "prioridade" not in new_item: new_item["prioridade"] = "low"
            if "observacoes" not in new_item: new_item["observacoes"] = "" # Garante campo vazio se não tiver
            
            valid_items.append(new_item)

        if not valid_items:
            send_tg(chat_id, "🚫 Nenhum item válido encontrado.")
            clear_state(chat_id)
            return

        # 3. Salva TEMPORARIAMENTE no estado (MongoDB) para decisão do usuário
        # (Como são poucos itens, cabe tranquilo no documento do Mongo)
        set_state(chat_id, "import_confirm", "wait_decision", temp_data={"items": valid_items})

        # 4. Pergunta ao Usuário
        msg = (
            f"📦 **Arquivo Analisado!**\n"
            f"Encontrei {len(valid_items)} eventos válidos.\n\n"
            "Como deseja prosseguir?"
        )
        
        kb = {"inline_keyboard": [
            [{"text": "🔥 SUBSTITUIR TUDO (Apagar atual)", "callback_data": "import_do:replace"}],
            [{"text": "➕ MESCLAR (Ignorar duplicados)", "callback_data": "import_do:merge"}],
            [{"text": "❌ Cancelar", "callback_data": "menu"}]
        ]}
        
        send_tg(chat_id, msg, kb)

    except Exception as e:
        print(f"Erro Import: {e}")
        send_tg(chat_id, "❌ Erro crítico ao processar o arquivo.")
        clear_state(chat_id)
        
# =========================================
#       5. INTERFACE (CALLBACKS)
# =========================================

def menu_item(chat_id, doc_id, msg_id=None):
    doc = db.provas.find_one({"_id": ObjectId(doc_id)})
    if not doc: return listar_agenda(chat_id, msg_id)
    prio_map = {"critical": "Alta 🚨", "medium": "Média ⚠️", "low": "Baixa 🟢"}
    texto = (
        f"📝 *Editando: {doc['materia']}*\n"
        f"📅 Data: `{doc['data']}`\n"
        f"📂 Categoria: *{doc.get('tipo', 'Geral')}*\n"
        f"📊 Prio: *{prio_map.get(doc.get('prioridade'), 'Baixa')}*\n"
        f"👀 Obs: {doc.get('observacoes', '-')}\n"
    )
    kb = {"inline_keyboard": [
        [{"text": "📝 Nome", "callback_data": f"editf:materia:{doc_id}"},
         {"text": "📅 Data", "callback_data": f"editf:data:{doc_id}"}],
        [{"text": "📂 Categoria", "callback_data": f"edit_type_init:{doc_id}"},
         {"text": "📊 Prio", "callback_data": f"edit_prio_menu:{doc_id}"}],
        [{"text": "👀 Observação", "callback_data": f"editf:observacoes:{doc_id}"}],
        [{"text": "🗑️ EXCLUIR", "callback_data": f"quick_del_ask:{doc_id}"}],
        [{"text": "🔙 Voltar", "callback_data": "manage_mode:edit"}]
    ]}
    send_tg(chat_id, texto, kb, msg_id)

def menu_notificacao(chat_id, msg_id=None):
    cfg = db.user_settings.find_one({"user_id": chat_id})
    interval = cfg.get("periodic_interval", 0) if cfg else 0
    
    mode_raw = cfg.get("notify_mode", "smart")
    mode_display = "🧠 Smart" if mode_raw == "smart" else "📋 Manual"
    status_intervalo = format_seconds(interval) if interval > 0 else "Desativado"
    
    texto = (
        f"🔔 *Configuração de Alertas*\n\n"
        f"⏱ Frequência: *{status_intervalo}*\n"
        f"⚙️ Modo Atual: *{mode_display}*\n\n"
        f"🧠 _Smart:_ Foca no essencial. Notifica eventos de prioridades Alta/Média nos quais sao definidos automaticamente de forma dinâmica (onde _Data Evento_ - _Hoje_ ≤ 7 dias >> alta && (onde _Data Evento_ - _Hoje_ ≤ 30 dias >> media).\n"
        f"📋 _Manual:_ Relatório completo. Lista todos os eventos futuros da sua agenda (incluindo os de baixa prioridade), independente do prazo.\n\n"
        f"💡 _Dica: Use_ `/alert -help` _para opções avançadas._"
    )
    
    kb = {"inline_keyboard": [
        [{"text": "6h", "callback_data": "set_cycle:21600"}, {"text": "12h", "callback_data": "set_cycle:43200"}],
        [{"text": "Diário", "callback_data": "set_cycle:86400"}, {"text": "🔕 Desativar", "callback_data": "set_cycle:0"}],
        [{"text": "✍️ Personalizar Tempo", "callback_data": "manual_freq_ask"}],
        [{"text": f"🔀 Alternar para {'Manual' if mode_raw == 'smart' else 'Smart'}", "callback_data": "toggle_notify_mode"}],
        # NOVO BOTÃO DE TESTE
        [{"text": "🔔 Testar Envio Agora", "callback_data": "test_notify"}],
        [{"text": "🔙 Voltar", "callback_data": "menu"}]
    ]}
    send_tg(chat_id, texto, kb, msg_id)

def processar_botao(chat_id, data, msg_id):
    if data == "menu":
        clear_state(chat_id)
        # Chama o listar_agenda simplificado (sem delete_mode)
        listar_agenda(chat_id, msg_id)
    elif data == "menu_del_mode":
        clear_state(chat_id)
        listar_agenda(chat_id, msg_id, delete_mode=True)
    
    # 1. Entrar no menu gerenciar (Padrão: Edit)
    elif data == "manage_init":
        menu_gerenciar(chat_id, mode="edit", msg_id=msg_id)

    # 2. Alternar o modo (Edit <-> Del)
    elif data.startswith("manage_mode:"):
        new_mode = data.split(":")[1]
        menu_gerenciar(chat_id, mode=new_mode, msg_id=msg_id)

    # 3. Ajuste no retorno da deleção (opcional, para não voltar pro menu principal direto)
    elif data.startswith("quick_del_do:"):
        doc_id = data.split(":")[1]
        db.provas.delete_one({"_id": ObjectId(doc_id)})
        menu_gerenciar(chat_id, mode="del", msg_id=msg_id)

    elif data.startswith("manage_del_ask:"):
        doc_id = data.split(":")[1]
        doc = db.provas.find_one({"_id": ObjectId(doc_id)})
        if not doc: return menu_gerenciar(chat_id, mode="del", msg_id=msg_id)
        
        txt = f"🗑️ *Confirmar Exclusão?*\n{doc['materia']} ({doc['data']})"
        
        # Botão SIM vai para um novo 'do'
        # Botão NÃO volta para 'manage_mode:del' (o segredo está aqui)
        kb = {"inline_keyboard": [
            [{"text": "🔥 SIM, APAGAR", "callback_data": f"manage_del_do:{doc_id}"}],
            [{"text": "🔙 Não (Voltar)", "callback_data": "manage_mode:del"}] 
        ]}
        send_tg(chat_id, txt, kb, msg_id)

    elif data.startswith("manage_del_do:"):
        doc_id = data.split(":")[1]
        db.provas.delete_one({"_id": ObjectId(doc_id)})
        # Força o retorno para o modo delete
        menu_gerenciar(chat_id, mode="del", msg_id=msg_id)

    elif data == "wiz_init":
        all_cats = get_all_cats(chat_id)
        buttons = [{"text": f"📂 {c}", "callback_data": f"wiz_cat:{c}"} for c in all_cats]
        rows = create_grid(buttons, cols=3)
        kb = {"inline_keyboard": rows}
        kb["inline_keyboard"].append([{"text": "✨ Nova Categoria...", "callback_data": "wiz_cat:NEW"}])
        kb["inline_keyboard"].append([{"text": "⚙️ Gerenciar Categorias", "callback_data": "manage_cats"}])
        kb["inline_keyboard"].append([{"text": "❌ Cancelar", "callback_data": "menu"}])
        send_tg(chat_id, "🆕 *Adicionar Evento*\nEscolha a Categoria:", kb, msg_id)
    
    elif data.startswith("wiz_cat:"):
        escolha = data.split(":")[1]
        if escolha == "NEW":
            kb = {"inline_keyboard": [[{"text": "❌ Cancelar", "callback_data": "menu"}]]}
            p = send_tg(chat_id, "✨ Digite o nome da *Nova Categoria*:", kb)
            set_state(chat_id, "create", "cat_input", temp_data={}, prompt_msg_id=p)
        else:
            set_state(chat_id, "create", "materia", temp_data={"tipo": escolha})
            kb = {"inline_keyboard": [[{"text": "❌ Cancelar", "callback_data": "menu"}]]}
            send_tg(chat_id, f"📂 Categoria: *{escolha}*\n⌨️ Digite o nome da **Matéria**:", kb, msg_id)
    
    elif data.startswith("wiz_prio:"):
        prio = data.split(":")[1]
        st = get_state(chat_id)
        if st:
            temp = st['temp_data']
            dt_obj = parse_smart_date(temp['data'])
            now = get_brt_now()
            delta_days = (dt_obj.date() - now.date()).days
            is_imminent = delta_days <= 1

            db.provas.insert_one({
                "user_id": chat_id, "materia": temp['materia'], 
                "data": temp['data'], "prioridade": prio, 
                "observacoes": "", "tipo": temp.get('tipo', 'Geral'),
                "sent_24h": is_imminent
            })
            clear_state(chat_id)
            delete_msg(chat_id, msg_id)
            send_tg(chat_id, f"✅ Agendado: *{temp['materia']}*")
            listar_agenda(chat_id, None)

            if is_imminent:
                titulo = "🚨 *ATENÇÃO: É HOJE!* 🚨" if delta_days == 0 else "🚨 *ATENÇÃO: É AMANHÃ!* 🚨"
                cat_sing = singularize(temp.get('tipo', 'Geral'))
                send_tg(chat_id, f"{titulo}\nO evento: *{temp['materia']}*\n📂 Categoria: {cat_sing}\n📅 Data: `{temp['data']}`\nPrepare-se!")

    elif data == "manage_cats":
        all_cats = get_all_cats(chat_id)
        kb = {"inline_keyboard": []}
        if not all_cats:
            answer_callback(data, "Nenhuma categoria.")
        else:
            for c in all_cats:
                kb["inline_keyboard"].append([{"text": f"🗑️ {c}", "callback_data": f"del_cat_ask:{c}"}])
        kb["inline_keyboard"].append([{"text": "🔙 Voltar", "callback_data": "wiz_init"}])
        send_tg(chat_id, "⚙️ *Apagar Categorias*\n(Remove da lista e apaga eventos associados!)", kb, msg_id)

    elif data.startswith("del_cat_ask:"):
        cat = data.split(":")[1]
        count = db.provas.count_documents({"user_id": chat_id, "tipo": cat})
        msg = f"⚠️ *Apagar Categoria '{cat}'?*\nItens vinculados: {count}"
        if count == 0: msg += "\n(Categoria vazia, será removida da lista)"
        kb = {"inline_keyboard": [
            [{"text": "🔥 CONFIRMAR EXCLUSÃO", "callback_data": f"del_cat_do:{cat}"}],
            [{"text": "🔙 Cancelar", "callback_data": "manage_cats"}]
        ]}
        send_tg(chat_id, msg, kb, msg_id)

    elif data.startswith("del_cat_do:"):
        cat = data.split(":")[1]
        res = db.provas.delete_many({"user_id": chat_id, "tipo": cat})
        db.user_settings.update_one({"user_id": chat_id}, {"$pull": {"custom_cats": cat}})
        send_tg(chat_id, f"🗑️ Categoria *{cat}* removida ({res.deleted_count} eventos apagados).")
        processar_botao(chat_id, "manage_cats", None)

    elif data.startswith("open:"): menu_item(chat_id, data.split(":")[1], msg_id)

    elif data.startswith("quick_del_ask:"):
        doc_id = data.split(":")[1]
        doc = db.provas.find_one({"_id": ObjectId(doc_id)})
        if not doc: return listar_agenda(chat_id, msg_id)
        txt = f"🗑️ *Tem certeza?*\nApagar: {doc['materia']} ({doc['data']})"
        kb = {"inline_keyboard": [[{"text": "🔥 SIM, APAGAR", "callback_data": f"quick_del_do:{doc_id}"}], [{"text": "🔙 Não", "callback_data": f"open:{doc_id}"}]]}
        send_tg(chat_id, txt, kb, msg_id)

    elif data.startswith("edit_type_init:"):
        doc_id = data.split(":")[1]
        cats = get_all_cats(chat_id)
        btns = [{"text": c, "callback_data": f"set_edit_cat:{doc_id}:{c}"} for c in cats]
        kb = {"inline_keyboard": create_grid(btns, 2)}
        kb["inline_keyboard"].append([{"text": "🔙 Voltar", "callback_data": f"open:{doc_id}"}])
        send_tg(chat_id, "📂 Escolha a nova Categoria:", kb, msg_id)

    elif data.startswith("set_edit_cat:"):
        _, doc_id, new_cat = data.split(":")
        db.provas.update_one({"_id": ObjectId(doc_id)}, {"$set": {"tipo": new_cat}})
        menu_item(chat_id, doc_id, msg_id)

    elif data.startswith("edit_prio_menu:"):
        doc_id = data.split(":")[1]
        kb = {"inline_keyboard": [
            [{"text": "🚨 Alta", "callback_data": f"set_edit_prio:{doc_id}:critical"},
             {"text": "⚠️ Média", "callback_data": f"set_edit_prio:{doc_id}:medium"},
             {"text": "🟢 Baixa", "callback_data": f"set_edit_prio:{doc_id}:low"}],
            [{"text": "🔙 Voltar", "callback_data": f"open:{doc_id}"}]
        ]}
        send_tg(chat_id, "📊 Escolha a Prioridade:", kb, msg_id)

    elif data.startswith("set_edit_prio:"):
        _, doc_id, prio = data.split(":")
        db.provas.update_one({"_id": ObjectId(doc_id)}, {"$set": {"prioridade": prio}})
        menu_item(chat_id, doc_id, msg_id)

    elif data.startswith("editf:"):
        _, field, doc_id = data.split(":")
        p = send_tg(chat_id, f"✍️ Digite o novo valor para *{field}*:", {"inline_keyboard":[[{"text":"❌ Cancelar", "callback_data":f"open:{doc_id}"}]]})
        set_state(chat_id, "create", "edit_val", temp_data={"field": field}, doc_id=doc_id, prompt_msg_id=p)

    elif data == "do_delete_cli":
        st = get_state(chat_id)
        if st and st['mode'] == 'confirm_del':
            db.provas.delete_many(st['temp_data']['query'])
            clear_state(chat_id)
            delete_msg(chat_id, msg_id)
            send_tg(chat_id, "🗑️ Itens apagados.")
            listar_agenda(chat_id)
            
    elif data == "cancel_del":
        clear_state(chat_id)
        delete_msg(chat_id, msg_id)
        send_tg(chat_id, "Cancelado.")
        
    elif data == "toggle_layout":
        toggle_user_layout(chat_id)
        listar_agenda(chat_id, msg_id)

    elif data == "notify_menu": menu_notificacao(chat_id, msg_id)
    
    elif data.startswith("set_cycle:"):
        s = int(data.split(":")[1])
        if s==0: db.user_settings.update_one({"user_id": chat_id}, {"$unset": {"periodic_interval": ""}})
        else: db.user_settings.update_one({"user_id": chat_id}, {"$set": {"periodic_interval": s, "last_periodic_run": get_brt_now()}}, upsert=True)
        menu_notificacao(chat_id, msg_id)

    elif data == "toggle_notify_mode":
        cfg = db.user_settings.find_one({"user_id": chat_id})
        current_mode = cfg.get("notify_mode", "smart")
        
        # Inverte o modo
        new_mode = "manual" if current_mode == "smart" else "smart"
        
        db.user_settings.update_one(
            {"user_id": chat_id}, 
            {"$set": {"notify_mode": new_mode}}, 
            upsert=True
        )
        # Recarrega o menu com o texto atualizado
        menu_notificacao(chat_id, msg_id)

    elif data == "manual_freq_ask":
        msg = (
            "✍️ *Definir Frequência Personalizada*\n\n"
            "Digite o tempo desejado:\n"
            "_Minimo 1h | Máximo 7 dias_\n\n"
            "• `1h`\n"
            "• `1h 30m`\n"
            "• `2d`\n\n"
            "🔐 *Admin (Segundos liberado):*\n"
            "Use: `TEMPO -K CHAVE_SIMETRICA`\n"
            "Ex: `10s -K a1b2c3...`"
        )
        kb = {"inline_keyboard": [[{"text": "🔙 Cancelar", "callback_data": "notify_menu"}]]}
        p = send_tg(chat_id, msg, kb)
        set_state(chat_id, "config_alert", "wait_input", prompt_msg_id=p)

    # --- LÓGICA DO TESTE DE NOTIFICAÇÃO ---
    elif data == "test_notify":
        cfg = db.user_settings.find_one({"user_id": chat_id})
        mode = cfg.get("notify_mode", "smart")
        
        all_tasks = list(db.provas.find({"user_id": chat_id}))
        now = get_brt_now()
        
        if not all_tasks:
            answer_callback(chat_id, "Sem eventos para notificar.")
            return

        grouped_tasks = {}
        total_items = 0

        for t in all_tasks:
            d = parse_smart_date(t.get('data', ''))
            if not d: continue
            
            delta_days = (d.date() - now.date()).days
            if delta_days < 0: continue 

            prio = t.get('prioridade', 'low')
            
            include = False
            if mode == 'manual':
                include = True
            else:
                # --- MUDANÇA 1: Lógica Smart Expandida ---
                # Inclui se for Crítico/Médio OU se faltar 30 dias ou menos
                if prio in ['critical', 'medium'] or delta_days <= 30:
                    include = True
            
            if include:
                cat = t.get('tipo', 'Geral')
                if cat not in grouped_tasks: grouped_tasks[cat] = []
                grouped_tasks[cat].append((d, t, delta_days))
                total_items += 1

        if total_items == 0:
            send_tg(chat_id, f"🔕 *Teste ({mode.title()}):* Nenhum evento nos critérios (30 dias/Urgente).")
            return

        lines = [f"🔔 *Teste de Notificação ({mode.title()})*", ""]
        
        for cat in sorted(grouped_tasks.keys()):
            lines.append(f"📂 *{cat}*")
            items = sorted(grouped_tasks[cat], key=lambda x: x[0])
            
            for d, t, dias in items:
                prio_raw = t.get('prioridade', 'low')
                
                # --- MUDANÇA 2: Ícones condizentes com a Árvore ---
                if prio_raw == 'critical' or dias <= 7:
                    ico = "🚨" # Vermelho
                elif prio_raw == 'medium' or dias <= 30:
                    ico = "⚠️" # Laranja
                else:
                    ico = "🔹" # Verde/Azul (Só aparece no Manual ou se for tag Alta longe)
                
                t_str = "HOJE 🔥" if dias == 0 else ("AMANHÃ" if dias == 1 else f"em {dias}d")
                lines.append(f"{ico} {t['materia']}: {t['data']} ({t_str})")
            
            lines.append("")

        lines.append(generate_ascii_tree(all_tasks, mode=mode))
        send_tg(chat_id, "\n".join(lines))
        
    elif data == "ajuda": enviar_ajuda(chat_id, msg_id=msg_id)

    elif data == "revoke_token":
        new_token = str(uuid.uuid4())
        # Atualiza no banco
        db.user_settings.update_one({"user_id": chat_id}, {"$set": {"export_token": new_token}})
        
        new_link = f"{Config.API_PUBLIC_URL}/export/{new_token}"
        
        msg = (
            "✅ *Token Revogado!*\n"
            "O link antigo foi desativado.\n\n"
            "🔑 *Novo Link:*\n"
            f"`{new_link}`"
        )
        send_tg(chat_id, msg)
        
    elif data == "do_unlink_confirm":
        success, msg = unlink_account(chat_id)
        # Remove os botões da mensagem anterior para ficar limpo
        delete_msg(chat_id, msg_id) 
        send_tg(chat_id, msg)
        # Volta pro menu principal
        listar_agenda(chat_id)

    elif data.startswith("import_do:"):
        action = data.split(":")[1]
        state = get_state(chat_id)
        
        # Segurança: Verifica se tem dados salvos no estado
        if not state or "items" not in state.get("temp_data", {}):
            send_tg(chat_id, "⚠️ Sessão expirada. Envie o arquivo novamente via `/import`.")
            return

        items_to_import = state["temp_data"]["items"]
        
        if action == "replace":
            # 1. MODO SUBSTITUIR: Apaga tudo e insere
            db.provas.delete_many({"user_id": chat_id})
            db.provas.insert_many(items_to_import)
            
            # Atualiza categorias
            db.user_settings.update_one({"user_id": chat_id}, {"$set": {"custom_cats": []}}) # Reseta cats antigas
            for it in items_to_import:
                 db.user_settings.update_one({"user_id": chat_id}, {"$addToSet": {"custom_cats": it["tipo"]}}, upsert=True)

            send_tg(chat_id, f"✅ **Sucesso!**\nSua agenda foi totalmente substituída por {len(items_to_import)} novos eventos.", msg_id=msg_id)

        elif action == "merge":
            # 2. MODO MESCLAR: Verifica duplicidade INTELIGENTE (Tipo + Materia + Data + OBS)
            
            # Busca eventos existentes
            existing = db.provas.find({"user_id": chat_id})
            existing_sigs = set()
            
            for doc in existing:
                # Assinatura Única: Inclui OBSERVAÇÕES agora!
                sig = (
                    str(doc.get("tipo", "")).strip().lower(),
                    str(doc.get("materia", "")).strip().lower(),
                    str(doc.get("data", "")).strip(),
                    str(doc.get("observacoes", "")).strip().lower() # <--- Correção aqui
                )
                existing_sigs.add(sig)
            
            final_list = []
            duplicates = 0
            
            for item in items_to_import:
                # Cria assinatura do item novo
                item_sig = (
                    str(item["tipo"]).strip().lower(),
                    str(item["materia"]).strip().lower(),
                    str(item["data"]).strip(),
                    str(item["observacoes"]).strip().lower()
                )
                
                if item_sig in existing_sigs:
                    duplicates += 1
                else:
                    final_list.append(item)
                    existing_sigs.add(item_sig) # Evita duplicação interna no próprio JSON

            if final_list:
                db.provas.insert_many(final_list)
                # Atualiza cats
                for it in final_list:
                     db.user_settings.update_one({"user_id": chat_id}, {"$addToSet": {"custom_cats": it["tipo"]}}, upsert=True)

            send_tg(chat_id, f"✅ **Mesclagem Concluída!**\n📥 {len(final_list)} novos adicionados.\n♻️ {duplicates} já existiam (ignorados).", msg_id=msg_id)

        # Limpa o estado e mostra a agenda
        clear_state(chat_id)
        listar_agenda(chat_id)

def rabbit_callback(ch, method, properties, body):
    try:
        msg = json.loads(body)

        # --- AVISO DE SPAM DINÂMICO ---
        if msg.get("action") == "spam_warning":
            tempo_bloqueio = msg.get("duration", 10)
            nivel = msg.get("level", 1)
            
            # Texto base
            header = "🚫 *SPAM DETECTADO!* 🛑"
            
            if nivel == 1:
                # Aviso inicial
                corpo = (
                    "Você está enviando mensagens muito rápido.\n"
                    f"Aguarde *{tempo_bloqueio} segundos* para continuar."
                )
            else:
                # Aviso de reincidência (Pena aumentada)
                corpo = (
                    f"⚠️ *Infração Nível {nivel}*\n"
                    "Você continuou enviando spam!\n\n"
                    f"⏳ Sua penalidade aumentou para: *{tempo_bloqueio} segundos*.\n"
                    "_Fique 5 minutos sem spam para resetar sua pena._"
                )

            texto_final = f"{header}\n\n{corpo}"
            
            send_tg(msg["chat_id"], texto_final)
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return
        # ------------------------------

        raw = msg.get("raw_update", {})
        # 1. Trata Botões
        if "callback_query" in raw:
            cb = raw["callback_query"]
            answer_callback(cb["id"])
            processar_botao(cb["message"]["chat"]["id"], cb["data"], cb["message"]["message_id"])
        
        # 2. Trata Mensagens
        elif "message" in raw:
            m = raw["message"]
            chat_id = m["chat"]["id"]
            msg_id = m["message_id"]

            # >>>> MUDANÇA AQUI: Verifica se é Documento <<<<
            if "document" in m:
                caption = m.get("caption", "")
                processar_documento(chat_id, m["document"], caption, msg_id)
            
            # >>>> Verifica se é Texto <<<<
            elif "text" in m:
                processar_texto(chat_id, m["text"], msg_id)
        
        ch.basic_ack(delivery_tag=method.delivery_tag)
    except Exception as e:
        print(f"❌ Erro Worker: {e}")
        ch.basic_ack(delivery_tag=method.delivery_tag)

while True:
    try:
        creds = pika.PlainCredentials(Config.RABBIT_USER, Config.RABBIT_PASS)
        conn = pika.BlockingConnection(pika.ConnectionParameters(host=Config.RABBIT_HOST, credentials=creds))
        ch = conn.channel()
        ch.queue_declare(queue=Config.QUEUE_NAME, durable=True)
        ch.basic_consume(queue=Config.QUEUE_NAME, on_message_callback=rabbit_callback)
        print("🚀 Worker Conectado!", flush=True)
        ch.start_consuming()
    except: time.sleep(5)