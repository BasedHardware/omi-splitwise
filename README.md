# Splitwise Integration for Omi

Split expenses with friends using voice commands through your Omi device. Create expenses, split costs, and manage shared finances - all hands-free!

---

## Features

- **Split Expenses** - Create and split expenses with friends using natural voice
- **Smart Friend Matching** - Fuzzy matches friend names so you don't need to be exact
- **Flexible Dates** - Use "today", "yesterday", or specific dates
- **Group Support** - Add expenses to Splitwise groups by name
- **Secure OAuth** - Industry-standard OAuth 2.0 authentication

---

## Quick Start

1. Install the Splitwise app from the Omi App Store
2. Click "Connect with Splitwise" to authenticate
3. Start using voice commands!

---

## Voice Commands

| Command | Description |
| ------- | ----------- |
| "Split $25 for lunch with John" | Split equally with one person |
| "Split 50 dollars for dinner with Alice and Bob" | Split among multiple people |
| "Add expense of $100 for groceries yesterday" | Specify a date |
| "Split $30 for food in group Roommates with Sarah" | Add to a specific group |

---

## Omi App Store Details

### App Information

| Field | Value |
| ----- | ----- |
| **App Name** | Splitwise |
| **Category** | Finance & Utilities |
| **Description** | Split expenses with friends using voice commands. Create expenses, split costs with fuzzy friend matching, and manage shared finances - all hands-free through Omi. |
| **Author** | Omi Community |
| **Version** | 1.0.0 |

### Capabilities

- External Integration (required for chat tools)
- Chat (for voice command responses)

### URLs for Omi App Configuration

| URL Type | URL |
| -------- | --- |
| **App Home URL** | `https://your-app.up.railway.app/` |
| **Setup Completed URL** | `https://your-app.up.railway.app/setup/splitwise` |
| **Chat Tools Manifest URL** | `https://your-app.up.railway.app/.well-known/omi-tools.json` |

> **Note:** Omi automatically appends `?uid=USER_ID` to these URLs.

---

## Splitwise Developer Setup

### Redirect URI (Add to Splitwise App Settings)

Add this redirect URI to your Splitwise app at [https://secure.splitwise.com/apps](https://secure.splitwise.com/apps):

```
https://your-app.up.railway.app/auth/splitwise/callback
```

For local development:
```
http://localhost:8080/auth/splitwise/callback
```

---

## Chat Tools

This app exposes a manifest endpoint at `/.well-known/omi-tools.json` that Omi automatically fetches when the app is created or updated.

### Available Tools

| Tool | Description |
| ---- | ----------- |
| `create_expense` | Create a Splitwise expense split among friends |

---

## Development

### Prerequisites

- Python 3.8+
- Splitwise Developer Account

### Local Setup

```bash
# Navigate to the plugin directory
cd plugins/splitwise

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy environment file and configure
cp .env.example .env
# Edit .env with your Splitwise credentials

# Run the server
python -m uvicorn main:app --reload --port 8080
```

### Environment Variables

```env
SPLITWISE_CONSUMER_KEY=your_consumer_key
SPLITWISE_CONSUMER_SECRET=your_consumer_secret
SPLITWISE_REDIRECT_URI=http://localhost:8080/auth/splitwise/callback
PORT=8080
REDIS_URL=  # Optional: for production use
```

---

## Deploy to Railway

### Step 1: Create Railway Project

1. Go to [Railway](https://railway.app) and sign in
2. Click **"New Project"** → **"Deploy from GitHub repo"**
3. Select your repository and choose the `plugins/splitwise` folder

### Step 2: Add Redis Database (Optional)

1. In your Railway project, click **"+ New"** → **"Database"** → **"Add Redis"**
2. Railway automatically creates and connects the Redis instance
3. The `REDIS_URL` environment variable is set automatically

### Step 3: Configure Environment Variables

Go to your service's **Variables** tab and add:

| Variable | Value |
| -------- | ----- |
| `SPLITWISE_CONSUMER_KEY` | Your consumer key from Splitwise |
| `SPLITWISE_CONSUMER_SECRET` | Your consumer secret from Splitwise |
| `SPLITWISE_REDIRECT_URI` | `https://YOUR-APP.up.railway.app/auth/splitwise/callback` |

### Step 4: Update Splitwise App Settings

Add your Railway URL as a redirect URI at [https://secure.splitwise.com/apps](https://secure.splitwise.com/apps):

```
https://YOUR-APP.up.railway.app/auth/splitwise/callback
```

### Step 5: Update Omi App Store

Update your app URLs in the Omi App Store:

| URL Type | Value |
| -------- | ----- |
| **App Home URL** | `https://YOUR-APP.up.railway.app/` |
| **Setup Completed URL** | `https://YOUR-APP.up.railway.app/setup/splitwise` |
| **Chat Tools Manifest URL** | `https://YOUR-APP.up.railway.app/.well-known/omi-tools.json` |

---

## API Endpoints

| Endpoint | Method | Description |
| -------- | ------ | ----------- |
| `/` | GET | Home page / App settings |
| `/health` | GET | Health check |
| `/auth/splitwise` | GET | Start OAuth flow |
| `/auth/splitwise/callback` | GET | OAuth callback |
| `/setup/splitwise` | GET | Check setup status |
| `/disconnect` | GET | Disconnect account |
| `/.well-known/omi-tools.json` | GET | Chat tools manifest |
| `/tools/create_expense` | POST | Chat tool: Create expense |

---

## Troubleshooting

### "User not authenticated"

- Complete the Splitwise OAuth flow by clicking "Connect with Splitwise" in app settings

### "Could not find friend"

- Check spelling of friend's name
- The app uses fuzzy matching, but very different names won't match
- Make sure the person is in your Splitwise friends list

### "Invalid callback parameters"

- Make sure the redirect URI in Splitwise app settings matches exactly

---

## License

MIT License - feel free to modify and distribute.

---

## Support

For issues or feature requests, please open an issue on GitHub or contact the Omi community.

---

Made with love for Omi
