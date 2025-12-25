import os
import asyncio
import threading
import requests
import psutil
from datetime import datetime, timezone

from flask import Flask, request, render_template, abort
import discord
from discord.ext import commands

# =====================
# ENV（安全取得）
# =====================
def getenv_int(name, required=True):
    v = os.getenv(name)
    if v is None:
        if required:
            raise RuntimeError(f"ENV {name} is not set")
        return None
    return int(v)

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
REDIRECT_URI = os.getenv("OAUTH_REDIRECT_URI")

GUILD_ID = getenv_int("DISCORD_GUILD_ID")
VERIFY_ROLE_ID = getenv_int("VERIFY_ROLE_ID")
FAIL_ROLE_ID = getenv_int("FAIL_ROLE_ID")
SUPPORT_ROLE_ID = getenv_int("SUPPORT_ROLE_ID")
SUPPORT_CHANNEL_ID = getenv_int("SUPPORT_CHANNEL_ID")
LOG_CHANNEL_ID = getenv_int("LOG_CHANNEL_ID")

SUPPORT_INVITE_URL = os.getenv("SUPPORT_INVITE_URL", "")
RECAPTCHA_SITE_KEY = os.getenv("RECAPTCHA_SITE_KEY")
RECAPTCHA_SECRET_KEY = os.getenv("RECAPTCHA_SECRET_KEY")

PORT = int(os.getenv("PORT", 10000))

if not all([DISCORD_TOKEN, CLIENT_ID, CLIENT_SECRET, REDIRECT_URI]):
    raise RuntimeError("Required OAuth ENV missing")

# =====================
# Discord Bot
# =====================
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="/", intents=intents)

# Discordのイベントループを保持（重要）
DISCORD_LOOP: asyncio.AbstractEventLoop | None = None

VERIFY_URL = (
    "https://discord.com/api/oauth2/authorize"
    f"?client_id={CLIENT_ID}"
    f"&redirect_uri={REDIRECT_URI}"
    "&response_type=code&scope=identify"
)

# =====================
# Bot Events
# =====================
@bot.event
async def on_ready():
    global DISCORD_LOOP
    DISCORD_LOOP = asyncio.get_running_loop()
    await bot.tree.sync()
    bot.loop.create_task(status_loop())
    print(f"Logged in as {bot.user}")

@bot.tree.command(name="認証パネル")
async def verify_panel(interaction: discord.Interaction):
    embed = discord.Embed(
        title="認証",
        description="下のボタンから認証してください。",
        color=discord.Color.dark_grey()
    )
    view = discord.ui.View()
    view.add_item(
        discord.ui.Button(
            label="認証する",
            style=discord.ButtonStyle.grey,
            url=VERIFY_URL
        )
    )
    await interaction.response.send_message(embed=embed, view=view)

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

# =====================
# Flask
# =====================
app = Flask(__name__)

def verify_recaptcha(token: str | None) -> bool:
    if not token or not RECAPTCHA_SECRET_KEY:
        return False
    r = requests.post(
        "https://www.google.com/recaptcha/api/siteverify",
        timeout=5,
        data={
            "secret": RECAPTCHA_SECRET_KEY,
            "response": token
        }
    )
    return bool(r.json().get("success"))

@app.route("/")
def ping():
    return "ok"

@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        abort(400)

    token_res = requests.post(
        "https://discord.com/api/oauth2/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI
        },
        timeout=10
    ).json()

    access_token = token_res.get("access_token")
    if not access_token:
        abort(400)

    user = requests.get(
        "https://discord.com/api/users/@me",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10
    ).json()

    display_name = user.get("global_name") or user.get("username")

    return render_template(
        "verify.html",
        site_key=RECAPTCHA_SITE_KEY,
        user_id=user["id"],
        username=display_name
    )

@app.route("/verify", methods=["POST"])
def verify():
    if DISCORD_LOOP is None:
        abort(503)

    user_id = int(request.form.get("user_id", 0))
    username = request.form.get("username", "unknown")
    recaptcha_token = request.form.get("g-recaptcha-response")

    if not verify_recaptcha(recaptcha_token):
        asyncio.run_coroutine_threadsafe(
            give_role(user_id, FAIL_ROLE_ID),
            DISCORD_LOOP
        )
        asyncio.run_coroutine_threadsafe(
            send_verify_log(user_id, username, False, "RECAPTCHA_FAILED"),
            DISCORD_LOOP
        )
        return render_template("fail.html", invite=SUPPORT_INVITE_URL)

    asyncio.run_coroutine_threadsafe(
        give_role(user_id, VERIFY_ROLE_ID),
        DISCORD_LOOP
    )
    asyncio.run_coroutine_threadsafe(
        send_verify_log(user_id, username, True),
        DISCORD_LOOP
    )
    return render_template("success.html")

# =====================
# Helpers
# =====================
async def give_role(user_id: int, role_id: int):
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return
    try:
        member = await guild.fetch_member(user_id)
    except:
        return
    role = guild.get_role(role_id)
    if role:
        await member.add_roles(role)

async def send_verify_log(user_id, username, success, reason=""):
    channel = bot.get_channel(LOG_CHANNEL_ID)
    if not channel:
        return
    embed = discord.Embed(
        title="認証成功" if success else "認証失敗",
        color=discord.Color.green() if success else discord.Color.red(),
        timestamp=datetime.now(timezone.utc)
    )
    embed.add_field(name="User", value=f"{username} ({user_id})", inline=False)
    if not success:
        embed.add_field(name="Reason", value=reason, inline=False)
    await channel.send(embed=embed)

# =====================
# Bot Status
# =====================
def get_status_text():
    return f"CPU:{psutil.cpu_percent():.0f}% | RAM:{psutil.virtual_memory().percent:.0f}%"

async def status_loop():
    await bot.wait_until_ready()
    while True:
        await bot.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name=get_status_text()
            )
        )
        await asyncio.sleep(900)

# =====================
# Run
# =====================
def run_bot():
    bot.run(DISCORD_TOKEN)

if __name__ == "__main__":
    threading.Thread(target=run_bot, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT)
