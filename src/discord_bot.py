import discord
import os
from discord.ext import commands, tasks
from src.database import db
from src.config import Config
from src.utils import parse_smart_date, get_brt_now

# Configuração
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f'🎮 Discord Bot Online: {bot.user}')
    # Inicia verificação de alertas se necessário
    check_reminders.start()

@bot.command(name="agenda")
async def agenda(ctx):
    # Lógica similar ao Telegram, mas formatada para Discord (Embeds)
    user_id_tg = obter_link_discord_telegram(ctx.author.id) # Lógica sua
    provas = list(db.provas.find({"user_id": user_id_tg}))
    
    if not provas:
        await ctx.send("📭 Nenhuma tarefa encontrada.")
        return

    embed = discord.Embed(title="🎓 Painel Acadêmico", color=0x00ff00)
    for p in provas:
        embed.add_field(name=p['materia'], value=f"{p['data']} - {p.get('tipo')}", inline=False)
    
    await ctx.send(embed=embed)

# Loop de notificação (Opcional, se não quiser usar o notifier.py)
@tasks.loop(minutes=60)
async def check_reminders():
    # Lógica de checar banco e mandar DM
    pass

if __name__ == "__main__":
    bot.run(os.getenv("DISCORD_TOKEN"))