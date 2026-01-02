import discord
from discord.ext import commands
from discord.ui import Button, View
from datetime import datetime
import pytz
import os
from dotenv import load_dotenv

# ---------- ENV ----------
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
MOD_ROLE_NAME = os.getenv("MOD_ROLE_NAME", "mod")
TIMEZONE = "US/Pacific"

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Map user_id -> mod channel id
user_ticket_channels = {}
ticket_users = {}

# ---------- CLOSE BUTTON ----------
class CloseTicketView(View):
    def __init__(self, channel_id):
        super().__init__(timeout=None)
        self.channel_id = channel_id

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger)
    async def close_ticket(self, interaction: discord.Interaction, button: Button):
        channel = interaction.guild.get_channel(self.channel_id)
        if channel:
            user_id = ticket_users.get(self.channel_id)
            if user_id:
                user = await bot.fetch_user(user_id)
                await user.send("✅ Your ticket has been closed by a mod.")
            await channel.delete()
        user_ticket_channels.pop(ticket_users.get(self.channel_id, 0), None)
        ticket_users.pop(self.channel_id, None)
        await interaction.response.send_message("Ticket closed.", ephemeral=True)

# ---------- CREATE TICKET BUTTON ----------
class TicketButtonView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📩 Open Support Ticket", style=discord.ButtonStyle.primary)
    async def create_ticket(self, interaction: discord.Interaction, button: Button):
        user = interaction.user
        guild = interaction.guild
        mod_role = discord.utils.get(guild.roles, name=MOD_ROLE_NAME)

        if not mod_role:
            await interaction.response.send_message(
                f"❌ Mod role '{MOD_ROLE_NAME}' not found!", ephemeral=True
            )
            return

        if user.id in user_ticket_channels:
            await interaction.response.send_message(
                "⚠️ You already have an open ticket!", ephemeral=True
            )
            return

        # DM the user
        try:
            await user.send(
                f"🎫 Hi {user.name}, your ticket has been created in **{guild.name}**. Mods will respond shortly."
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "⚠️ Cannot DM you. Please enable DMs from server members.", ephemeral=True
            )
            return

        # Create private channel for mods
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            mod_role: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            bot.user: discord.PermissionOverwrite(view_channel=True, send_messages=True)
        }

        channel = await guild.create_text_channel(f"ticket-{user.name}", overwrites=overwrites)
        user_ticket_channels[user.id] = channel.id
        ticket_users[channel.id] = user.id

        # Add close button
        close_view = CloseTicketView(channel.id)
        await channel.send(
            embed=discord.Embed(
                title=f"🎫 New Ticket from {user.name}",
                description=f"User ID: {user.id}\nOpened at: {datetime.now(pytz.timezone(TIMEZONE)).strftime('%Y-%m-%d %H:%M:%S %Z')}",
                color=discord.Color.green()
            ),
            view=close_view
        )

        await interaction.response.send_message(
            f"✅ Ticket created! Check your DMs, {user.mention}.", ephemeral=True
        )

# ---------- SETUP COMMAND ----------
@bot.command()
@commands.has_permissions(administrator=True)
async def setup(ctx):
    """Posts the ticket button for users."""
    view = TicketButtonView()
    await ctx.send("Click the button below to open a support ticket:", view=view)

# ---------- DM -> Server ----------
@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # User sent DM
    if isinstance(message.channel, discord.DMChannel):
        user_id = message.author.id
        if user_id not in user_ticket_channels:
            await message.author.send(
                "You don't have an open ticket. Please click the button in the server to create one."
            )
            return

        channel_id = user_ticket_channels[user_id]
        guild_channel = bot.get_channel(channel_id)
        if guild_channel:
            content = message.content or ""
            files = [await att.to_file() for att in message.attachments]
            embed = discord.Embed(
                title=f"📨 Message from {message.author.name}",
                description=content if content else "📎 Attachment only",
                color=discord.Color.blue(),
                timestamp=datetime.now(pytz.timezone(TIMEZONE))
            )
            await guild_channel.send(embed=embed, files=files)

    # Mods send message in server ticket -> DM user
    else:
        channel_id = message.channel.id
        if channel_id in ticket_users:
            user_id = ticket_users[channel_id]
            user = await bot.fetch_user(user_id)
            if message.content:
                await user.send(f"**Mod:** {message.content}")
            for att in message.attachments:
                await user.send(file=await att.to_file())

    await bot.process_commands(message)

# ---------- ON READY ----------
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")

bot.run(DISCORD_TOKEN)
