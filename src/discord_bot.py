# --- START OF FILE src/discord_bot.py ---
import discord
import os
import shlex
import re
import json
import io
from datetime import datetime, timedelta
from discord.ext import commands
from src.database import db
from src.config import Config
from src.utils import (
    parse_smart_date, parse_cli_args, parse_time_string,
    format_seconds, singularize, generate_link_code, 
    validate_link_code, get_linked_ids, unlink_account, 
    get_partners, unlink_specific
)

# Configurações
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix=["!", "/"], intents=intents, help_command=None)

# --- UTILITÁRIOS ---
def get_brt_now():
    return datetime.utcnow() - timedelta(hours=3)

def regex_ci(value):
    """Cria regex Case Insensitive para o Mongo"""
    return {"$regex": f"^{re.escape(str(value).strip())}$", "$options": "i"}

async def send_chunked_message(ctx, text):
    """
    Envia mensagens longas (>2000 chars) dividindo-as em pedaços,
    preservando a formatação de bloco de código (ansi/ini).
    """
    if len(text) <= 2000:
        await ctx.send(text)
        return

    # Detecta o tipo de bloco de código (ansi ou ini)
    lines = text.split('\n')
    block_type = ""
    
    # Remove as crases iniciais e finais para processar o miolo
    clean_lines = []
    if lines[0].startswith("```"):
        block_type = lines[0].replace("```", "").strip()
        lines = lines[1:] # Remove primeira linha
    
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1] # Remove ultima linha

    # Monta os chunks
    current_chunk = f"```{block_type}\n"
    
    for line in lines:
        # Verifica se adicionar a próxima linha estoura o limite
        # 2000 - len(```) - margem de segurança
        if len(current_chunk) + len(line) + 5 > 1990:
            current_chunk += "```"
            await ctx.send(current_chunk)
            # Reinicia o chunk com o cabeçalho
            current_chunk = f"```{block_type}\n{line}\n"
        else:
            current_chunk += f"{line}\n"

    # Envia o restante se houver
    if len(current_chunk) > len(f"```{block_type}\n"):
        current_chunk += "```"
        await ctx.send(current_chunk)

