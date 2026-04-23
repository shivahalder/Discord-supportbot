# Discord ModMail Bot

A Discord bot that creates a private communication system between server members and moderators. Users send a direct message to the bot, and it automatically generates a dedicated ticket channel where staff can respond — keeping all conversations organized and confidential.

---

## Features

- **Automatic Ticket Creation** — When a user sends a DM to the bot, a private modmail channel is instantly created in the server.
- **Two-Way Messaging** — Moderators can reply directly inside the ticket channel, and the message is forwarded to the user's DMs.
- **Spam Protection** — Users who send more than 5 messages within 10 seconds are automatically blocked from the modmail system.
- **Manual Ticket Opening** — Staff members can proactively open a modmail thread for any user by their ID.
- **Ticket Closure Notifications** — When a ticket is deleted or closed, the user receives an automatic notification via DM.
- **Docker Support** — The bot can be containerized and deployed using Docker for consistent, reliable hosting.

---

## Commands

| Command | Permission Required | Description |
|---|---|---|
| `!setup` | Administrator | Creates the MODMAIL category and SUPPORTER role in your server. |
| `!open <userID>` | SUPPORTER role | Manually opens a modmail thread to contact a specific user. |
| `!close [reason]` | SUPPORTER role | Closes the current modmail thread and notifies the user. |
| `!help` | Anyone | Displays a list of all available commands. |

> The default prefix is `!`. You can change it by setting the `PREFIX` variable in your `.env` file.

---

## Prerequisites

Before running this bot, make sure you have the following installed:

- [Node.js](https://nodejs.org/) v16 or higher
- npm (comes bundled with Node.js)
- A Discord bot token from the [Discord Developer Portal](https://discord.com/developers/applications)

---

## Installation

**1. Clone the repository**
```bash
git clone https://github.com/YOUR_USERNAME/discord-modmail-bot.git
cd discord-modmail-bot
```

**2. Install dependencies**
```bash
npm install
```

**3. Configure environment variables**

Create a `.env` file in the root directory and add the following:
```env
DISCORD_BOT_TOKEN=your_bot_token_here
PREFIX=!
SERVER_ID=your_server_id_here
```

**4. Start the bot**
```bash
node index.js
```

---

## Docker Deployment

If you prefer to run the bot inside a Docker container:

```bash
docker build -t modmail-bot .
docker run -d --env-file .env modmail-bot
```

---

## How It Works

1. A user sends a direct message to the bot.
2. The bot locates the MODMAIL category in your server (created by `!setup`).
3. A new text channel is created under that category, named after the user.
4. Supporters can see the channel and respond — their replies are forwarded to the user's DMs.
5. When the issue is resolved, a supporter runs `!close` to delete the channel and notify the user.

---

## Security Notice

Never share or commit your `.env` file or bot token to a public repository. The `.gitignore` file in this project already excludes `.env` to help protect your credentials.

---

## Tech Stack

- **Runtime:** Node.js
- **Library:** [discord.js](https://discord.js.org/) v14
- **Configuration:** dotenv
- **Deployment:** Docker

---

## License

This project is licensed under the ISC License.
