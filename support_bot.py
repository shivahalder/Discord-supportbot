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
- User-initiated ticket closing
- Persistent close button with auto-update
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
from typing import Optional, Dict, List, Any
from dotenv import load_dotenv
import logging
from pathlib import Path
import aiosqlite

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
# ASYNC DATABASE SETUP
# ============================================================================

class AsyncDatabase:
    """Handles all database operations for the modmail system asynchronously."""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
    
    async def get_connection(self) -> aiosqlite.Connection:
        """Create a new database connection."""
        conn = await aiosqlite.connect(self.db_path)
        conn.row_factory = aiosqlite.Row
        return conn
    
    async def init_database(self):
        """Initialize database tables."""
        async with await self.get_connection() as conn:
            # Tickets table
            await conn.execute('''
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
            await conn.execute('''
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
            await conn.execute('''
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
            await conn.execute('''
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
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS config (
                    guild_id INTEGER PRIMARY KEY,
                    ticket_counter INTEGER DEFAULT 0,
                    log_channel_id INTEGER,
                    ticket_category_id INTEGER
                )
            ''')
            
            await conn.commit()
            
        logger.info("Database initialized successfully")
    
    async def create_ticket(self, user_id: int, guild_id: int, channel_id: int, 
                           category: str = "general", priority: str = "normal",
                           created_by: Optional[int] = None) -> int:
        """Create a new ticket and return ticket ID."""
        async with await self.get_connection() as conn:
            cursor = await conn.execute('''
                INSERT INTO tickets (user_id, channel_id, guild_id, category, priority, created_by)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (user_id, channel_id, guild_id, category, priority, created_by))
            
            ticket_id = cursor.lastrowid
            
            # Update user ticket count
            await conn.execute('''
                INSERT INTO user_data (user_id, total_tickets)
                VALUES (?, 1)
                ON CONFLICT(user_id) DO UPDATE SET total_tickets = total_tickets + 1
            ''', (user_id,))
            
            await conn.commit()
            
        logger.info(f"Created ticket {ticket_id} for user {user_id}")
        return ticket_id
    
    async def get_active_ticket(self, user_id: int, guild_id: int) -> Optional[Dict]:
        """Get active ticket for a user."""
        async with await self.get_connection() as conn:
            cursor = await conn.execute('''
                SELECT * FROM tickets 
                WHERE user_id = ? AND guild_id = ? AND status = 'open'
                ORDER BY created_at DESC LIMIT 1
            ''', (user_id, guild_id))
            
            row = await cursor.fetchone()
            return dict(row) if row else None
    
    async def get_ticket_by_channel(self, channel_id: int) -> Optional[Dict]:
        """Get ticket by channel ID."""
        async with await self.get_connection() as conn:
            cursor = await conn.execute('SELECT * FROM tickets WHERE channel_id = ? AND status = "open"', 
                                       (channel_id,))
            
            row = await cursor.fetchone()
            return dict(row) if row else None
    
    async def get_ticket_by_id(self, ticket_id: int) -> Optional[Dict]:
        """Get ticket by ID."""
        async with await self.get_connection() as conn:
            cursor = await conn.execute('SELECT * FROM tickets WHERE id = ?', (ticket_id,))
            row = await cursor.fetchone()
            return dict(row) if row else None
    
    async def close_ticket(self, ticket_id: int, closed_by: int, reason: str = None):
        """Close a ticket."""
        async with await self.get_connection() as conn:
            await conn.execute('''
                UPDATE tickets 
                SET status = 'closed', closed_at = CURRENT_TIMESTAMP, closed_by = ?, close_reason = ?
                WHERE id = ?
            ''', (closed_by, reason, ticket_id))
            
            await conn.commit()
            
        logger.info(f"Closed ticket {ticket_id} by {closed_by}")
    
    async def add_message(self, ticket_id: int, user_id: int, content: str, 
                         is_staff: bool = False, attachment_urls: List[str] = None):
        """Add a message to ticket history."""
        async with await self.get_connection() as conn:
            attachment_json = json.dumps(attachment_urls) if attachment_urls else None
            
            await conn.execute('''
                INSERT INTO messages (ticket_id, user_id, content, is_staff, attachment_urls)
                VALUES (?, ?, ?, ?, ?)
            ''', (ticket_id, user_id, content, is_staff, attachment_json))
            
            # Update last message timestamp
            await conn.execute('''
                UPDATE tickets SET last_message_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (ticket_id,))
            
            await conn.commit()
    
    async def add_note(self, ticket_id: int, author_id: int, content: str):
        """Add internal note to ticket."""
        async with await self.get_connection() as conn:
            await conn.execute('''
                INSERT INTO notes (ticket_id, author_id, content)
                VALUES (?, ?, ?)
            ''', (ticket_id, author_id, content))
            
            await conn.commit()
            
        logger.info(f"Added note to ticket {ticket_id}")
    
    async def get_ticket_history(self, ticket_id: int) -> List[Dict]:
        """Get all messages for a ticket."""
        async with await self.get_connection() as conn:
            cursor = await conn.execute('''
                SELECT * FROM messages 
                WHERE ticket_id = ? 
                ORDER BY timestamp ASC
            ''', (ticket_id,))
            
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    
    async def get_ticket_notes(self, ticket_id: int) -> List[Dict]:
        """Get all notes for a ticket."""
        async with await self.get_connection() as conn:
            cursor = await conn.execute('''
                SELECT * FROM notes 
                WHERE ticket_id = ? 
                ORDER BY timestamp ASC
            ''', (ticket_id,))
            
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    
    async def get_user_data(self, user_id: int) -> Optional[Dict]:
        """Get user data."""
        async with await self.get_connection() as conn:
            cursor = await conn.execute('SELECT * FROM user_data WHERE user_id = ?', (user_id,))
            row = await cursor.fetchone()
            return dict(row) if row else None
    
    async def update_user_warnings(self, user_id: int, increment: int = 1):
        """Update user warning count."""
        async with await self.get_connection() as conn:
            await conn.execute('''
                INSERT INTO user_data (user_id, warnings)
                VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET warnings = warnings + ?
            ''', (user_id, increment, increment))
            
            await conn.commit()
    
    async def ban_user(self, user_id: int, reason: str = None):
        """Mark user as banned."""
        async with await self.get_connection() as conn:
            await conn.execute('''
                INSERT INTO user_data (user_id, banned, banned_at, ban_reason)
                VALUES (?, 1, CURRENT_TIMESTAMP, ?)
                ON CONFLICT(user_id) DO UPDATE SET 
                    banned = 1, banned_at = CURRENT_TIMESTAMP, ban_reason = ?
            ''', (user_id, reason, reason))
            
            await conn.commit()
            
        logger.info(f"User {user_id} marked as banned")
    
    async def get_inactive_tickets(self, hours: int = 48) -> List[Dict]:
        """Get tickets inactive for specified hours."""
        async with await self.get_connection() as conn:
            cursor = await conn.execute('''
                SELECT * FROM tickets 
                WHERE status = 'open' 
                AND datetime(last_message_at) < datetime('now', ? || ' hours')
            ''', (f'-{hours}',))
            
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    
    async def get_ticket_stats(self, guild_id: int) -> Dict:
        """Get ticket statistics."""
        async with await self.get_connection() as conn:
            cursor = await conn.execute('''
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
            
            row = await cursor.fetchone()
            return dict(row) if row else {}

# Initialize async database
db = AsyncDatabase(DATABASE_PATH)

# ============================================================================
# UI COMPONENTS
# ============================================================================

class PersistentTicketView(View):
    """Persistent view that auto-updates when messages are sent."""
    
    def __init__(self, ticket_id: int, channel_id: int):
        super().__init__(timeout=None)
        self.ticket_id = ticket_id
        self.channel_id = channel_id
        self.message = None
        self.update_lock = asyncio.Lock()
    
    async def update_display(self, channel: discord.TextChannel):
        """Update the persistent message with current ticket status."""
        async with self.update_lock:
            try:
                # Get latest ticket info
                ticket = await db.get_ticket_by_id(self.ticket_id)
                if not ticket or ticket['status'] == 'closed':
                    return
                
                # Get last few messages for preview
                messages = await db.get_ticket_history(self.ticket_id)
                recent_messages = messages[-3:] if messages else []
                
                # Create updated embed
                embed = discord.Embed(
                    title=f"🎫 Ticket #{self.ticket_id} - Control Panel",
                    description="**This panel updates automatically**\n\n"
                               f"**Status:** 🟢 Open\n"
                               f"**Priority:** {ticket['priority'].upper()}\n"
                               f"**Category:** {ticket['category'].title()}\n"
                               f"**Last Activity:** {ticket['last_message_at']}",
                    color=discord.Color.green()
                )
                
                if recent_messages:
                    preview = ""
                    for msg in recent_messages:
                        author_type = "👤 User" if not msg['is_staff'] else "🛡️ Staff"
                        content = msg['content'][:50] + "..." if len(msg['content']) > 50 else msg['content']
                        preview += f"{author_type}: {content}\n"
                    
                    embed.add_field(name="Recent Activity", value=preview or "No recent messages", inline=False)
                
                embed.set_footer(text=f"Last updated: {datetime.now(tz).strftime('%H:%M:%S')}")
                
                if self.message:
                    await self.message.edit(embed=embed, view=self)
                    
            except Exception as e:
                logger.error(f"Error updating persistent view: {e}")
    
    @discord.ui.button(
        label="🔒 Close Ticket",
        style=discord.ButtonStyle.danger,
        custom_id="persistent_close",
        emoji="🔒"
    )
    async def close_ticket_button(self, interaction: discord.Interaction, button: Button):
        """Handle ticket closure with modal."""
        # Check if user is authorized (staff or ticket owner)
        ticket = await db.get_ticket_by_id(self.ticket_id)
        if not ticket:
            await interaction.response.send_message("❌ Ticket not found!", ephemeral=True)
            return
        
        is_authorized = (
            interaction.user.id == ticket['user_id'] or
            any(role.name in [MOD_ROLE_NAME, ADMIN_ROLE_NAME] for role in interaction.user.roles)
        )
        
        if not is_authorized:
            await interaction.response.send_message(
                "❌ You don't have permission to close this ticket.", 
                ephemeral=True
            )
            return
        
        # Show close modal with appropriate context
        modal = CloseTicketModal(self.ticket_id, self.channel_id, is_staff=interaction.user.id != ticket['user_id'])
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(
        label="📝 Add Note",
        style=discord.ButtonStyle.secondary,
        custom_id="persistent_note",
        emoji="📝"
    )
    async def add_note_button(self, interaction: discord.Interaction, button: Button):
        """Add internal note (staff only)."""
        if not any(role.name in [MOD_ROLE_NAME, ADMIN_ROLE_NAME] for role in interaction.user.roles):
            await interaction.response.send_message("❌ Only staff can add notes!", ephemeral=True)
            return
        
        modal = NoteModal(self.ticket_id)
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(
        label="📄 Transcript",
        style=discord.ButtonStyle.secondary,
        custom_id="persistent_transcript",
        emoji="📄"
    )
    async def transcript_button(self, interaction: discord.Interaction, button: Button):
        """Generate ticket transcript."""
        await interaction.response.defer(ephemeral=True)
        
        transcript = await create_transcript(self.ticket_id)
        
        if transcript:
            file = discord.File(
                fp=transcript.encode('utf-8'),
                filename=f"ticket_{self.ticket_id}_transcript.txt"
            )
            await interaction.followup.send("📄 Here's your transcript:", file=file, ephemeral=True)
        else:
            await interaction.followup.send("❌ Could not generate transcript.", ephemeral=True)


class CloseTicketModal(Modal, title="Close Support Ticket"):
    """Modal for closing tickets with reason."""
    
    reason = TextInput(
        label="Reason for closing",
        style=discord.TextStyle.paragraph,
        placeholder="Please provide a reason for closing this ticket...",
        required=True,
        max_length=500
    )
    
    def __init__(self, ticket_id: int, channel_id: int, is_staff: bool = False):
        super().__init__()
        self.ticket_id = ticket_id
        self.channel_id = channel_id
        self.is_staff = is_staff
        
        if is_staff:
            self.title = "Close Ticket (Staff)"
        else:
            self.title = "Close Your Ticket"
            self.reason.label = "Why are you closing this ticket?"
            self.reason.placeholder = "Optional: Let us know why you're closing the ticket..."
            self.reason.required = False
    
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        channel = interaction.guild.get_channel(self.channel_id) if interaction.guild else None
        ticket = await db.get_ticket_by_id(self.ticket_id)
        
        if not ticket:
            await interaction.followup.send("❌ Ticket not found!", ephemeral=True)
            return
        
        reason = str(self.reason) if self.reason else "No reason provided"
        
        # Close ticket in database
        await db.close_ticket(self.ticket_id, interaction.user.id, reason)
        
        # Generate transcript
        transcript = await create_transcript(self.ticket_id)
        
        # Notify the user
        try:
            user = await bot.fetch_user(ticket['user_id'])
            
            close_embed = discord.Embed(
                title="🔒 Ticket Closed",
                description=f"Your ticket #{self.ticket_id} has been closed.",
                color=discord.Color.red()
            )
            
            if reason:
                close_embed.add_field(name="Reason", value=reason, inline=False)
            
            close_embed.set_footer(text=f"Closed by {interaction.user.name}")
            
            await user.send(embed=close_embed)
            
            if transcript:
                file = discord.File(
                    fp=transcript.encode('utf-8'),
                    filename=f"ticket_{self.ticket_id}_transcript.txt"
                )
                await user.send("📄 Here's your ticket transcript:", file=file)
                
        except discord.Forbidden:
            logger.warning(f"Could not DM user {ticket['user_id']} about ticket closure")
        
        # Log closure
        if interaction.guild:
            await log_action(
                interaction.guild,
                "Ticket Closed",
                f"Ticket #{self.ticket_id} closed by {interaction.user.mention}",
                discord.Color.red(),
                fields={
                    "Reason": reason,
                    "User": f"<@{ticket['user_id']}>",
                    "Closed By": interaction.user.mention
                }
            )
        
        # Send closure message and delete channel
        if channel:
            await channel.send(
                f"🔒 Ticket closed by {interaction.user.mention}\n"
                f"**Reason:** {reason}\n\n"
                "Channel will be deleted in 10 seconds..."
            )
            await asyncio.sleep(10)
            await channel.delete()


class NoteModal(Modal, title="Add Internal Note"):
    """Modal for adding internal notes."""
    
    note = TextInput(
        label="Note Content",
        style=discord.TextStyle.paragraph,
        placeholder="Enter your internal note here...",
        required=True,
        max_length=1000
    )
    
    def __init__(self, ticket_id: int):
        super().__init__()
        self.ticket_id = ticket_id
    
    async def on_submit(self, interaction: discord.Interaction):
        await db.add_note(self.ticket_id, interaction.user.id, str(self.note))
        
        embed = discord.Embed(
            title="📝 Internal Note Added",
            description=str(self.note),
            color=discord.Color.gold(),
            timestamp=datetime.now(tz)
        )
        
        embed.set_author(name=interaction.user.name, icon_url=interaction.user.display_avatar.url)
        embed.set_footer(text="Only visible to staff")
        
        await interaction.response.send_message(embed=embed)


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
        existing_ticket = await db.get_active_ticket(user.id, guild.id)
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
            ticket_id = await db.create_ticket(
                user_id=user.id,
                guild_id=guild.id,
                channel_id=channel.id,
                category=self.category,
                created_by=user.id
            )
            
            # Save initial message
            await db.add_message(ticket_id, user.id, str(self.reason), is_staff=False)
            
            # Create initial embed
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
            
            # Create persistent control panel
            persistent_view = PersistentTicketView(ticket_id, channel.id)
            control_message = await channel.send(
                f"{mod_role.mention} New ticket from {user.mention}",
                embed=embed,
                view=persistent_view
            )
            persistent_view.message = control_message
            
            # Send guide message
            guide_embed = discord.Embed(
                title="📬 Ticket Management",
                description="**For Moderators:**\n"
                           "• Messages sent here will be relayed to the user\n"
                           "• Use the buttons above to manage the ticket\n"
                           "• The control panel updates automatically\n\n"
                           "**For Users:**\n"
                           f"• {user.mention} can also close this ticket using the button\n"
                           "• All messages will be sent to you as DMs\n"
                           "• You can close the ticket if it was created by mistake",
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
                               "will be sent to the support team.\n\n"
                               "**To close this ticket**, use the button in the ticket channel.",
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
        
        async with await db.get_connection() as conn:
            await conn.execute('UPDATE tickets SET priority = ? WHERE id = ?', 
                              (priority, self.ticket_id))
            await conn.commit()
        
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
        ticket = await db.get_ticket_by_id(ticket_id)
        
        if not ticket:
            return None
        
        messages = await db.get_ticket_history(ticket_id)
        notes = await db.get_ticket_notes(ticket_id)
        
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
    """
    guild = ctx.guild
    
    # Check if user already has ticket
    existing_ticket = await db.get_active_ticket(user.id, guild.id)
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
        ticket_id = await db.create_ticket(
            user_id=user.id,
            guild_id=guild.id,
            channel_id=channel.id,
            category="staff-created",
            created_by=ctx.author.id
        )
        
        # Save initial message
        await db.add_message(ticket_id, ctx.author.id, reason, is_staff=True)
        
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
        
        # Create persistent control panel
        persistent_view = PersistentTicketView(ticket_id, channel.id)
        control_message = await channel.send(
            f"{mod_role.mention} Ticket created for {user.mention} by {ctx.author.mention}",
            embed=embed,
            view=persistent_view
        )
        persistent_view.message = control_message
        
        # Send the initial message
        await channel.send(f"**{ctx.author.name} (Staff):** {reason}")
        
        # DM user
        try:
            dm_embed = discord.Embed(
                title="📨 Support Ticket Created",
                description=f"A moderator has created a support ticket for you in **{guild.name}**.\n\n"
                           f"**Ticket ID:** #{ticket_id}\n\n"
                           f"**Initial Message:**\n{reason}\n\n"
                           "Please respond here to communicate with the staff team.\n\n"
                           "**To close this ticket**, use the button in the ticket channel.",
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


@bot.command(name="warn")
@commands.has_role(MOD_ROLE_NAME)
async def warn_user_command(ctx, *, reason: str):
    """
    Issue a warning to the ticket user.
    Usage: !warn <reason>
    """
    ticket = await db.get_ticket_by_channel(ctx.channel.id)
    
    if not ticket:
        await ctx.send("❌ This command can only be used in ticket channels.")
        return
    
    # Update warnings in database
    await db.update_user_warnings(ticket['user_id'], 1)
    
    # Get updated warning count
    user_data = await db.get_user_data(ticket['user_id'])
    warning_count = user_data['warnings'] if user_data else 1
    
    # Add note
    await db.add_note(
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
    stats = await db.get_ticket_stats(ctx.guild.id)
    
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
    user_data = await db.get_user_data(user.id)
    
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
            value="Create a ticket for a specific user",
            inline=False
        )
        
        embed.add_field(
            name="!warn <reason>",
            value="Issue a warning to the ticket user",
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
        
        embed.add_field(
            name="Ticket Controls",
            value="• Use the persistent control panel in each ticket channel\n"
                 "• Users can now close their own tickets\n"
                 "• Control panel updates automatically with new messages",
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
            name="Closing a Ticket",
            value="You can close your own ticket by clicking the 🔒 button in the ticket channel.\n"
                 "This is useful if you created a ticket by mistake or no longer need assistance.",
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
    # Initialize database
    await db.init_database()
    
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
            ticket = await db.get_active_ticket(user.id, guild.id)
            
            if ticket:
                channel = bot.get_channel(ticket['channel_id'])
                
                if channel:
                    # Save message to database
                    attachment_urls = [att.url for att in message.attachments] if message.attachments else None
                    await db.add_message(
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
                    
                    # Update persistent view
                    for view in bot.persistent_views:
                        if isinstance(view, PersistentTicketView) and view.ticket_id == ticket['id']:
                            await view.update_display(channel)
                    
                break
        else:
            # No active ticket found
            await user.send(
                "❌ You don't have an open support ticket.\n\n"
                "Please go to the server and click the 📩 button to create a ticket."
            )
        
        return
    
    # Handle messages in ticket channels from staff
    ticket = await db.get_ticket_by_channel(message.channel.id)
    
    if ticket:
        # Check if user is staff
        is_staff = any(role.name in [MOD_ROLE_NAME, ADMIN_ROLE_NAME] for role in message.author.roles)
        
        if is_staff:
            # Don't relay commands
            if message.content.startswith('!'):
                await bot.process_commands(message)
                return
            
            # Save message to database
            attachment_urls = [att.url for att in message.attachments] if message.attachments else None
            await db.add_message(
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
            
            # Update persistent view
            for view in bot.persistent_views:
                if isinstance(view, PersistentTicketView) and view.ticket_id == ticket['id']:
                    await view.update_display(message.channel)
    
    await bot.process_commands(message)


@bot.event
async def on_member_ban(guild: discord.Guild, user: discord.User):
    """Handle user bans."""
    # Mark user as banned in database
    await db.ban_user(user.id, "Banned from server")
    
    # Close any active tickets
    ticket = await db.get_active_ticket(user.id, guild.id)
    if ticket:
        await db.close_ticket(ticket['id'], bot.user.id, "User was banned")
        
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


@bot.event
async def on_guild_channel_delete(channel):
    """Handle channel deletion."""
    # If a ticket channel is deleted, mark ticket as closed
    ticket = await db.get_ticket_by_channel(channel.id)
    if ticket and ticket['status'] == 'open':
        await db.close_ticket(ticket['id'], bot.user.id, "Channel deleted")

# ============================================================================
# BACKGROUND TASKS
# ============================================================================

@tasks.loop(hours=1)
async def check_inactive_tickets():
    """Check for inactive tickets and notify/close them."""
    logger.info("Checking for inactive tickets...")
    
    inactive_tickets = await db.get_inactive_tickets(INACTIVE_TIMEOUT)
    
    for ticket in inactive_tickets:
        try:
            guild = bot.get_guild(ticket['guild_id'])
            if not guild:
                continue
            
            channel = guild.get_channel(ticket['channel_id'])
            if not channel:
                # Channel was deleted manually
                await db.close_ticket(ticket['id'], bot.user.id, "Channel deleted")
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
    
    # Add persistent views
    bot.add_view(CreateTicketButton())
    
    try:
        bot.run(DISCORD_TOKEN)
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}")
        exit(1)
