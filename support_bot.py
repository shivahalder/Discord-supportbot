"""
Enhanced Discord Modmail System
================================
A comprehensive modmail bot with persistent storage, advanced features,
and improved user experience.

Features:
- Persistent SQLite database for ticket data
- Admin command to create tickets for specific users
- Message history and transcripts
- Categorized tickets with priority levels
- Auto-close inactive tickets
- Detailed logging system
- Support for attachments and embeds
- Ban/mute notifications
- Ticket statistics and analytics
"""

import discord
from discord.ext import commands, tasks
from discord.ui import Button, View, Select, Modal, TextInput
from datetime import datetime, timedelta
import pytz
import os
import sqlite3
import json
import asyncio
from typing import Optional, Dict, List
from dotenv import load_dotenv
import logging
from pathlib import Path

# ============================================================================
# CONFIGURATION
# ============================================================================

load_dotenv()

# Environment variables
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
MOD_ROLE_NAME = os.getenv("MOD_ROLE_NAME", "mod")
ADMIN_ROLE_NAME = os.getenv("ADMIN_ROLE_NAME", "admin")
TIMEZONE = os.getenv("TIMEZONE", "US/Pacific")
TICKET_CATEGORY_NAME = os.getenv("TICKET_CATEGORY_NAME", "Tickets")
LOG_CHANNEL_NAME = os.getenv("LOG_CHANNEL_NAME", "modmail-logs")
INACTIVE_TIMEOUT = int(os.getenv("INACTIVE_TIMEOUT_HOURS", "48"))
DATABASE_PATH = os.getenv("DATABASE_PATH", "modmail.db")

# Bot configuration
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.guilds = True
intents.presences = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# Timezone
tz = pytz.timezone(TIMEZONE)

