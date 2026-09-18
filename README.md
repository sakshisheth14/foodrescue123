# FoodRescue — FoodRescueMap-inspired Dark Theme

FoodRescue is a Flask + SQLite localhost project connecting restaurants / food businesses with NGOs / organizations so safe surplus can be listed, discovered, reserved and collected before the pickup deadline.

## Run

### Option 1 — Windows
Double-click `run-foodrescue.bat`.

### Option 2 — Terminal
```bat
py -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
py app.py
```

Open: **http://localhost:5055**

## Account types
- Restaurant / Food Business — can only see and manage its own listings.
- NGO / Organization — can discover live listings and reserve them.
- Individual accounts are intentionally not included.

## Theme
The interface uses a dark, bold, animated product-marketing direction inspired by the visual language of FoodRescueMap while keeping FoodRescue's own vision, workflows and functionality.

## Important
The app creates `foodrescue.db` beside `app.py` on first run. Delete that file only if you want to reset local data.