def generate_discord_tree(tasks, mode='v', notify_mode='smart'):
    if not tasks: return None, "📭 *Agenda vazia.*"

    now = get_brt_now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    
    dados = {}
    for p in tasks:
        tipo = p.get('tipo', 'Geral')
        if tipo not in dados: dados[tipo] = {}
        if p['materia'] not in dados[tipo]: dados[tipo][p['materia']] = []
        dados[tipo][p['materia']].append(p)

    lines = []
    logo_output = None

    # --- MODO NOTIFY (ANSI COLORIDO) ---
    if mode == 'notify':
        ESC = "\u001b["
        RESET = f"{ESC}0m"
        
        # CORES ATUALIZADAS
        COR_TITULO    = f"{ESC}1;37m" # BRANCO
        COR_TAG_TEXT  = f"{ESC}1;37m" # BRANCO
        
        COR_ESTRUTURA = f"{ESC}0;34m" # Azul Escuro
        COR_MATERIA   = f"{ESC}1;35m" # Roxo
        
        COR_URGENTE   = f"{ESC}1;31m" # Vermelho
        COR_MEDIO     = f"{ESC}1;33m" # Amarelo
        COR_BAIXO     = f"{ESC}0;34m" # Azul

        arte = r"""
 ___  ___  ________ ________  ________  _________
|\  \|\  \|\  _____\\   ____\|\   __  \|\___   ___\
\ \  \\\  \ \  \__/\ \  \___|\ \  \|\  \|___ \  \_|
 \ \  \\\  \ \   __\\ \  \    \ \   __  \   \ \  \
  \ \  \\\  \ \  \_| \ \  \____\ \  \ \  \   \ \  \
   \ \_______\ \__\   \ \_______\ \__\ \__\   \ \__\
    \|_______|\|__|    \|_______|\|__|\|__|    \|__|
"""
        logo_output = f"```ansi\n{COR_MATERIA}{arte}{RESET}\n```"
        lines.append("```ansi")
        
        tipos = sorted(dados.keys())
        priority = ["Provas", "Trabalhos"]
        tipos = priority + [x for x in tipos if x not in priority]
        tipos = [t for t in tipos if t in dados]

        for tipo in tipos:
            lines.append(f"\n{COR_TITULO}: : {tipo} : :{RESET}")
            materias = sorted(dados[tipo].keys())
            for j, materia in enumerate(materias):
                prefix = "└──" if j == len(materias)-1 else "├──"
                lines.append(f"{COR_ESTRUTURA}{prefix} {RESET}{COR_MATERIA}{materia}{RESET}")
                docs = sorted(dados[tipo][materia], key=lambda x: parse_smart_date(x['data']) or datetime.max)
                indent = "    " if prefix == "└──" else "│   "
                
                for k, d in enumerate(docs):
                    dt_obj = parse_smart_date(d['data'])
                    delta_days = (dt_obj - today).days if dt_obj else 999
                    prio = d.get('prioridade', 'low')
                    
                    obs = d.get('observacoes', '')
                    obs_str = f"({obs}) " if obs else ""
                    
                    # Definição do Texto da TAG
                    tag_text = "[LOW]"
                    if prio == 'critical': tag_text = "[URG]"
                    elif prio == 'medium': tag_text = "[MED]"

                    # Definição da COR da DATA
                    final_color = COR_BAIXO # Padrão Azul
                    
                    if prio == 'critical': 
                        final_color = COR_URGENTE
                    elif prio == 'medium': 
                        final_color = COR_MEDIO

                    if notify_mode == 'smart':
                        if delta_days < 0 or delta_days <= 7:
                            final_color = COR_URGENTE
                        elif delta_days <= 30 and prio != 'critical':
                            final_color = COR_MEDIO
                    
                    content = d['data']
                    conn = "└──" if k == len(docs)-1 else "├──"
                    
                    # MONTAGEM DA LINHA: Obs -> Data -> Tag
                    str_line = (
                        f"{COR_ESTRUTURA}{indent}{conn} {RESET}"
                        f"{COR_BAIXO}{obs_str}{RESET}"
                        f"{final_color}{content}{RESET} "
                        f"{COR_TAG_TEXT}{tag_text}{RESET}"
                    )
                    lines.append(str_line)
        lines.append("```")
        return logo_output, "\n".join(lines)

    # --- MODOS V/H (INI) ---
    else:
        lines.append("```ini")
        if mode == 'h': lines.append("[ 🌲 VISÃO HORIZONTAL ]")
        else: lines.append("[ 🌲 VISÃO VERTICAL ]")
        
        tipos = sorted(dados.keys())
        for tipo in tipos:
            lines.append(f"\n[{tipo.upper()}]")
            materias = sorted(dados[tipo].keys())
            
            for j, materia in enumerate(materias):
                is_last_mat = (j == len(materias)-1)
                prefix = "└──" if is_last_mat else "├──"
                lines.append(f"{prefix} {materia}")
                
                docs = sorted(dados[tipo][materia], key=lambda x: parse_smart_date(x['data']) or datetime.max)
                indent = "    " if is_last_mat else "│   "
                items_formatted = []
                for d in docs:
                    prio = d.get('prioridade', 'low')
                    obs = d.get('observacoes', '')
                    obs_str = f"{obs} " if obs else ""
                    tag = ""
                    if prio == 'critical': tag = " [URG]"
                    elif prio == 'medium': tag = " [MED]"
                    # Mantemos a ordem padrão para o modo INI (não colorido)
                    items_formatted.append(f"{obs_str}{d['data']}{tag}")

                if mode == 'h':
                    joined = " | ".join(items_formatted)
                    lines.append(f"{indent}└── {joined}")
                else:
                    for k, item_txt in enumerate(items_formatted):
                        conn = "└──" if k == len(items_formatted)-1 else "├──"
                        lines.append(f"{indent}{conn} {item_txt}")

        lines.append("```")
        return None, "\n".join(lines)
        
# --- EVENTOS ---
@bot.event
async def on_ready():
    print(f'🎮 Discord CLI Online: {bot.user}')
    await bot.change_presence(activity=discord.Game(name="!help | !tree"))

