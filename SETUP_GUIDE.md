# Malioboro Scraper — Complete Setup Guide
## 1-Week Collection: Thursday 1 May → Wednesday 7 May

---

## Step 1 — Install Dependencies

```bash
pip install opencv-python httpx m3u8 Pillow numpy
```

Verify everything installed:
```bash
python -c "import cv2, httpx, m3u8, numpy; print('All good')"
```

---

## Step 2 — Run the Test Script First

Always test before starting the full collection.

```bash
# Quick test — just checks if URLs are reachable (fast, ~1 min)
python scraper_test.py --fast

# Full test — actually grabs a frame from each camera (~5 min)
python scraper_test.py

# Test specific cameras only
python scraper_test.py --cameras 1 2 3 4 5
```

**What to expect:**
- ✓ green = camera working, frame grabbed
- ⚠ yellow = URL reachable but frame grab failed (may still work, monitor it)
- ✗ red = camera unreachable (will be logged as "failed" in metadata, not a problem)

If 70%+ cameras are green, you're good to proceed.

---

## Step 3 — Test the Scraper Manually (One Run)

Before leaving it to run automatically, do one manual test run:

```bash
python scraper_cctv.py --now
```

Check that:
- Frames appear in `dataset/raw_frames/`
- `dataset/metadata.csv` has rows in it
- `dataset/logs/` has a log file

---

## Step 4 — Prevent Your Laptop from Sleeping

This is the most important step. The scraper will silently miss slots if your laptop sleeps.

### Windows
1. Open **Settings → System → Power & Sleep**
2. Set "When plugged in, put my computer to sleep after" → **Never**
3. Also go to **Settings → System → Display** → set screen off to **Never** (optional but helpful)

Or via PowerShell (run as administrator):
```powershell
powercfg /change standby-timeout-ac 0
powercfg /change monitor-timeout-ac 0
```

### Mac
1. **System Settings → Battery → Options**
2. Enable "Prevent automatic sleeping when display is off"
3. Or: `sudo pmset -a sleep 0 disksleep 0`

### Linux
```bash
gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type 'nothing'
# or
systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
```

---

## Step 5 — Start the Scraper (Leave It Running)

```bash
cd /path/to/your/project
python scraper_cctv.py
```

That's it. The scraper will:
1. Print the schedule for today
2. Sleep until the next slot
3. Wake up, scrape all 22 cameras, go back to sleep
4. Repeat until Wednesday

**Keep this terminal window open.** Don't close it.

If you're on a laptop and want it to keep running with the lid closed, use:

```bash
# Linux/Mac — run in background, survives terminal close
nohup python scraper_cctv.py > dataset/logs/nohup.log 2>&1 &
echo "Scraper PID: $!"

# To check if it's still running:
ps aux | grep scraper_cctv

# To stop it:
kill <PID>
```

On Windows, just leave the Command Prompt / PowerShell window open.

---

## 1-Week Schedule

| Slot | Time | Why |
|------|------|-----|
| early_morning | 06:00 | Baseline — very low crowd, good for empty-scene reference |
| morning | 08:00 | Commute peak — motorcycles dense |
| midmorning | 10:00 | Tourist arrival — becak/andong start appearing |
| noon | 12:00 | Lunch rush — high density people + vehicles |
| afternoon | 14:00 | Hottest hour — still busy, different lighting |
| late_afternoon | 16:00 | Pre-evening rush — bajaj peak |
| evening | 18:00 | **Best slot** — golden light, all custom vehicles active |
| night | 20:00 | Night market — different lighting, crowd profile |

**Total: 8 slots × 22 cameras × 7 days = 1,232 frame attempts**
**Expected usable (after blur/dark filter): ~900–1,000 frames**

---

## Dataset Math: 1 Week vs 2 Weeks

| | 2 weeks (original) | 1 week (your plan) |
|---|---|---|
| Slots/day | 4 | 8 |
| Total runs | 4×14 = 56 | 8×7 = 56 |
| Frame attempts | 22×56 = 1,232 | 22×56 = 1,232 |

**Exactly the same total.** You just compress the same 56 runs into 7 days instead of 14, by running twice as many slots per day. The dataset size and diversity are identical.

---

## If You Miss a Slot

If your laptop slept or you had to restart, just run the missed slot manually:

```bash
# Missed the 12:00 noon slot? Run it now:
python scraper_cctv.py --now --slot noon

# Missed morning and noon? Run both:
python scraper_cctv.py --now --slot morning
python scraper_cctv.py --now --slot noon
```

The scheduler will resume automatically for future slots.

---

## Monitoring Progress

```bash
# Count frames collected so far
ls dataset/raw_frames/*.jpg | wc -l

# Check today's log
cat dataset/logs/scraper_$(date +%Y%m%d).log

# See metadata summary (requires python)
python -c "
import csv
rows = list(csv.DictReader(open('dataset/metadata.csv')))
ok = [r for r in rows if r['status']=='ok']
fail = [r for r in rows if r['status']=='failed']
print(f'Total attempts: {len(rows)}')
print(f'Successful: {len(ok)}')
print(f'Failed: {len(fail)}')
print(f'Blurry: {sum(1 for r in ok if r[\"blurry\"]==\"True\")}')
print(f'Dark: {sum(1 for r in ok if r[\"dark\"]==\"True\")}')
by_slot = {}
for r in ok:
    by_slot[r[\"slot\"]] = by_slot.get(r[\"slot\"], 0) + 1
print('\\nBy slot:')
for slot, count in sorted(by_slot.items()):
    print(f'  {slot}: {count}')
"
```

---

## After Collection: Quick Cleanup

```bash
# Move blurry/dark frames to a rejected folder (don't delete — you might want them later)
python -c "
import csv, shutil
from pathlib import Path

rejected = Path('dataset/rejected')
rejected.mkdir(exist_ok=True)

for row in csv.DictReader(open('dataset/metadata.csv')):
    if row['blurry'] == 'True' or row['dark'] == 'True':
        src = Path('dataset/raw_frames') / row['filename']
        if src.exists():
            shutil.move(str(src), str(rejected / row['filename']))
            print(f'Moved: {row[\"filename\"]}')
"
```
