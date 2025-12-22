import os
import discord
from discord.ext import commands

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="/", intents=intents)

# ===== env =====
TOKEN = os.getenv("DISCORD_TOKEN")
CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
REDIRECT_URI = os.getenv("OAUTH_REDIRECT_URI")

VERIFY_ROLE_ID = int(os.getenv("VERIFY_ROLE_ID"))
FAIL_ROLE_ID = int(os.getenv("FAIL_ROLE_ID"))
SUPPORT_ROLE_ID = int(os.getenv("SUPPORT_ROLE_ID"))
SUPPORT_CHANNEL_ID = int(os.getenv("SUPPORT_CHANNEL_ID"))

VERIFY_URL = (
    "https://discord.com/api/oauth2/authorize"
    f"?client_id={CLIENT_ID}"
    f"&redirect_uri={REDIRECT_URI}"
    "&response_type=code&scope=identify"
)

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user}")

# ===== 認証パネル =====
@bot.tree.command(name="認証パネル")
async def verify_panel(interaction: discord.Interaction):
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

# ===== 救済認証 =====
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

bot.run(TOKEN)
