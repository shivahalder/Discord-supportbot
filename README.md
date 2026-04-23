# Discord Verification Bot

A Discord bot combined with an Express HTTP server that handles member verification, IP logging, and ban management backed by a MySQL database.

## How it works

When a new member joins the server, the bot sends them a DM with a unique verification link. Clicking the link hits the Express server, records their Discord ID, username, and IP address in the database, and assigns them the verified role. Admins can then ban or unban users by Discord ID or by the IP address captured during verification.

## Features

- Sends a verification link to every new member via DM
- Assigns a role on successful verification
- Blocks banned IDs and IPs from verifying
- Admin commands to ban/unban by Discord ID or IP
- Automatically creates `IPBAN` and `IDBAN` roles if they do not exist
- Strips all roles from a banned member and assigns the appropriate ban role
- Restores the verified role when a ban is lifted

## Prerequisites

- Node.js 20 or later (or Docker)
- A MySQL-compatible database
- A Discord bot token with the `Server Members Intent` and `Message Content Intent` enabled in the Discord Developer Portal

## Environment variables

Copy `.env.example` to `.env` and fill in the values. Never commit your `.env` file.

| Variable           | Description                          |
|--------------------|--------------------------------------|
| `DISCORD_BOT_TOKEN`| Your Discord bot token               |
| `DB_HOST`          | MySQL host                           |
| `DB_PORT`          | MySQL port (default: 3306)           |
| `DB_USER`          | MySQL username                       |
| `DB_PASSWORD`      | MySQL password                       |
| `DB_NAME`          | MySQL database name                  |

## Database setup

Run the following SQL to create the required tables:

```sql
CREATE TABLE verified (
    discordId VARCHAR(32) PRIMARY KEY,
    username  VARCHAR(100),
    ip        VARCHAR(64)
);

CREATE TABLE banned_ids (
    discordId VARCHAR(32) PRIMARY KEY,
    username  VARCHAR(100)
);

CREATE TABLE banned_ips (
    ip       VARCHAR(64) PRIMARY KEY,
    username VARCHAR(100)
);
```

## Running locally

```bash
npm install
node bot-and-server.js
```

## Running with Docker

```bash
docker compose up --build
```

The Express server listens on port `3000`. Make sure that port is reachable from wherever your verification link points.

## Bot commands

All commands require the `Ban Members` permission.

| Command           | Description                                      |
|-------------------|--------------------------------------------------|
| `!banid @user`    | Bans a user by Discord ID and assigns IDBAN role |
| `!unbanid @user`  | Removes the ID ban and restores the verified role|
| `!banip @user`    | Bans the IP associated with a verified user      |
| `!unbanip @user`  | Removes the IP ban and restores the verified role|

You can mention a user or provide their raw Discord ID for any command.

## Project structure

```
bot-and-server.js   main entry point (bot + HTTP server)
Dockerfile
docker-compose.yml
package.json
.env                not committed — contains secrets
```
