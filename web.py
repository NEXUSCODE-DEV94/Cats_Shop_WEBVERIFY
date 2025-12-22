import os
import requests
from flask import Flask, request, render_template
import discord
import asyncio

app = Flask(__name__)

# ===== env =====
CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
REDIRECT_URI = os.getenv("OAUTH_REDIRECT_URI")
BOT_TOKEN = os.getenv("DISCORD_TOKEN")

GUILD_ID = int(os.getenv("DISCORD_GUILD_ID"))
VERIFY_ROLE_ID = int(os.getenv("VERIFY_ROLE_ID"))
FAIL_ROLE_ID = int(os.getenv("FAIL_ROLE_ID"))
SUPPORT_INVITE_URL = os.getenv("SUPPORT_INVITE_URL")

RECAPTCHA_SITE_KEY = os.getenv("RECAPTCHA_SITE_KEY")
RECAPTCHA_SECRET_KEY = os.getenv("RECAPTCHA_SECRET_KEY")

# ===== Discord Client =====
intents = discord.Intents.none()
intents.guilds = True
intents.members = True
client = discord.Client(intents=intents)

loop = asyncio.get_event_loop()
loop.create_task(client.start(BOT_TOKEN))

# ===== util =====
def verify_recaptcha(token):
    r = requests.post(
        "https://www.google.com/recaptcha/api/siteverify",
        data={
            "secret": RECAPTCHA_SECRET_KEY,
            "response": token
        }
    )
    return r.json().get("success", False)

# ===== routes =====
@app.route("/")
def ping():
    return "ok"

@app.route("/callback", methods=["GET", "POST"])
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
    token = request.form.get("g-recaptcha-response")
    user_id = int(request.form.get("user_id"))

    if not verify_recaptcha(token):
        loop.create_task(assign_role(user_id, FAIL_ROLE_ID))
        return render_template(
            "fail.html",
            invite=SUPPORT_INVITE_URL,
            error="RECAPTCHA_FAILED"
        )

    loop.create_task(assign_role(user_id, VERIFY_ROLE_ID))
    return render_template("success.html")

async def assign_role(user_id, role_id):
    guild = client.get_guild(GUILD_ID)
    member = guild.get_member(user_id)
    role = guild.get_role(role_id)
    if member and role:
        await member.add_roles(role)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