# --- COMANDO: !help ---
@bot.command(name="help", aliases=["ajuda"])
async def help_cmd(ctx):
    embed = discord.Embed(title="🐧 Guia Acadêmico (CLI)", color=0x00ff00)
    embed.description = "Gerencie suas tarefas via linha de comando (igual ao Telegram)."
    
    add_txt = (
        "`!add Categoria` (Cria pasta vazia)\n"
        "`!add Cat Matéria Data [flags]`\n"
        "Ex: `!add Provas Cálculo 25/12 -alta`"
    )
    embed.add_field(name="🌱 Adicionar", value=add_txt, inline=False)

    edit_txt = (
        "Use `>` para separar ANTIGO > NOVO\n"
        "`!edit CatAntiga > CatNova` (Renomear Cat)\n"
        "`!edit Cat Evento > Cat NovoNome` (Renomear Evento)\n"
        "`!edit Cat Evento > OutraCat Evento` (Mover)\n"
        "`!edit Cat Evento > -alta -obs \"Texto\"` (Alterar Flags)"
    )
    embed.add_field(name="✏️ Editar / Mover", value=edit_txt, inline=False)

    del_txt = (
        "`!del Categoria` (Apaga TUDO da categoria)\n"
        "`!del Cat Evento` (Apaga Matéria)\n"
        "`!del Cat Evento Data` (Apaga Data específica)"
    )
    embed.add_field(name="🗑️ Deletar", value=del_txt, inline=False)

    view_txt = (
        "`!tree h` (Horizontal)\n"
        "`!tree v` ou `!tree f` (Vertical)\n"
        "`!tree notify` (Visualização de Alerta)\n"
        "`!list cat` (Ver categorias)\n"
        "`!list event` (Lista vertical)"
    )
    embed.add_field(name="🌲 Visualização", value=view_txt, inline=False)

    sys_txt = (
        "`!alert -f 12h -mode smart/manual` (Configurar notificação)\n"
        "`!alert teste` (Simular visualização)\n"
        "`!alert` (Visualiza status)\n"
        "`!alert desativa` (Desativa alerta)\n"
        "`!export` (Baixar backup JSON)\n"
        "`!import` (Carregar backup)\n"
        "`!link` (Conectar com Telegram)"
    )
    embed.add_field(name="⚙️ Sistema", value=sys_txt, inline=False)
    
    await ctx.send(embed=embed)

# --- COMANDO: !add ---
@bot.command(name="add")
async def add(ctx, *, args_str: str = ""):
    try:
        args, flags = parse_cli_args(args_str)
        if not args:
             await ctx.send("⚠️ Sintaxe: `!add Categoria [Materia] [Data] [flags]`")
             return

        cat = args[0].title()
        ids = get_linked_ids(ctx.author.id)

        if len(args) == 1:
            db.user_settings.update_one(
                {"user_id": ctx.author.id}, 
                {"$addToSet": {"custom_cats": cat}}, 
                upsert=True
            )
            await ctx.send(f"✅ Categoria **{cat}** criada/verificada.")
            return

        if len(args) < 3:
            await ctx.send("⚠️ **Faltam dados!** Use: `!add Categoria Matéria Data`")
            return

        mat = args[1]
        data_raw = args[2]

        dt_obj = parse_smart_date(data_raw)
        now = get_brt_now().replace(hour=0, minute=0, second=0, microsecond=0)

        if not dt_obj or dt_obj < now:
            await ctx.send(f"🚫 **Data Inválida ou Passada:** `{data_raw}`")
            return

        prio = flags.get("prio", "low")
        obs = flags.get("obs", "")
        if not obs and len(args) > 3: obs = " ".join(args[3:])
        
        item = {
            "user_id": ctx.author.id,
            "tipo": cat,
            "materia": mat,
            "data": dt_obj.strftime("%d/%m/%Y"),
            "prioridade": prio,
            "observacoes": obs,
            "sent_24h": False,
            "origin": "discord"
        }

        db.provas.insert_one(item)
        db.user_settings.update_one(
            {"user_id": ctx.author.id}, 
            {"$addToSet": {"custom_cats": cat}}, 
            upsert=True
        )

        prio_icon = "🚨" if prio == "critical" else ("⚠️" if prio == "medium" else "🟢")
        await ctx.send(f"✅ **Agendado!**\n📂 {cat} | 📅 {dt_obj.strftime('%d/%m/%Y')} | {prio_icon} {mat}")
        
        # USA A NOVA FUNÇÃO DE CHUNK
        tasks = list(db.provas.find({"user_id": {"$in": ids}}).sort("data", 1))
        logo, tree_str = generate_discord_tree(tasks, mode='v')
        
        if logo: await ctx.send(logo)
        await send_chunked_message(ctx, tree_str)

    except Exception as e:
        await ctx.send(f"❌ Erro ao processar: {e}")

