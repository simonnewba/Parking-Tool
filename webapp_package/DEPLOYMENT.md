# Parking Garage Traffic Engineering Tool — Deployment Guide

## What this is

A Monte Carlo traffic simulation tool with a web front end. Every "Run
Simulation" click runs 60 independent randomized simulations of the same
afternoon and reports the median result. Typical response time: 5-10 seconds.

Fully anonymized — no client names anywhere in the code or interface.

## Files in this package

- `app.py` — Flask backend, serves the frontend and the `/api/simulate` endpoint
- `engine.py`, `network_config.py`, `network_graph.py` — the simulation engine
- `static/index.html` — the frontend (single page, CW-branded)
- `static/cw_logo.png` — CW logo asset
- `requirements.txt` — Python dependencies
- `Procfile` — tells Render how to start the app

## Deploying to Render (free tier)

1. **Create a GitHub repository** and push all the files in this package to it
   (keep the folder structure exactly as-is — `static/` must stay a subfolder).

2. **Sign up at [render.com](https://render.com)** (free tier is fine).

3. **New → Web Service**, connect your GitHub account, and select the
   repository you just created.

4. Render should auto-detect this as a Python app. Confirm these settings:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn app:app`
   - **Instance Type:** Free

5. Click **Create Web Service**. First deploy takes a few minutes.

6. Once live, Render gives you a URL like
   `https://your-app-name.onrender.com` — that's what you share with the client.

## Important free-tier behavior to know about

Render's free tier **sleeps after a period of inactivity**. The first request
after it's been idle will take 20-30 seconds extra to "wake up" the server —
after that, it responds normally. Worth mentioning to the client
("first load may take a moment") rather than something to fix, since it's a
free-tier tradeoff.

## Testing locally before deploying (optional)

```
pip install -r requirements.txt
python3 app.py
```

Then open `http://127.0.0.1:5000` in a browser.

## What's adjustable in the tool

1. Employees at Lot B (200 or 0) and Lot C (900 or 1200) — Lot A is fixed at 740.
2. Four access-point toggles — switch any combination off to simulate a closed/blocked road.
3. Signal timing at all 3 signal-controlled points (green/red, 15-120 sec in 15-sec steps).

Each run is a live computation, not a lookup table — the combination space
(especially the signal timing) is far too large to precompute exhaustively.
