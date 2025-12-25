import os
import asyncio
import threading
import requests
import psutil
import time
from datetime import datetime, timezone

from flask import Flask, request, render_template
import discord
from discord.ext import commands

# =====================
# ENV
# =====================
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
REDIRECT_URI = os.getenv("OAUTH_REDIRECT_URI")

GUILD_ID = int(os.getenv("DISCORD_GUILD_ID"))
VERIFY_ROLE_ID = int(os.getenv("VERIFY_ROLE_ID"))
FAIL_ROLE_ID = int(os.getenv("FAIL_ROLE_ID"))
SUPPORT_ROLE_ID = int(os.getenv("SUPPORT_ROLE_ID"))
SUPPORT_CHANNEL_ID = int(os.getenv("SUPPORT_CHANNEL_ID"))
SUPPORT_INVITE_URL = os.getenv("SUPPORT_INVITE_URL")
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID"))

RECAPTCHA_SITE_KEY = os.getenv("RECAPTCHA_SITE_KEY")
RECAPTCHA_SECRET_KEY = os.getenv("RECAPTCHA_SECRET_KEY")

PORT = int(os.getenv("PORT", 10000))

# =====================
# Discord Bot
# =====================
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="/", intents=intents)

VERIFY_URL = (
    "https://discord.com/api/oauth2/authorize"
    f"?client_id={CLIENT_ID}"
    f"&redirect_uri={REDIRECT_URI}"
    "&response_type=code&scope=identify"
)

def get_client_ip(req):
    xff = req.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()
    return req.remote_addr or "unknown"

# =====================
# Bot Events
# =====================
@bot.event
async def on_ready():
    await bot.tree.sync()
    bot.loop.create_task(status_loop())
    print(f"Logged in as {bot.user}")

@bot.tree.command(name="認証パネル")
async def verify_panel(interaction: discord.Interaction):
    await interaction.response.defer(thinking=False)

    embed = discord.Embed(
        title="認証",
        description="ボタンを押して認証！",
        color=discord.Color.dark_grey()
    )
    embed.set_footer(text="Cats Shop🛒 | Web Verify System")

    view = discord.ui.View()
    view.add_item(
        discord.ui.Button(
            label="認証する",
            style=discord.ButtonStyle.grey,
            url=VERIFY_URL
        )
    )

    await interaction.followup.send(embed=embed, view=view)

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if message.channel.id != SUPPORT_CHANNEL_ID:
        return

    if message.content.strip() == "認証お願いします":
        role = message.guild.get_role(SUPPORT_ROLE_ID)
        fail = message.guild.get_role(FAIL_ROLE_ID)

        if role:
            await message.author.add_roles(role)
        if fail:
            await message.author.remove_roles(fail)

        await message.delete()
        await message.channel.send(
            f"{message.author.mention} 認証しました。",
            delete_after=10
        )

# =====================
# Flask
# =====================
app = Flask(__name__)

def verify_recaptcha(token: str) -> bool:
    r = requests.post(
        "https://www.google.com/recaptcha/api/siteverify",
        data={
            "secret": RECAPTCHA_SECRET_KEY,
            "response": token
        }
    )
    return r.json().get("success", False)

@app.route("/")
def ping():
    return "ok"

@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return "No code", 400

    token_res = requests.post(
        "https://discord.com/api/oauth2/token",
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    ).json()

    access_token = token_res.get("access_token")
    if not access_token:
        return "OAuth failed", 400

    user = requests.get(
        "https://discord.com/api/users/@me",
        headers={"Authorization": f"Bearer {access_token}"}
    ).json()

    return render_template(
        "verify.html",
        site_key=RECAPTCHA_SITE_KEY,
        user=user
    )

@app.route("/verify", methods=["POST"])
def verify():
    recaptcha_token = request.form.get("g-recaptcha-response")
    user_id = int(request.form.get("user_id"))
    username = request.form.get("username", "unknown")
    ip = get_client_ip(request)

    if not verify_recaptcha(recaptcha_token):
        asyncio.run_coroutine_threadsafe(
            send_verify_log(user_id, username, ip, False, "RECAPTCHA_FAILED"),
            bot.loop
        )
        asyncio.run_coroutine_threadsafe(
            give_role(user_id, FAIL_ROLE_ID),
            bot.loop
        )
        return render_template("fail.html", invite=SUPPORT_INVITE_URL, error="RECAPTCHA_FAILED")

    asyncio.run_coroutine_threadsafe(
        send_verify_log(user_id, username, ip, True),
        bot.loop
    )
    asyncio.run_coroutine_threadsafe(
        give_role(user_id, VERIFY_ROLE_ID),
        bot.loop
    )

    return render_template("success.html")

# =====================
# Helpers
# =====================
async def give_role(user_id: int, role_id: int):
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return

    member = guild.get_member(user_id)
    if not member:
        try:
            member = await guild.fetch_member(user_id)
        except:
            return

    role = guild.get_role(role_id)
    if role:
        await member.add_roles(role)

async def send_verify_log(user_id, username, ip, success, reason=""):
    channel = bot.get_channel(LOG_CHANNEL_ID)
    if not channel:
        return

    embed = discord.Embed(
        title="認証成功" if success else "認証失敗",
        color=discord.Color.green() if success else discord.Color.red(),
        timestamp=datetime.now(timezone.utc)
    )
    embed.add_field(name="User", value=f"{username} ({user_id})", inline=False)
    embed.add_field(name="IP", value=ip, inline=False)
    if not success:
        embed.add_field(name="Reason", value=reason, inline=False)

    await channel.send(embed=embed)

# =====================
# Bot Status
# =====================
def get_status_text(bot):
    cpu = psutil.cpu_percent()
    ram = psutil.virtual_memory().percent
    disk = psutil.disk_usage("/").percent
    net = psutil.net_io_counters()
    net_mb = (net.bytes_sent + net.bytes_recv) / 1024 / 1024
    servers = len(bot.guilds)

    return f"CPU:{cpu:.1f}% | RAM:{ram:.1f}% | DSK:{disk:.1f}% | NET:{net_mb:.0f}MB | {servers} SRV"

async def status_loop():
    await bot.wait_until_ready()
    while not bot.is_closed():
        await bot.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name=get_status_text(bot)
            )
        )
        await asyncio.sleep(900)

# =====================
# Run
# =====================
def run_bot():
    bot.run(DISCORD_TOKEN)

if __name__ == "__main__":
    threading.Thread(target=run_bot).start()
    app.run(host="0.0.0.0", port=PORT)
