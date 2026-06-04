# Pet Drama Autopilot

Fully automated YouTube channel pipeline for **Pet Animals Facts (Cats, Dogs, etc.)**. Generates ideas, creates videos via MoneyPrinterTurbo, auto-uploads with thumbnails — runs daily on a schedule.

---

## What This Does

1. Generates video ideas using Gemini AI
2. Creates videos via MoneyPrinterTurbo (running locally)
3. Generates custom pet-themed thumbnails
4. Uploads to YouTube with title, description, tags, thumbnail
5. Tracks uploaded videos to avoid duplicates
6. Retries failed videos automatically
7. Runs on a daily schedule (default: 9:00 AM)

---

## Prerequisites

- Python 3.10+
- [MoneyPrinterTurbo](https://github.com/harry0703/MoneyPrinterTurbo) installed and running at `localhost:8080`
- `ffmpeg` installed and in PATH
- Internet connection for API calls

---

## Setup Steps

### 1. Install MoneyPrinterTurbo
```bash
git clone https://github.com/harry0703/MoneyPrinterTurbo
cd MoneyPrinterTurbo
pip install -r requirements.txt
python main.py   # Start it — must be running before pipeline runs
```

### 2. Get API Keys (both free)
- **Gemini**: https://aistudio.google.com → Get API key (no credit card)
- **Pexels**: https://www.pexels.com/api → Generate free API key

### 3. Setup YouTube API
1. Go to https://console.cloud.google.com
2. Create a new project
3. Enable **YouTube Data API v3**
4. Go to Credentials → Create Credentials → OAuth 2.0 Client ID
5. Application type: **Desktop App**
6. Download `client_secrets.json`
7. Place it in `credentials/client_secrets.json`

### 4. Configure .env
Edit `.env` and fill in your keys:
```
GEMINI_API_KEY=your_actual_key_here
PEXELS_API_KEY=your_actual_key_here
```

### 5. Install Dependencies
```bash
cd pet_autopilot
pip install -r requirements.txt
```

### 6. First-Time YouTube Auth (one-time only)
```bash
python main.py --setup
# Opens browser → log in → grant permissions → token saved
```

---

## Running

```bash
# Test everything without uploading to YouTube
python main.py --dry-run

# Run pipeline once (full upload)
python main.py --now

# Use a specific video title
python main.py --idea "What Is Dark Matter"

# Check stats
python main.py --status

# Retry failed videos
python main.py --retry-failed

# Start daily scheduler (runs forever)
python scheduler.py
```

---

## Common Errors

**1. `MoneyPrinterTurbo is NOT running`**
Start it first: `cd MoneyPrinterTurbo && python main.py`

**2. `client_secrets.json not found`**
Download from Google Cloud Console → place in `credentials/` folder

**3. `YouTube quota exceeded`**
YouTube free tier = 10,000 units/day. One upload ≈ 1,600 units. Wait until midnight Pacific time for quota reset.

**4. `Gemini API key invalid`**
Check `GEMINI_API_KEY` in `.env`. Get free key at https://aistudio.google.com

**5. `Video creation timed out`**
MoneyPrinterTurbo is slow on first run (downloads models). Wait 15+ min or check its logs.

---

## File Structure

```
pet_autopilot/
├── main.py           — pipeline + CLI
├── scheduler.py      — daily scheduler
├── config.py         — all config from .env
├── modules/
│   ├── idea_generator.py   — Gemini AI ideas
│   ├── video_creator.py    — MoneyPrinterTurbo integration
│   ├── thumbnail.py        — Pillow thumbnail generator
│   ├── uploader.py         — YouTube Data API
│   └── tracker.py          — JSON-based duplicate/retry tracking
├── data/             — JSON state files
├── thumbnails/       — generated thumbnails
├── outputs/          — video files
├── logs/             — rotating log files
└── credentials/      — OAuth tokens (git-ignored)
```