# --- COMANDO: !edit ---
@bot.command(name="edit")
async def edit(ctx, *, args_str: str = ""):
    if '>' not in args_str:
        await ctx.send("⚠️ Use `>` para editar. Ex: `!edit Provas > Trabalhos`")
        return

    parts = args_str.split('>')
    lhs_str, rhs_str = parts[0].strip(), parts[1].strip()

    args_lhs, _ = parse_cli_args(lhs_str)
    args_rhs, flags_rhs = parse_cli_args(rhs_str)

    if not args_lhs:
        await ctx.send("⚠️ Origem vazia.")
        return

    ids = get_linked_ids(ctx.author.id)
    query = {"user_id": {"$in": ids}}
    
    scope = "unknown"
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
        await ctx.send("⚠️ Origem inválida.")
        return

    count = db.provas.count_documents(query)
    if count == 0:
        await ctx.send(f"🚫 Nada encontrado para: **{desc}**")
        return

    update_set = {}
    
    if scope == "category":
        if len(args_rhs) >= 1:
            new_cat = args_rhs[0].title()
            res = db.provas.update_many(query, {"$set": {"tipo": new_cat}})
            db.user_settings.update_one({"user_id": ctx.author.id}, {"$addToSet": {"custom_cats": new_cat}}, upsert=True)
            db.user_settings.update_one({"user_id": ctx.author.id}, {"$pull": {"custom_cats": args_lhs[0]}})
            await ctx.send(f"✅ Categoria renomeada para **{new_cat}** ({res.modified_count} itens).")
            return

    elif scope == "event":
        if len(args_rhs) >= 1:
            new_cat = args_rhs[0].title()
            update_set["tipo"] = new_cat
            db.user_settings.update_one({"user_id": ctx.author.id}, {"$addToSet": {"custom_cats": new_cat}}, upsert=True)
        if len(args_rhs) >= 2:
            update_set["materia"] = args_rhs[1]

    elif scope == "item":
        if len(args_rhs) >= 1:
            new_cat = args_rhs[0].title()
            update_set["tipo"] = new_cat
            db.user_settings.update_one({"user_id": ctx.author.id}, {"$addToSet": {"custom_cats": new_cat}}, upsert=True)
        if len(args_rhs) >= 2:
            update_set["materia"] = args_rhs[1]
        if len(args_rhs) >= 3:
            new_date = parse_smart_date(args_rhs[2])
            if new_date: update_set["data"] = new_date.strftime("%d/%m/%Y")

    if flags_rhs.get("prio"): update_set["prioridade"] = flags_rhs["prio"]
    if flags_rhs.get("obs"): update_set["observacoes"] = flags_rhs["obs"]

    if not update_set:
        await ctx.send("⚠️ Nenhuma alteração detectada.")
        return

    res = db.provas.update_many(query, {"$set": update_set})
    await ctx.send(f"✅ **Editado!** {res.modified_count} itens atualizados.")
    
    # USA A NOVA FUNÇÃO DE CHUNK
    tasks = list(db.provas.find({"user_id": {"$in": ids}}).sort("data", 1))
    logo, tree_str = generate_discord_tree(tasks, 'v')
    if logo: await ctx.send(logo)
    await send_chunked_message(ctx, tree_str)