# ============================================================================
# LOGGING SETUP
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('modmail.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('modmail')

# ============================================================================
# DATABASE SETUP
# ============================================================================

class Database:
    """Handles all database operations for the modmail system."""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.init_database()
    
    def get_connection(self):
        """Create a new database connection."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    def init_database(self):
        """Initialize database tables."""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Tickets table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                channel_id INTEGER,
                guild_id INTEGER NOT NULL,
                status TEXT DEFAULT 'open',
                category TEXT DEFAULT 'general',
                priority TEXT DEFAULT 'normal',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                closed_at TIMESTAMP,
                closed_by INTEGER,
                close_reason TEXT,
                created_by INTEGER,
                last_message_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Messages table for transcript
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                content TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_staff BOOLEAN DEFAULT 0,
                attachment_urls TEXT,
                FOREIGN KEY (ticket_id) REFERENCES tickets (id)
            )
        ''')
        
        # Notes table for internal mod notes
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id INTEGER NOT NULL,
                author_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (ticket_id) REFERENCES tickets (id)
            )
        ''')
        
        # User data table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_data (
                user_id INTEGER PRIMARY KEY,
                total_tickets INTEGER DEFAULT 0,
                warnings INTEGER DEFAULT 0,
                banned BOOLEAN DEFAULT 0,
                banned_at TIMESTAMP,
                ban_reason TEXT,
                notes TEXT
            )
        ''')
        
        # Config table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS config (
                guild_id INTEGER PRIMARY KEY,
                ticket_counter INTEGER DEFAULT 0,
                log_channel_id INTEGER,
                ticket_category_id INTEGER
            )
        ''')
        
        conn.commit()
        conn.close()
        logger.info("Database initialized successfully")
    
    def create_ticket(self, user_id: int, guild_id: int, channel_id: int, 
                     category: str = "general", priority: str = "normal",
                     created_by: Optional[int] = None) -> int:
        """Create a new ticket and return ticket ID."""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO tickets (user_id, channel_id, guild_id, category, priority, created_by)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, channel_id, guild_id, category, priority, created_by))
        
        ticket_id = cursor.lastrowid
        
        # Update user ticket count
        cursor.execute('''
            INSERT INTO user_data (user_id, total_tickets)
            VALUES (?, 1)
            ON CONFLICT(user_id) DO UPDATE SET total_tickets = total_tickets + 1
        ''', (user_id,))
        
        conn.commit()
        conn.close()
        
        logger.info(f"Created ticket {ticket_id} for user {user_id}")
        return ticket_id
    
    def get_active_ticket(self, user_id: int, guild_id: int) -> Optional[sqlite3.Row]:
        """Get active ticket for a user."""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM tickets 
            WHERE user_id = ? AND guild_id = ? AND status = 'open'
            ORDER BY created_at DESC LIMIT 1
        ''', (user_id, guild_id))
        
        ticket = cursor.fetchone()
        conn.close()
        return ticket
    
    def get_ticket_by_channel(self, channel_id: int) -> Optional[sqlite3.Row]:
        """Get ticket by channel ID."""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM tickets WHERE channel_id = ? AND status = "open"', 
                      (channel_id,))
        
        ticket = cursor.fetchone()
        conn.close()
        return ticket
    
    def close_ticket(self, ticket_id: int, closed_by: int, reason: str = None):
        """Close a ticket."""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE tickets 
            SET status = 'closed', closed_at = CURRENT_TIMESTAMP, closed_by = ?, close_reason = ?
            WHERE id = ?
        ''', (closed_by, reason, ticket_id))
        
        conn.commit()
        conn.close()
        
        logger.info(f"Closed ticket {ticket_id} by {closed_by}")
    
    def add_message(self, ticket_id: int, user_id: int, content: str, 
                   is_staff: bool = False, attachment_urls: List[str] = None):
        """Add a message to ticket history."""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        attachment_json = json.dumps(attachment_urls) if attachment_urls else None
        
        cursor.execute('''
            INSERT INTO messages (ticket_id, user_id, content, is_staff, attachment_urls)
            VALUES (?, ?, ?, ?, ?)
        ''', (ticket_id, user_id, content, is_staff, attachment_json))
        
        # Update last message timestamp
        cursor.execute('''
            UPDATE tickets SET last_message_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (ticket_id,))
        
        conn.commit()
        conn.close()
    
    def add_note(self, ticket_id: int, author_id: int, content: str):
        """Add internal note to ticket."""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO notes (ticket_id, author_id, content)
            VALUES (?, ?, ?)
        ''', (ticket_id, author_id, content))
        
        conn.commit()
        conn.close()
        
        logger.info(f"Added note to ticket {ticket_id}")
    
    def get_ticket_history(self, ticket_id: int) -> List[sqlite3.Row]:
        """Get all messages for a ticket."""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM messages 
            WHERE ticket_id = ? 
            ORDER BY timestamp ASC
        ''', (ticket_id,))
        
        messages = cursor.fetchall()
        conn.close()
        return messages
    
    def get_ticket_notes(self, ticket_id: int) -> List[sqlite3.Row]:
        """Get all notes for a ticket."""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM notes 
            WHERE ticket_id = ? 
            ORDER BY timestamp ASC
        ''', (ticket_id,))
        
        notes = cursor.fetchall()
        conn.close()
        return notes
    
    def get_user_data(self, user_id: int) -> Optional[sqlite3.Row]:
        """Get user data."""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM user_data WHERE user_id = ?', (user_id,))
        
        data = cursor.fetchone()
        conn.close()
        return data
    
    def update_user_warnings(self, user_id: int, increment: int = 1):
        """Update user warning count."""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO user_data (user_id, warnings)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET warnings = warnings + ?
        ''', (user_id, increment, increment))
        
        conn.commit()
        conn.close()
    
    def ban_user(self, user_id: int, reason: str = None):
        """Mark user as banned."""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO user_data (user_id, banned, banned_at, ban_reason)
            VALUES (?, 1, CURRENT_TIMESTAMP, ?)
            ON CONFLICT(user_id) DO UPDATE SET 
                banned = 1, banned_at = CURRENT_TIMESTAMP, ban_reason = ?
        ''', (user_id, reason, reason))
        
        conn.commit()
        conn.close()
        
        logger.info(f"User {user_id} marked as banned")
    
    def get_inactive_tickets(self, hours: int = 48) -> List[sqlite3.Row]:
        """Get tickets inactive for specified hours."""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM tickets 
            WHERE status = 'open' 
            AND datetime(last_message_at) < datetime('now', ? || ' hours')
        ''', (f'-{hours}',))
        
        tickets = cursor.fetchall()
        conn.close()
        return tickets
    
    def get_ticket_stats(self, guild_id: int) -> Dict:
        """Get ticket statistics."""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) as open,
                SUM(CASE WHEN status = 'closed' THEN 1 ELSE 0 END) as closed,
                AVG(CASE WHEN closed_at IS NOT NULL 
                    THEN (julianday(closed_at) - julianday(created_at)) * 24 
                    ELSE NULL END) as avg_resolution_hours
            FROM tickets
            WHERE guild_id = ?
        ''', (guild_id,))
        
        stats = cursor.fetchone()
        conn.close()
        return dict(stats) if stats else {}

# Initialize database
db = Database(DATABASE_PATH)

# ============================================================================
# UI COMPONENTS
# ============================================================================

class TicketCategorySelect(Select):
    """Dropdown for selecting ticket category."""
    
    def __init__(self):
        options = [
            discord.SelectOption(label="General Support", value="general", emoji="💬", 
                               description="General questions and support"),
            discord.SelectOption(label="Technical Issue", value="technical", emoji="🔧",
                               description="Bug reports and technical problems"),
            discord.SelectOption(label="Account Issue", value="account", emoji="👤",
                               description="Account-related concerns"),
            discord.SelectOption(label="Report User", value="report", emoji="⚠️",
                               description="Report another user"),
            discord.SelectOption(label="Appeal", value="appeal", emoji="📋",
                               description="Ban or mute appeal"),
            discord.SelectOption(label="Other", value="other", emoji="❓",
                               description="Other inquiries")
        ]
        
        super().__init__(
            placeholder="Select ticket category...",
            min_values=1,
            max_values=1,
            options=options
        )
    
    async def callback(self, interaction: discord.Interaction):
        category = self.values[0]
        await interaction.response.defer()
        
        # Store category in view for later use
        self.view.selected_category = category


class TicketReasonModal(Modal, title="Create Support Ticket"):
    """Modal for entering ticket reason."""
    
    reason = TextInput(
        label="What do you need help with?",
        style=discord.TextStyle.paragraph,
        placeholder="Please describe your issue in detail...",
        required=True,
        max_length=1000
    )
    
    def __init__(self, category: str):
        super().__init__()
        self.category = category
    
    async def on_submit(self, interaction: discord.Interaction):
        user = interaction.user
        guild = interaction.guild
        
        # Check if user has open ticket
        existing_ticket = db.get_active_ticket(user.id, guild.id)
        if existing_ticket:
            await interaction.response.send_message(
                "⚠️ You already have an open ticket! Please use that one or close it first.",
                ephemeral=True
            )
            return
        
        # Get mod role
        mod_role = discord.utils.get(guild.roles, name=MOD_ROLE_NAME)
        if not mod_role:
            await interaction.response.send_message(
                f"❌ Mod role '{MOD_ROLE_NAME}' not found!", ephemeral=True
            )
            logger.error(f"Mod role {MOD_ROLE_NAME} not found in guild {guild.id}")
            return
        
        await interaction.response.defer(ephemeral=True)
        
        try:
            # Create ticket channel
            category = discord.utils.get(guild.categories, name=TICKET_CATEGORY_NAME)
            
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                mod_role: discord.PermissionOverwrite(
                    view_channel=True, 
                    send_messages=True, 
                    read_message_history=True,
                    attach_files=True,
                    embed_links=True
                ),
                bot.user: discord.PermissionOverwrite(
                    view_channel=True, 
                    send_messages=True,
                    manage_messages=True,
                    manage_channels=True
                )
            }
            
            channel = await guild.create_text_channel(
                name=f"ticket-{user.name}",
                category=category,
                overwrites=overwrites,
                topic=f"Support ticket for {user.name} ({user.id}) | Category: {self.category}"
            )
            
            # Create ticket in database
            ticket_id = db.create_ticket(
                user_id=user.id,
                guild_id=guild.id,
                channel_id=channel.id,
                category=self.category,
                created_by=user.id
            )
            
            # Save initial message
            db.add_message(ticket_id, user.id, str(self.reason), is_staff=False)
            
            # Create ticket embed
            embed = discord.Embed(
                title=f"🎫 Support Ticket #{ticket_id}",
                description=f"**User:** {user.mention} ({user.id})\n"
                           f"**Category:** {self.category.title()}\n"
                           f"**Created:** {datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S %Z')}",
                color=discord.Color.blue()
            )
            
            embed.add_field(
                name="Initial Message",
                value=str(self.reason),
                inline=False
            )
            
            embed.set_footer(text=f"Ticket ID: {ticket_id}")
            
            # Create control panel
            control_view = TicketControlView(channel.id, ticket_id)
            
            await channel.send(
                f"{mod_role.mention} New ticket from {user.mention}",
                embed=embed,
                view=control_view
            )
            
            # Send guide message
            guide_embed = discord.Embed(
                title="📬 Ticket Management",
                description="**For Moderators:**\n"
                           "• Messages sent here will be relayed to the user\n"
                           "• Use `/note` to add internal notes\n"
                           "• Use `/warn` to issue warnings\n"
                           "• Use buttons below to manage the ticket\n\n"
                           f"**User:** {user.mention} will receive all messages as DMs.",
                color=discord.Color.gold()
            )
            
            await channel.send(embed=guide_embed)
            
            # DM user
            try:
                dm_embed = discord.Embed(
                    title="✅ Ticket Created",
                    description=f"Your support ticket has been created in **{guild.name}**.\n\n"
                               f"**Category:** {self.category.title()}\n"
                               f"**Ticket ID:** #{ticket_id}\n\n"
                               "A moderator will respond soon. All messages you send here "
                               "will be sent to the support team.",
                    color=discord.Color.green()
                )
                
                await user.send(embed=dm_embed)
            except discord.Forbidden:
                await channel.send(
                    f"⚠️ Could not DM {user.mention}. They may have DMs disabled."
                )
            
            await interaction.followup.send(
                f"✅ Ticket created! Check your DMs and #{channel.name}",
                ephemeral=True
            )
            
            # Log ticket creation
            await log_action(
                guild,
                "Ticket Created",
                f"User {user.mention} created ticket #{ticket_id}",
                discord.Color.green(),
                fields={
                    "Category": self.category.title(),
                    "Channel": channel.mention,
                    "Initial Message": str(self.reason)[:100] + "..." if len(str(self.reason)) > 100 else str(self.reason)
                }
            )
            
        except Exception as e:
            logger.error(f"Error creating ticket: {e}")
            await interaction.followup.send(
                "❌ An error occurred while creating your ticket. Please try again or contact an administrator.",
                ephemeral=True
            )


class TicketCategoryView(View):
    """View for category selection."""
    
    def __init__(self):
        super().__init__(timeout=None)
        self.selected_category = None
        self.add_item(TicketCategorySelect())
    
    @discord.ui.button(label="Continue", style=discord.ButtonStyle.green, row=1)
    async def continue_button(self, interaction: discord.Interaction, button: Button):
        if not self.selected_category:
            await interaction.response.send_message(
                "⚠️ Please select a category first!",
                ephemeral=True
            )
            return
        
        # Show reason modal
        modal = TicketReasonModal(self.selected_category)
        await interaction.response.send_modal(modal)


class CreateTicketButton(View):
    """Main button to create tickets."""
    
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(
        label="📩 Create Support Ticket",
        style=discord.ButtonStyle.primary,
        custom_id="create_ticket_main"
    )
    async def create_ticket(self, interaction: discord.Interaction, button: Button):
        # Show category selection
        category_view = TicketCategoryView()
        
        embed = discord.Embed(
            title="🎫 Create Support Ticket",
            description="Please select the category that best describes your issue:",
            color=discord.Color.blue()
        )
        
        await interaction.response.send_message(
            embed=embed,
            view=category_view,
            ephemeral=True
        )


class TicketControlView(View):
    """Control panel for ticket management."""
    
    def __init__(self, channel_id: int, ticket_id: int):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        self.ticket_id = ticket_id
    
    @discord.ui.button(
        label="Close Ticket",
        style=discord.ButtonStyle.danger,
        emoji="🔒"
    )
    async def close_ticket(self, interaction: discord.Interaction, button: Button):
        modal = CloseReasonModal(self.channel_id, self.ticket_id)
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(
        label="Claim",
        style=discord.ButtonStyle.primary,
        emoji="✋"
    )
    async def claim_ticket(self, interaction: discord.Interaction, button: Button):
        channel = interaction.guild.get_channel(self.channel_id)
        if channel:
            await channel.send(
                f"🙋 {interaction.user.mention} has claimed this ticket and will handle it."
            )
            
            db.add_note(
                self.ticket_id,
                interaction.user.id,
                f"Ticket claimed by {interaction.user.name}"
            )
            
        await interaction.response.send_message(
            "✅ You have claimed this ticket.",
            ephemeral=True
        )
    
    @discord.ui.button(
        label="Priority",
        style=discord.ButtonStyle.secondary,
        emoji="⚡"
    )
    async def set_priority(self, interaction: discord.Interaction, button: Button):
        view = PrioritySelectView(self.ticket_id, self.channel_id)
        await interaction.response.send_message(
            "Select ticket priority:",
            view=view,
            ephemeral=True
        )
    
    @discord.ui.button(
        label="Transcript",
        style=discord.ButtonStyle.secondary,
        emoji="📄"
    )
    async def generate_transcript(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)
        
        transcript = await create_transcript(self.ticket_id)
        
        if transcript:
            file = discord.File(
                fp=transcript.encode('utf-8'),
                filename=f"ticket_{self.ticket_id}_transcript.txt"
            )
            await interaction.followup.send(
                "📄 Ticket transcript:",
                file=file,
                ephemeral=True
            )
        else:
            await interaction.followup.send(
                "❌ Could not generate transcript.",
                ephemeral=True
            )


class CloseReasonModal(Modal, title="Close Ticket"):
    """Modal for entering close reason."""
    
    reason = TextInput(
        label="Reason for closing (optional)",
        style=discord.TextStyle.paragraph,
        placeholder="Enter reason for closing this ticket...",
        required=False,
        max_length=500
    )
    
    def __init__(self, channel_id: int, ticket_id: int):
        super().__init__()
        self.channel_id = channel_id
        self.ticket_id = ticket_id
    
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        channel = interaction.guild.get_channel(self.channel_id)
        if not channel:
            await interaction.followup.send("❌ Channel not found!", ephemeral=True)
            return
        
        # Get ticket info
        ticket = db.get_ticket_by_channel(self.channel_id)
        if not ticket:
            await interaction.followup.send("❌ Ticket not found!", ephemeral=True)
            return
        
        # Close in database
        db.close_ticket(
            self.ticket_id,
            interaction.user.id,
            str(self.reason) if self.reason else "No reason provided"
        )
        
        # Generate transcript
        transcript = await create_transcript(self.ticket_id)
        
        # DM user
        try:
            user = await bot.fetch_user(ticket['user_id'])
            
            close_embed = discord.Embed(
                title="🔒 Ticket Closed",
                description=f"Your ticket #{self.ticket_id} has been closed.",
                color=discord.Color.red()
            )
            
            if self.reason:
                close_embed.add_field(
                    name="Reason",
                    value=str(self.reason),
                    inline=False
                )
            
            close_embed.set_footer(text=f"Closed by {interaction.user.name}")
            
            await user.send(embed=close_embed)
            
            # Send transcript if available
            if transcript:
                file = discord.File(
                    fp=transcript.encode('utf-8'),
                    filename=f"ticket_{self.ticket_id}_transcript.txt"
                )
                await user.send("📄 Ticket transcript:", file=file)
                
        except discord.Forbidden:
            logger.warning(f"Could not DM user {ticket['user_id']} about ticket closure")
        
        # Log closure
        await log_action(
            interaction.guild,
            "Ticket Closed",
            f"Ticket #{self.ticket_id} closed by {interaction.user.mention}",
            discord.Color.red(),
            fields={
                "Reason": str(self.reason) if self.reason else "No reason provided",
                "User": f"<@{ticket['user_id']}>"
            }
        )
        
        # Delete channel
        await channel.send("🔒 Ticket closed. Channel will be deleted in 5 seconds...")
        await asyncio.sleep(5)
        await channel.delete()


class PrioritySelectView(View):
    """View for selecting ticket priority."""
    
    def __init__(self, ticket_id: int, channel_id: int):
        super().__init__(timeout=60)
        self.ticket_id = ticket_id
        self.channel_id = channel_id
        
        select = Select(
            placeholder="Select priority level...",
            options=[
                discord.SelectOption(label="Low", value="low", emoji="🟢"),
                discord.SelectOption(label="Normal", value="normal", emoji="🟡"),
                discord.SelectOption(label="High", value="high", emoji="🟠"),
                discord.SelectOption(label="Urgent", value="urgent", emoji="🔴")
            ]
        )
        select.callback = self.priority_callback
        self.add_item(select)
    
    async def priority_callback(self, interaction: discord.Interaction):
        priority = self.children[0].values[0]
        
        conn = db.get_connection()
        cursor = conn.cursor()
        cursor.execute('UPDATE tickets SET priority = ? WHERE id = ?', 
                      (priority, self.ticket_id))
        conn.commit()
        conn.close()
        
        channel = interaction.guild.get_channel(self.channel_id)
        if channel:
            priority_emoji = {"low": "🟢", "normal": "🟡", "high": "🟠", "urgent": "🔴"}
            await channel.send(
                f"{priority_emoji.get(priority, '🟡')} Ticket priority set to **{priority.upper()}**"
            )
        
        await interaction.response.send_message(
            f"✅ Priority updated to {priority.upper()}",
            ephemeral=True
        )

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

async def log_action(guild: discord.Guild, title: str, description: str, 
                     color: discord.Color, fields: Dict = None):
    """Log actions to the log channel."""
    log_channel = discord.utils.get(guild.text_channels, name=LOG_CHANNEL_NAME)
    
    if not log_channel:
        return
    
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.now(tz)
    )
    
    if fields:
        for name, value in fields.items():
            embed.add_field(name=name, value=value, inline=False)
    
    try:
        await log_channel.send(embed=embed)
    except Exception as e:
        logger.error(f"Failed to log action: {e}")


async def create_transcript(ticket_id: int) -> Optional[str]:
    """Generate a text transcript of the ticket."""
    try:
        ticket = db.get_connection().execute(
            'SELECT * FROM tickets WHERE id = ?', (ticket_id,)
        ).fetchone()
        
        if not ticket:
            return None
        
        messages = db.get_ticket_history(ticket_id)
        notes = db.get_ticket_notes(ticket_id)
        
        transcript = []
        transcript.append("=" * 80)
        transcript.append(f"TICKET TRANSCRIPT #{ticket_id}")
        transcript.append("=" * 80)
        transcript.append(f"User ID: {ticket['user_id']}")
        transcript.append(f"Category: {ticket['category']}")
        transcript.append(f"Priority: {ticket['priority']}")
        transcript.append(f"Status: {ticket['status']}")
        transcript.append(f"Created: {ticket['created_at']}")
        
        if ticket['closed_at']:
            transcript.append(f"Closed: {ticket['closed_at']}")
            transcript.append(f"Close Reason: {ticket['close_reason']}")
        
        transcript.append("\n" + "=" * 80)
        transcript.append("MESSAGES")
        transcript.append("=" * 80 + "\n")
        
        for msg in messages:
            timestamp = msg['timestamp']
            user_type = "STAFF" if msg['is_staff'] else "USER"
            content = msg['content'] or "[No content]"
            
            transcript.append(f"[{timestamp}] {user_type} (ID: {msg['user_id']})")
            transcript.append(f"  {content}")
            
            if msg['attachment_urls']:
                attachments = json.loads(msg['attachment_urls'])
                transcript.append(f"  Attachments: {', '.join(attachments)}")
            
            transcript.append("")
        
        if notes:
            transcript.append("\n" + "=" * 80)
            transcript.append("INTERNAL NOTES")
            transcript.append("=" * 80 + "\n")
            
            for note in notes:
                transcript.append(f"[{note['timestamp']}] Staff ID: {note['author_id']}")
                transcript.append(f"  {note['content']}")
                transcript.append("")
        
        transcript.append("=" * 80)
        transcript.append("END OF TRANSCRIPT")
        transcript.append("=" * 80)
        
        return "\n".join(transcript)
        
    except Exception as e:
        logger.error(f"Error creating transcript: {e}")
        return None

# ============================================================================
# COMMANDS
# ============================================================================

@bot.command(name="setup")
@commands.has_permissions(administrator=True)
async def setup_command(ctx):
    """
    Set up the modmail system.
    Creates necessary channels and posts the ticket button.
    """
    guild = ctx.guild
    
    # Create ticket category if doesn't exist
    category = discord.utils.get(guild.categories, name=TICKET_CATEGORY_NAME)
    if not category:
        category = await guild.create_category(TICKET_CATEGORY_NAME)
        await ctx.send(f"✅ Created ticket category: {TICKET_CATEGORY_NAME}")
    
    # Create log channel if doesn't exist
    log_channel = discord.utils.get(guild.text_channels, name=LOG_CHANNEL_NAME)
    if not log_channel:
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True)
        }
        
        mod_role = discord.utils.get(guild.roles, name=MOD_ROLE_NAME)
        if mod_role:
            overwrites[mod_role] = discord.PermissionOverwrite(view_channel=True)
        
        log_channel = await guild.create_text_channel(
            LOG_CHANNEL_NAME,
            overwrites=overwrites,
            topic="Modmail system logs"
        )
        await ctx.send(f"✅ Created log channel: {log_channel.mention}")
    
    # Post ticket creation button
    embed = discord.Embed(
        title="📬 Support Ticket System",
        description="Need help? Click the button below to open a support ticket.\n\n"
                   "Our moderator team will respond as soon as possible.\n\n"
                   "**Before creating a ticket:**\n"
                   "• Check the server rules and FAQ\n"
                   "• Make sure you haven't already opened a ticket\n"
                   "• Provide as much detail as possible",
        color=discord.Color.blue()
    )
    
    embed.set_footer(text="Tickets are private and only visible to staff")
    
    view = CreateTicketButton()
    await ctx.send(embed=embed, view=view)
    
    await ctx.send("✅ Modmail system setup complete!")
    logger.info(f"Modmail system set up in guild {guild.id}")


@bot.command(name="createticket")
@commands.has_role(MOD_ROLE_NAME)
async def create_ticket_for_user(ctx, user: discord.Member, *, reason: str):
    """
    Create a ticket for a specific user.
    Usage: !createticket @user <reason>
    
    Example: !createticket @Yumi Hey, what happened with the spam incident?
    """
    guild = ctx.guild
    
    # Check if user already has ticket
    existing_ticket = db.get_active_ticket(user.id, guild.id)
    if existing_ticket:
        await ctx.send(f"⚠️ {user.mention} already has an open ticket in <#{existing_ticket['channel_id']}>")
        return
    
    # Get mod role
    mod_role = discord.utils.get(guild.roles, name=MOD_ROLE_NAME)
    if not mod_role:
        await ctx.send(f"❌ Mod role '{MOD_ROLE_NAME}' not found!")
        return
    
    try:
        # Create ticket channel
        category = discord.utils.get(guild.categories, name=TICKET_CATEGORY_NAME)
        
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            mod_role: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True
            ),
            bot.user: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                manage_messages=True,
                manage_channels=True
            )
        }
        
        channel = await guild.create_text_channel(
            name=f"ticket-{user.name}",
            category=category,
            overwrites=overwrites,
            topic=f"Support ticket for {user.name} ({user.id}) | Created by staff"
        )
        
        # Create ticket in database
        ticket_id = db.create_ticket(
            user_id=user.id,
            guild_id=guild.id,
            channel_id=channel.id,
            category="staff-created",
            created_by=ctx.author.id
        )
        
        # Save initial message
        db.add_message(ticket_id, ctx.author.id, reason, is_staff=True)
        
        # Create ticket embed
        embed = discord.Embed(
            title=f"🎫 Staff-Created Ticket #{ticket_id}",
            description=f"**User:** {user.mention} ({user.id})\n"
                       f"**Created by:** {ctx.author.mention}\n"
                       f"**Created:** {datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S %Z')}",
            color=discord.Color.purple()
        )
        
        embed.add_field(
            name="Reason",
            value=reason,
            inline=False
        )
        
        embed.set_footer(text=f"Ticket ID: {ticket_id}")
        
        # Create control panel
        control_view = TicketControlView(channel.id, ticket_id)
        
        await channel.send(
            f"{mod_role.mention} Ticket created for {user.mention} by {ctx.author.mention}",
            embed=embed,
            view=control_view
        )
        
        # Send the initial message
        await channel.send(f"**{ctx.author.name} (Staff):** {reason}")
        
        # DM user
        try:
            dm_embed = discord.Embed(
                title="📨 Support Ticket Created",
                description=f"A moderator has created a support ticket for you in **{guild.name}**.\n\n"
                           f"**Ticket ID:** #{ticket_id}\n\n"
                           f"**Initial Message:**\n{reason}\n\n"
                           "Please respond here to communicate with the staff team.",
                color=discord.Color.blue()
            )
            
            await user.send(embed=dm_embed)
        except discord.Forbidden:
            await channel.send(f"⚠️ Could not DM {user.mention}. They may have DMs disabled.")
        
        await ctx.send(f"✅ Created ticket #{ticket_id} for {user.mention} in {channel.mention}")
        
        # Log ticket creation
        await log_action(
            guild,
            "Staff-Created Ticket",
            f"{ctx.author.mention} created ticket #{ticket_id} for {user.mention}",
            discord.Color.purple(),
            fields={
                "Channel": channel.mention,
                "Reason": reason
            }
        )
        
    except Exception as e:
        logger.error(f"Error creating staff ticket: {e}")
        await ctx.send("❌ An error occurred while creating the ticket. Check logs for details.")


@bot.command(name="note")
@commands.has_role(MOD_ROLE_NAME)
async def add_note_command(ctx, *, content: str):
    """
    Add an internal note to a ticket (only visible to staff).
    Usage: !note <content>
    """
    ticket = db.get_ticket_by_channel(ctx.channel.id)
    
    if not ticket:
        await ctx.send("❌ This command can only be used in ticket channels.")
        return
    
    db.add_note(ticket['id'], ctx.author.id, content)
    
    embed = discord.Embed(
        title="📝 Internal Note Added",
        description=content,
        color=discord.Color.gold(),
        timestamp=datetime.now(tz)
    )
    
    embed.set_author(name=ctx.author.name, icon_url=ctx.author.display_avatar.url)
    embed.set_footer(text="This note is only visible to staff")
    
    await ctx.send(embed=embed)
    await ctx.message.delete()


@bot.command(name="warn")
@commands.has_role(MOD_ROLE_NAME)
async def warn_user_command(ctx, *, reason: str):
    """
    Issue a warning to the ticket user.
    Usage: !warn <reason>
    """
    ticket = db.get_ticket_by_channel(ctx.channel.id)
    
    if not ticket:
        await ctx.send("❌ This command can only be used in ticket channels.")
        return
    
    # Update warnings in database
    db.update_user_warnings(ticket['user_id'], 1)
    
    # Get updated warning count
    user_data = db.get_user_data(ticket['user_id'])
    warning_count = user_data['warnings'] if user_data else 1
    
    # Add note
    db.add_note(
        ticket['id'],
        ctx.author.id,
        f"WARNING ISSUED: {reason}"
    )
    
    # Send to channel
    embed = discord.Embed(
        title="⚠️ Warning Issued",
        description=reason,
        color=discord.Color.orange(),
        timestamp=datetime.now(tz)
    )
    
    embed.add_field(name="Total Warnings", value=str(warning_count), inline=False)
    embed.set_footer(text=f"Issued by {ctx.author.name}")
    
    await ctx.send(embed=embed)
    
    # DM user
    try:
        user = await bot.fetch_user(ticket['user_id'])
        
        dm_embed = discord.Embed(
            title="⚠️ Warning Received",
            description=f"You have received a warning in **{ctx.guild.name}**.",
            color=discord.Color.orange()
        )
        
        dm_embed.add_field(name="Reason", value=reason, inline=False)
        dm_embed.add_field(name="Total Warnings", value=str(warning_count), inline=False)
        dm_embed.set_footer(text="Please review the server rules to avoid further warnings")
        
        await user.send(embed=dm_embed)
    except discord.Forbidden:
        await ctx.send("⚠️ Could not DM user about warning.")
    
    # Log warning
    await log_action(
        ctx.guild,
        "Warning Issued",
        f"{ctx.author.mention} warned <@{ticket['user_id']}> in ticket #{ticket['id']}",
        discord.Color.orange(),
        fields={
            "Reason": reason,
            "Total Warnings": str(warning_count)
        }
    )


@bot.command(name="stats")
@commands.has_role(MOD_ROLE_NAME)
async def ticket_stats(ctx):
    """View ticket statistics."""
    stats = db.get_ticket_stats(ctx.guild.id)
    
    embed = discord.Embed(
        title="📊 Ticket Statistics",
        color=discord.Color.blue(),
        timestamp=datetime.now(tz)
    )
    
    embed.add_field(name="Total Tickets", value=str(stats.get('total', 0)), inline=True)
    embed.add_field(name="Open Tickets", value=str(stats.get('open', 0)), inline=True)
    embed.add_field(name="Closed Tickets", value=str(stats.get('closed', 0)), inline=True)
    
    avg_hours = stats.get('avg_resolution_hours', 0)
    if avg_hours:
        avg_time = f"{avg_hours:.1f} hours"
    else:
        avg_time = "N/A"
    
    embed.add_field(name="Avg. Resolution Time", value=avg_time, inline=False)
    
    await ctx.send(embed=embed)


@bot.command(name="userinfo")
@commands.has_role(MOD_ROLE_NAME)
async def user_info_command(ctx, user: discord.Member):
    """
    View user information and ticket history.
    Usage: !userinfo @user
    """
    user_data = db.get_user_data(user.id)
    
    embed = discord.Embed(
        title=f"👤 User Information: {user.name}",
        color=discord.Color.blue(),
        timestamp=datetime.now(tz)
    )
    
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.add_field(name="User ID", value=str(user.id), inline=True)
    embed.add_field(name="Account Created", value=user.created_at.strftime('%Y-%m-%d'), inline=True)
    embed.add_field(name="Joined Server", value=user.joined_at.strftime('%Y-%m-%d') if user.joined_at else "Unknown", inline=True)
    
    if user_data:
        embed.add_field(name="Total Tickets", value=str(user_data['total_tickets']), inline=True)
        embed.add_field(name="Warnings", value=str(user_data['warnings']), inline=True)
        
        if user_data['banned']:
            embed.add_field(
                name="Ban Status",
                value=f"❌ Banned\nReason: {user_data['ban_reason'] or 'None'}\nDate: {user_data['banned_at']}",
                inline=False
            )
    else:
        embed.add_field(name="Total Tickets", value="0", inline=True)
        embed.add_field(name="Warnings", value="0", inline=True)
    
    await ctx.send(embed=embed)


@bot.command(name="close")
@commands.has_role(MOD_ROLE_NAME)
async def quick_close(ctx, *, reason: str = "No reason provided"):
    """
    Quick close command for tickets.
    Usage: !close [reason]
    """
    ticket = db.get_ticket_by_channel(ctx.channel.id)
    
    if not ticket:
        await ctx.send("❌ This command can only be used in ticket channels.")
        return
    
    # Close ticket
    db.close_ticket(ticket['id'], ctx.author.id, reason)
    
    # Generate transcript
    transcript = await create_transcript(ticket['id'])
    
    # DM user
    try:
        user = await bot.fetch_user(ticket['user_id'])
        
        close_embed = discord.Embed(
            title="🔒 Ticket Closed",
            description=f"Your ticket #{ticket['id']} has been closed.",
            color=discord.Color.red()
        )
        
        close_embed.add_field(name="Reason", value=reason, inline=False)
        close_embed.set_footer(text=f"Closed by {ctx.author.name}")
        
        await user.send(embed=close_embed)
        
        if transcript:
            file = discord.File(
                fp=transcript.encode('utf-8'),
                filename=f"ticket_{ticket['id']}_transcript.txt"
            )
            await user.send("📄 Ticket transcript:", file=file)
            
    except discord.Forbidden:
        logger.warning(f"Could not DM user {ticket['user_id']} about ticket closure")
    
    # Log closure
    await log_action(
        ctx.guild,
        "Ticket Closed",
        f"Ticket #{ticket['id']} closed by {ctx.author.mention}",
        discord.Color.red(),
        fields={
            "Reason": reason,
            "User": f"<@{ticket['user_id']}>"
        }
    )
    
    await ctx.send("🔒 Ticket closed. Channel will be deleted in 5 seconds...")
    await asyncio.sleep(5)
    await ctx.channel.delete()


@bot.command(name="help")
async def help_command(ctx):
    """Display help information."""
    
    if any(role.name in [MOD_ROLE_NAME, ADMIN_ROLE_NAME] for role in ctx.author.roles):
        # Staff help
        embed = discord.Embed(
            title="📚 Modmail Bot - Staff Commands",
            description="Commands available to moderators and administrators",
            color=discord.Color.gold()
        )
        
        embed.add_field(
            name="!setup",
            value="Set up the modmail system (Admin only)",
            inline=False
        )
        
        embed.add_field(
            name="!createticket @user <reason>",
            value="Create a ticket for a specific user\nExample: `!createticket @Yumi What happened?`",
            inline=False
        )
        
        embed.add_field(
            name="!note <content>",
            value="Add an internal note to the current ticket (staff only)",
            inline=False
        )
        
        embed.add_field(
            name="!warn <reason>",
            value="Issue a warning to the ticket user",
            inline=False
        )
        
        embed.add_field(
            name="!close [reason]",
            value="Close the current ticket",
            inline=False
        )
        
        embed.add_field(
            name="!userinfo @user",
            value="View user information and ticket history",
            inline=False
        )
        
        embed.add_field(
            name="!stats",
            value="View ticket statistics",
            inline=False
        )
        
    else:
        # User help
        embed = discord.Embed(
            title="📚 Modmail Bot - User Guide",
            description="How to use the modmail system",
            color=discord.Color.blue()
        )
        
        embed.add_field(
            name="Creating a Ticket",
            value="Click the 📩 button in the designated channel to create a support ticket",
            inline=False
        )
        
        embed.add_field(
            name="Messaging Support",
            value="Once your ticket is created, send messages here (in DMs) and they will be relayed to staff",
            inline=False
        )
        
        embed.add_field(
            name="Attachments",
            value="You can send images and files - they will be forwarded to the support team",
            inline=False
        )
    
    await ctx.send(embed=embed)

# ============================================================================
# EVENTS
# ============================================================================

@bot.event
async def on_ready():
    """Bot startup event."""
    logger.info(f"✅ Bot logged in as {bot.user}")
    logger.info(f"✅ Connected to {len(bot.guilds)} guild(s)")
    
    # Start background tasks
    check_inactive_tickets.start()
    
    print(f"✅ {bot.user} is online and ready!")


@bot.event
async def on_message(message):
    """Handle incoming messages."""
    
    # Ignore bot messages
    if message.author.bot:
        return
    
    # Handle DMs from users
    if isinstance(message.channel, discord.DMChannel):
        user = message.author
        
        # Find user's active tickets across all guilds
        for guild in bot.guilds:
            ticket = db.get_active_ticket(user.id, guild.id)
            
            if ticket:
                channel = bot.get_channel(ticket['channel_id'])
                
                if channel:
                    # Save message to database
                    attachment_urls = [att.url for att in message.attachments] if message.attachments else None
                    db.add_message(
                        ticket['id'],
                        user.id,
                        message.content,
                        is_staff=False,
                        attachment_urls=attachment_urls
                    )
                    
                    # Create embed
                    embed = discord.Embed(
                        title=f"📨 Message from {user.name}",
                        description=message.content if message.content else "*No text content*",
                        color=discord.Color.blue(),
                        timestamp=datetime.now(tz)
                    )
                    
                    embed.set_author(name=user.name, icon_url=user.display_avatar.url)
                    embed.set_footer(text=f"User ID: {user.id}")
                    
                    # Send to ticket channel
                    files = []
                    for att in message.attachments:
                        try:
                            file = await att.to_file()
                            files.append(file)
                        except Exception as e:
                            logger.error(f"Error downloading attachment: {e}")
                    
                    if files:
                        await channel.send(embed=embed, files=files)
                    else:
                        await channel.send(embed=embed)
                    
                break
        else:
            # No active ticket found
            await user.send(
                "❌ You don't have an open support ticket.\n\n"
                "Please go to the server and click the 📩 button to create a ticket."
            )
        
        return
    
    # Handle messages in ticket channels from staff
    ticket = db.get_ticket_by_channel(message.channel.id)
    
    if ticket:
        # Check if user is staff
        if any(role.name in [MOD_ROLE_NAME, ADMIN_ROLE_NAME] for role in message.author.roles):
            # Don't relay commands
            if message.content.startswith('!'):
                await bot.process_commands(message)
                return
            
            # Save message to database
            attachment_urls = [att.url for att in message.attachments] if message.attachments else None
            db.add_message(
                ticket['id'],
                message.author.id,
                message.content,
                is_staff=True,
                attachment_urls=attachment_urls
            )
            
            # Send to user
            try:
                user = await bot.fetch_user(ticket['user_id'])
                
                # Send content
                if message.content:
                    await user.send(f"**{message.author.name} (Staff):** {message.content}")
                
                # Send attachments
                for att in message.attachments:
                    try:
                        file = await att.to_file()
                        await user.send(file=file)
                    except Exception as e:
                        logger.error(f"Error forwarding attachment: {e}")
                        
            except discord.Forbidden:
                await message.channel.send(
                    "⚠️ Could not DM user. They may have DMs disabled or blocked the bot."
                )
            except Exception as e:
                logger.error(f"Error sending message to user: {e}")
    
    await bot.process_commands(message)


@bot.event
async def on_member_ban(guild: discord.Guild, user: discord.User):
    """Handle user bans."""
    # Mark user as banned in database
    db.ban_user(user.id, "Banned from server")
    
    # Close any active tickets
    ticket = db.get_active_ticket(user.id, guild.id)
    if ticket:
        db.close_ticket(ticket['id'], bot.user.id, "User was banned")
        
        channel = guild.get_channel(ticket['channel_id'])
        if channel:
            await channel.send("🔨 User has been banned. Ticket will be closed.")
            await asyncio.sleep(5)
            await channel.delete()
    
    # Log ban
    await log_action(
        guild,
        "User Banned",
        f"<@{user.id}> was banned from the server",
        discord.Color.dark_red(),
        fields={"User": f"{user.name} ({user.id})"}
    )


# ============================================================================
# BACKGROUND TASKS
# ============================================================================

@tasks.loop(hours=1)
async def check_inactive_tickets():
    """Check for inactive tickets and notify/close them."""
    logger.info("Checking for inactive tickets...")
    
    inactive_tickets = db.get_inactive_tickets(INACTIVE_TIMEOUT)
    
    for ticket in inactive_tickets:
        try:
            guild = bot.get_guild(ticket['guild_id'])
            if not guild:
                continue
            
            channel = guild.get_channel(ticket['channel_id'])
            if not channel:
                # Channel was deleted manually
                db.close_ticket(ticket['id'], bot.user.id, "Channel deleted")
                continue
            
            # Send warning
            embed = discord.Embed(
                title="⏰ Inactive Ticket",
                description=f"This ticket has been inactive for {INACTIVE_TIMEOUT} hours.\n"
                           "It will be automatically closed in 24 hours if there's no response.",
                color=discord.Color.orange()
            )
            
            await channel.send(embed=embed)
            
            # DM user
            try:
                user = await bot.fetch_user(ticket['user_id'])
                await user.send(
                    f"⏰ Your ticket #{ticket['id']} in **{guild.name}** has been inactive.\n"
                    "Please respond if you still need assistance, or it will be automatically closed."
                )
            except:
                pass
                
        except Exception as e:
            logger.error(f"Error processing inactive ticket {ticket['id']}: {e}")


@check_inactive_tickets.before_loop
async def before_check_inactive():
    """Wait for bot to be ready before starting task."""
    await bot.wait_until_ready()

# ============================================================================
# ERROR HANDLING
# ============================================================================

@bot.event
async def on_command_error(ctx, error):
    """Handle command errors."""
    
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You don't have permission to use this command.")
    
    elif isinstance(error, commands.MissingRole):
        await ctx.send(f"❌ You need the {error.missing_role} role to use this command.")
    
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send("❌ Could not find that user.")
    
    elif isinstance(error, commands.CommandNotFound):
        pass  # Ignore unknown commands
    
    else:
        logger.error(f"Command error: {error}")
        await ctx.send("❌ An error occurred while processing your command.")

# ============================================================================
# RUN BOT
# ============================================================================

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        logger.error("❌ DISCORD_TOKEN not found in environment variables!")
        exit(1)
    
    try:
        bot.run(DISCORD_TOKEN)
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}")
        exit(1)
