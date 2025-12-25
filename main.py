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
# ENV
# =====================
def getenv_int(name):
    v = os.getenv(name)
    if v is None:
        raise RuntimeError(f"ENV {name} not set")
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

# =====================
# Discord Bot
# =====================
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="/", intents=intents)
DISCORD_LOOP = None

VERIFY_URL = (
    "https://discord.com/api/oauth2/authorize"
    f"?client_id={CLIENT_ID}"
    f"&redirect_uri={REDIRECT_URI}"
    "&response_type=code&scope=identify"
)

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

# =====================
# Flask
# =====================
app = Flask(__name__)

def get_client_ip(req):
    xff = req.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()
    return req.remote_addr or "unknown"

def verify_recaptcha(token):
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
        }
    ).json()

    access_token = token_res.get("access_token")
    if not access_token:
        abort(400)

    user = requests.get(
        "https://discord.com/api/users/@me",
        headers={"Authorization": f"Bearer {access_token}"}
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
    ip = get_client_ip(request)

    if not verify_recaptcha(recaptcha_token):
        asyncio.run_coroutine_threadsafe(
            send_verify_log(user_id, username, ip, False, "RECAPTCHA_FAILED"),
            DISCORD_LOOP
        )
        asyncio.run_coroutine_threadsafe(
            give_role(user_id, FAIL_ROLE_ID),
            DISCORD_LOOP
        )
        return render_template("fail.html", invite=SUPPORT_INVITE_URL)

    asyncio.run_coroutine_threadsafe(
        send_verify_log(user_id, username, ip, True),
        DISCORD_LOOP
    )
    asyncio.run_coroutine_threadsafe(
        give_role(user_id, VERIFY_ROLE_ID),
        DISCORD_LOOP
    )
    return render_template("success.html")

# =====================
# Helpers
# =====================
async def give_role(user_id, role_id):
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
# Status
# =====================
async def status_loop():
    await bot.wait_until_ready()
    while True:
        await bot.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name=f"CPU {psutil.cpu_percent():.0f}% | RAM {psutil.virtual_memory().percent:.0f}%"
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