# --- COMANDO: !del ---
@bot.command(name="del", aliases=["rm", "delete"])
async def delete(ctx, *, args_str: str = ""):
    try:
        args = shlex.split(args_str)
        if not args:
            await ctx.send("⚠️ Diga o que apagar. Ex: `!del Provas`")
            return

        ids = get_linked_ids(ctx.author.id)
        cat = args[0]
        query = {
            "user_id": {"$in": ids},
            "tipo": regex_ci(cat)
        }
        msg_alvo = f"Categoria **{cat}**"

        if len(args) >= 2:
            mat = args[1]
            query["materia"] = regex_ci(mat)
            msg_alvo = f"Evento **{mat}** em {cat}"

        if len(args) >= 3:
            data_raw = args[2]
            dt_obj = parse_smart_date(data_raw)
            if dt_obj:
                query["data"] = dt_obj.strftime("%d/%m/%Y")
                msg_alvo += f" na data {query['data']}"

        total = db.provas.count_documents(query)
        
        if total == 0:
            await ctx.send(f"🚫 **Nada encontrado para:** {msg_alvo}")
            return

        if len(args) == 1:
             db.user_settings.update_many(
                 {"user_id": {"$in": ids}},
                 {"$pull": {"custom_cats": cat}}
             )

        res = db.provas.delete_many(query)
        await ctx.send(f"🗑️ **Apagado!** {res.deleted_count} itens removidos.")

    except Exception as e:
        await ctx.send(f"❌ Erro: {e}")

# --- COMANDO: !tree (Atualizado) ---
@bot.command(name="tree")
async def tree(ctx, mode: str = None):
    if not mode:
        await ctx.send("⚠️ **Use:** `!tree h` (Horizontal), `!tree v` (Vertical) ou `!tree notify` (Alerta)")
        return
    
    mode = mode.lower()
    if mode in ['f', 'v']: mode = 'v'
    
    if mode not in ['v', 'h', 'notify']:
        await ctx.send("⚠️ Opção inválida. Use: `v`, `h` ou `notify`.")
        return

    ids = get_linked_ids(ctx.author.id)
    
    # 1. Busca configurações do usuário
    user_settings = db.user_settings.find_one({"user_id": ctx.author.id}) or {}
    
    # 2. Extrai apenas o modo ('smart' ou 'manual')
    current_notify_mode = user_settings.get("notify_mode", "smart")

    tasks = list(db.provas.find({"user_id": {"$in": ids}}).sort("data", 1))
    
    # 3. CORREÇÃO AQUI: Passamos 'notify_mode' (string) em vez de 'notify_settings' (dict)
    logo, tree_str = generate_discord_tree(tasks, mode=mode, notify_mode=current_notify_mode)
    
    if logo:
        await ctx.send(logo)
        
    await send_chunked_message(ctx, tree_str)

# --- COMANDO: !list (Atualizado) ---
@bot.command(name="list", aliases=["ls", "agenda"])
async def list_cmd(ctx, sub: str = None):
    ids = get_linked_ids(ctx.author.id)
    
    if not sub:
        await ctx.send("Use: `!list cat` ou `!list event`")
        return

    sub = sub.lower()

    if sub in ["cat", "cats"]:
        settings = list(db.user_settings.find({"user_id": {"$in": ids}}))
        all_cats = set(["Provas", "Trabalhos"])
        for s in settings:
            for c in s.get("custom_cats", []): all_cats.add(c)
        used_cats = db.provas.distinct("tipo", {"user_id": {"$in": ids}})
        for c in used_cats: all_cats.add(c)
            
        lines = []
        for c in sorted(list(all_cats)):
            count = db.provas.count_documents({"user_id": {"$in": ids}, "tipo": c})
            status = f"({count} itens)" if count > 0 else "(Vazia)"
            lines.append(f"• **{c}** {status}")
            
        embed = discord.Embed(title="📂 Categorias", description="\n".join(lines), color=0xf1c40f)
        await ctx.send(embed=embed)

    elif sub in ["event", "events"]:
        tasks = list(db.provas.find({"user_id": {"$in": ids}}).sort("data", 1))
        
        # O modo 'v' retorna logo=None, mas é bom manter o padrão
        logo, tree_str = generate_discord_tree(tasks, mode='v')
        
        if logo: await ctx.send(logo)
        await send_chunked_message(ctx, tree_str)
        
    else:
         await ctx.send("⚠️ Opção inválida. Use `!list cat` ou `!list event`.")


