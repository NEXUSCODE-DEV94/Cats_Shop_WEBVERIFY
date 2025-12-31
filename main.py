import os
import asyncio
import threading
import requests
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
LOG_CHANNEL_ID = getenv_int("LOG_CHANNEL_ID")

RECAPTCHA_SITE_KEY = os.getenv("RECAPTCHA_SITE_KEY")
RECAPTCHA_SECRET_KEY = os.getenv("RECAPTCHA_SECRET_KEY")

PORT = int(os.getenv("PORT", 10000))

# =====================
# Discord Bot
# =====================
intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
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
    print("Bot ready")

@bot.tree.command(name="認証パネル")
async def verify_panel(interaction: discord.Interaction):
    embed = discord.Embed(
        title="認証",
        description="下のボタンから認証してください",
        color=discord.Color.dark_grey()
    )

    view = discord.ui.View(timeout=None)
    view.add_item(
        discord.ui.Button(
            label="認証する",
            style=discord.ButtonStyle.link,
            url=VERIFY_URL
        )
    )

    await interaction.response.send_message(embed=embed, view=view)

# =====================
# Flask
# =====================
app = Flask(__name__)

def verify_recaptcha(token):
    if not token:
        return False
    r = requests.post(
        "https://www.google.com/recaptcha/api/siteverify",
        timeout=5,
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
        abort(400)

    token_res = requests.post(
        "https://discord.com/api/oauth2/token",
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=10
    )

    if token_res.status_code != 200:
        print(token_res.text)
        abort(502)

    token_json = token_res.json()
    access_token = token_json.get("access_token")
    if not access_token:
        abort(400)

    user_res = requests.get(
        "https://discord.com/api/users/@me",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10
    )

    if user_res.status_code != 200:
        abort(502)

    user = user_res.json()

    return render_template(
        "verify.html",
        site_key=RECAPTCHA_SITE_KEY,
        user_id=user["id"],
        username=user.get("global_name") or user["username"]
    )

@app.route("/verify", methods=["POST"])
def verify():
    if DISCORD_LOOP is None:
        abort(503)

    user_id = int(request.form["user_id"])
    token = request.form.get("g-recaptcha-response")

    if not verify_recaptcha(token):
        asyncio.run_coroutine_threadsafe(
            give_role(user_id, FAIL_ROLE_ID),
            DISCORD_LOOP
        )
        return render_template("fail.html")

    asyncio.run_coroutine_threadsafe(
        give_role(user_id, VERIFY_ROLE_ID),
        DISCORD_LOOP
    )

    asyncio.run_coroutine_threadsafe(
        send_log(user_id, True),
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
    member = await guild.fetch_member(user_id)
    role = guild.get_role(role_id)
    if role:
        await member.add_roles(role)

async def send_log(user_id, success):
    channel = bot.get_channel(LOG_CHANNEL_ID)
    if not channel:
        return
    embed = discord.Embed(
        title="認証成功" if success else "認証失敗",
        color=discord.Color.green() if success else discord.Color.red(),
        timestamp=datetime.now(timezone.utc)
    )
    embed.add_field(name="User ID", value=str(user_id))
    await channel.send(embed=embed)

# =====================
# Run
# =====================
def run_bot():
    bot.run(DISCORD_TOKEN)

if __name__ == "__main__":
    threading.Thread(target=run_bot, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT)
