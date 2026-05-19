# Drivebot

A small personal Telegram bot that receives Telegram media, downloads it to local disk, uploads it to Google Drive, makes the uploaded file public to anyone with the link, and deletes the temporary local file afterward.

It uses Pyrofork polling for Telegram, SQLite for persistence, FastAPI for the Google OAuth callback, and the minimal Drive scope:

```text
https://www.googleapis.com/auth/drive.file
```

## Features

- `/start` - show available commands
- `/connect` - start Google OAuth
- `/disconnect` - remove stored Google OAuth tokens
- `/folder <folder_id|folder_name|root>` - set the target Google Drive folder
- `/files` - list recent files in the configured folder
- `/status` - show Google connection, upload path, and storage status
- `/delete <file_id>` - delete a Drive file, also available through inline buttons from `/files`
- Uploads documents, videos, audio, voice messages, video notes, animations, photos, stickers, and forwarded media
- Asks for a custom Google Drive filename before uploading; `/skip` uses the default Telegram filename
- Appends the Telegram message ID to uploaded filenames to avoid duplicate Drive names
- Makes uploaded files public and returns direct Google Drive download links

## Requirements

- Python 3.12+
- A Telegram bot token from BotFather
- Telegram API ID and API hash from <https://my.telegram.org>
- A Google Cloud OAuth client
- A public HTTPS URL for the OAuth callback if running outside localhost

## Setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` with your Telegram and Google values.

Create the local folders if they do not already exist:

```bash
mkdir -p downloads tokens credentials
```

Put the Google OAuth JSON file at:

```text
credentials/client_secret.json
```

Run the bot:

```bash
python -m bot.main
```

The bot uses Telegram polling. Do not configure a Telegram webhook.

## Google Cloud OAuth Setup

1. Open Google Cloud Console.
2. Create or select a project.
3. Enable the Google Drive API.
4. Configure the OAuth consent screen.
5. Create an OAuth client ID.
6. Choose `Web application`.
7. Add this authorized redirect URI, matching `.env` exactly:

```text
https://api.your-domain.example/google/callback
```

8. Download the client JSON and save it as `credentials/client_secret.json`.
9. If your app is in testing mode, add your Google account as a test user.

The bot requests only `drive.file`, so it can manage files it creates or files explicitly opened/selected by the app. For this bot’s normal upload flow, that is enough.

Uploaded files are shared as `anyone with the link can read`, so treat the returned links as public.

## Folders

By default, uploads go to Drive root. To upload into a folder:

Send either a Drive folder ID or a folder name:

```text
/folder your_google_drive_folder_id
```

```text
/folder tg
```

If the folder name is not visible to the app, the bot creates that folder and stores its real Drive folder ID.

Use `/folder root` to return to Drive root.

## VPS Deployment

Example systemd unit at `/etc/systemd/system/drivebot.service`:

```ini
[Unit]
Description=Drivebot Telegram bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=drivebot
WorkingDirectory=/opt/drivebot
EnvironmentFile=/opt/drivebot/.env
ExecStart=/opt/drivebot/.venv/bin/python -m bot.main
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now drivebot
sudo journalctl -u drivebot -f
```

Example nginx reverse proxy:

```nginx
server {
    listen 443 ssl http2;
    server_name your-domain.example;

    ssl_certificate /etc/letsencrypt/live/your-domain.example/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.example/privkey.pem;

    location /google/callback {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }

    location /health {
        proxy_pass http://127.0.0.1:8000;
    }
}
```

Your `.env` should contain:

```text
OAUTH_REDIRECT_URI=https://api.your-domain.example/google/callback
OAUTH_HOST=127.0.0.1
OAUTH_PORT=8000
```

## Security Notes

- Keep `.env`, `tokens/`, and `credentials/` private.
- Set `ALLOWED_TELEGRAM_IDS` for personal use.
- OAuth state values are single-use and expire after 15 minutes.
- Secrets and token-like log messages are redacted by the logging filter.
- SQLite stores tokens locally in `tokens/drivebot.sqlite3`; protect this file with filesystem permissions.

## Large Files

Telegram downloads are written to `downloads/`. Google Drive uploads use resumable chunked file uploads from disk through `MediaFileUpload`, so the bot does not load large files fully into RAM.