# --- COMANDO: !alert ---
@bot.command(name="alert")
async def alert(ctx, *, args_str: str = ""):
    # --- TESTE VISUAL (CORRIGIDO) ---
    if "test" in args_str.lower():
        ids = get_linked_ids(ctx.author.id)
        tasks = list(db.provas.find({"user_id": {"$in": ids}}).sort("data", 1))
        
        # Pega a configuração do banco por padrão
        cfg = db.user_settings.find_one({"user_id": ctx.author.id}) or {}
        mode_atual = cfg.get("notify_mode", "smart")
        
        # SOBRESCREVE SE TIVER FLAG NO COMANDO
        if "-smart" in args_str.lower(): mode_atual = "smart"
        elif "-manual" in args_str.lower(): mode_atual = "manual"

        await ctx.send(f"🔔 **Simulação de Alerta ({mode_atual.upper()})**")
        
        # Gera usando o modo decidido
        logo, tree_view = generate_discord_tree(tasks, mode='notify', notify_mode=mode_atual)
        
        if logo: await ctx.send(logo)
        await send_chunked_message(ctx, tree_view)
        return
    # --------------------------------

    if "desativar" in args_str.lower():
        db.user_settings.update_one({"user_id": ctx.author.id}, {"$unset": {"periodic_interval": ""}})
        await ctx.send("🔕 Alertas desativados.")
        return

    if "-help" in args_str:
        await ctx.send("🔔 **Alertas:**\nUse `-f TEMPO` (Ex: `!alert -f 12h`).\nUse `-mode smart` ou `-mode manual`.")
        return

    try: args = shlex.split(args_str)
    except: args = args_str.split()
    update_data = {}
    msg_log = []

    if "-f" in args:
        try:
            idx = args.index("-f") + 1
            if idx < len(args):
                secs = parse_time_string(args[idx])
                if secs:
                    bypass = False
                    if "-K" in args:
                         k_idx = args.index("-K") + 1
                         if k_idx < len(args) and args[k_idx] == Config.ADMIN_KEY: bypass = True
                    if not bypass and (secs < 3600 or secs > 604800):
                        await ctx.send("⚠️ Tempo deve ser entre 1h e 7 dias.")
                        return
                    update_data["periodic_interval"] = secs
                    update_data["last_periodic_run"] = get_brt_now()
                    msg_log.append(f"Freq: {format_seconds(secs)}")
        except: pass

    if "-mode" in args:
        try:
            idx = args.index("-mode") + 1
            if idx < len(args):
                m = args[idx].lower()
                if m in ["smart", "manual"]:
                    update_data["notify_mode"] = m
                    msg_log.append(f"Modo: {m.title()}")
        except: pass

    if update_data:
        db.user_settings.update_one({"user_id": ctx.author.id}, {"$set": update_data}, upsert=True)
        await ctx.send(f"✅ Configurado! " + " | ".join(msg_log))
    else:
        cfg = db.user_settings.find_one({"user_id": ctx.author.id}) or {}
        inter = cfg.get("periodic_interval", 0)
        mode = cfg.get("notify_mode", "smart")
        status = format_seconds(inter) if inter > 0 else "Off"
        await ctx.send(f"⚙️ **Status Alertas:**\nFrequência: {status}\nModo: {mode}")

# --- COMANDO: !export ---
@bot.command(name="export")
async def export_cmd(ctx):
    ids = get_linked_ids(ctx.author.id)
    data = list(db.provas.find({"user_id": {"$in": ids}}, {"_id": 0, "user_id": 0, "sent_24h": 0}))

    if not data:
        await ctx.send("📭 Agenda vazia.")
        return

    file_stream = io.BytesIO(json.dumps(data, indent=4, ensure_ascii=False).encode('utf-8'))
    file_stream.seek(0)
    await ctx.send("📦 **Backup**", file=discord.File(file_stream, filename="agenda_backup.json"))

# --- COMANDO: !link ---
@bot.command(name="link")
async def link(ctx, arg1: str = None, arg2: str = None):
    partners = get_partners(ctx.author.id)

    if not arg1:
        embed = discord.Embed(title="🔗 Gerenciar Vínculo", color=0x3498db)
        desc = (
            # CHANGE 1: Update the help text below
            "`!link telegram` (Gerar código p/ Telegram)\n"
            "`!link CÓDIGO` (Vincular)\n"
            "`!link status` (Ver conectados)\n"
            "`!link desvincular [ID]` (Sair)"
        )
        if partners: desc += f"\n\n🔗 **Você tem {len(partners)} conta(s) conectada(s).**"
        embed.description = desc
        await ctx.send(embed=embed)
        return

    if arg1.lower() == "status":
        if not partners: await ctx.send("🔓 **Status:** Conta Isolada.")
        else:
            txt = "**🔗 Contas Conectadas:**\n" + "\n".join([f"• ID: `{p}`" for p in partners])
            await ctx.send(txt)
        return

    if arg1.lower() == "desvincular":
        if arg2 and arg2.isdigit():
            success, msg = unlink_specific(ctx.author.id, arg2)
            await ctx.send(msg)
            return
        if arg2 and arg2.lower() in ["sim", "confirmar", "yes"]:
            success, msg = unlink_account(ctx.author.id)
            await ctx.send(msg)
        else:
            await ctx.send("⚠️ Use: `!link desvincular ID` ou `!link desvincular confirmar`")
        return

    # CHANGE 2: Update the condition check below
    if arg1.lower() == "telegram":
        warning = f"\n⚠️ Já possui {len(partners)} contas." if partners else ""
        
        # Note: Do NOT change "discord" inside generate_link_code. 
        # This parameter identifies WHERE the code was created, not where it goes.
        token = generate_link_code("discord", ctx.author.id) 
        
        embed = discord.Embed(title="🔐 Código de Vínculo", color=0xffff00)
        embed.description = f"Seu código: **`{token}`**\n1. Vá no Telegram.\n2. `/link {token}`\n{warning}"
        await ctx.author.send(embed=embed)
        await ctx.send("📩 Código enviado na DM!")
    else:
        token = arg1.strip()
        success, resp = validate_link_code(token, "discord", ctx.author.id)
        await ctx.send(resp)

# --- COMANDO: !import ---
@bot.command(name="import")
async def import_cmd(ctx):
    if not ctx.message.attachments:
        await ctx.send("⚠️ Anexe o `.json` e digite `!import`.")
        return

    attachment = ctx.message.attachments[0]
    if not attachment.filename.endswith('.json'):
        await ctx.send("🚫 Formato inválido.")
        return

    try:
        file_bytes = await attachment.read()
        data_import = json.loads(file_bytes.decode('utf-8'))
        
        if not isinstance(data_import, list):
             await ctx.send("🚫 JSON inválido.")
             return

        valid_items = []
        for item in data_import:
            if "materia" not in item or "data" not in item: continue
            new_item = item.copy()
            if "_id" in new_item: del new_item["_id"]
            new_item["user_id"] = ctx.author.id
            new_item["origin"] = "discord_import"
            valid_items.append(new_item)

        if not valid_items:
            await ctx.send("🚫 Nenhum evento válido.")
            return

        view = ImportView(ctx.author.id, valid_items)
        embed = discord.Embed(title="📦 Importação", description=f"Encontrei **{len(valid_items)}** eventos.", color=0x3498db)
        await ctx.send(embed=embed, view=view)
    except: await ctx.send("❌ Erro ao processar arquivo.")

class ImportView(discord.ui.View):
    def __init__(self, author_id, valid_items):
        super().__init__(timeout=60)
        self.author_id = author_id
        self.items = valid_items
        self.finished = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author_id

    @discord.ui.button(label="🔥 SUBSTITUIR TUDO", style=discord.ButtonStyle.danger)
    async def replace_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.finished = True
        db.provas.delete_many({"user_id": self.author_id})
        if self.items:
            db.provas.insert_many(self.items)
            db.user_settings.update_one({"user_id": self.author_id}, {"$set": {"custom_cats": []}})
            for item in self.items:
                db.user_settings.update_one({"user_id": self.author_id}, {"$addToSet": {"custom_cats": item.get("tipo", "Geral")}}, upsert=True)
        await interaction.response.edit_message(content="✅ Substituído com sucesso!", view=None)

    @discord.ui.button(label="➕ MESCLAR", style=discord.ButtonStyle.primary)
    async def merge_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.finished = True
        if self.items: db.provas.insert_many(self.items)
        for item in self.items:
             db.user_settings.update_one({"user_id": self.author_id}, {"$addToSet": {"custom_cats": item.get("tipo", "Geral")}}, upsert=True)
        await interaction.response.edit_message(content=f"✅ {len(self.items)} itens adicionados.", view=None)

    @discord.ui.button(label="❌ Cancelar", style=discord.ButtonStyle.secondary)
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.finished = True
        await interaction.response.edit_message(content="❌ Cancelado.", view=None)

if __name__ == "__main__":
    if Config.DISCORD_TOKEN:
        bot.run(Config.DISCORD_TOKEN)