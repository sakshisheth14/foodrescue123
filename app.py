from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3, uuid, os

BASE_DIR = Path(__file__).resolve().parent

if os.environ.get("VERCEL"):
    DB_PATH = Path("/tmp/foodrescue.db")
else:
    DB_PATH = BASE_DIR / "foodrescue.db"

app = Flask(__name__)
app.secret_key = "foodrescue-localhost-2026"
PORT = 5055

# ─── database ─────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT NOT NULL UNIQUE COLLATE NOCASE,
        phone TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('business','ngo')),
        organization_name TEXT NOT NULL,
        address TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS listings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        business_id INTEGER NOT NULL,
        food_name TEXT NOT NULL,
        category TEXT NOT NULL,
        quantity INTEGER NOT NULL CHECK(quantity > 0),
        price REAL NOT NULL DEFAULT 0,
        is_free INTEGER NOT NULL DEFAULT 0,
        pickup_time TEXT NOT NULL,
        address TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        food_type TEXT NOT NULL DEFAULT 'Cooked',
        created_at TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1,
        paused INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY(business_id) REFERENCES users(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS reservations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        listing_id INTEGER NOT NULL,
        ngo_id INTEGER NOT NULL,
        quantity INTEGER NOT NULL CHECK(quantity > 0),
        code TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL DEFAULT 'Reserved',
        created_at TEXT NOT NULL,
        FOREIGN KEY(listing_id) REFERENCES listings(id) ON DELETE CASCADE,
        FOREIGN KEY(ngo_id) REFERENCES users(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS pickup_assignments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        reservation_id INTEGER NOT NULL UNIQUE,
        pickup_name TEXT NOT NULL DEFAULT 'Pickup Volunteer',
        pickup_phone TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'Waiting',
        eta_minutes INTEGER NOT NULL DEFAULT 0,
        assigned_at TEXT,
        arrived_at TEXT,
        completed_at TEXT,
        FOREIGN KEY(reservation_id) REFERENCES reservations(id) ON DELETE CASCADE
    );
    """)
    conn.commit()

    # add columns that may be missing from an older schema
    for col, defn in [("food_type", "TEXT NOT NULL DEFAULT 'Cooked'"),
                      ("paused",    "INTEGER NOT NULL DEFAULT 0")]:
        try:
            conn.execute(f"ALTER TABLE listings ADD COLUMN {col} {defn}")
            conn.commit()
        except Exception:
            pass
    conn.close()

init_db()

# ─── helpers ──────────────────────────────────────────────────────────────────

def now_iso():
    return datetime.now().replace(microsecond=0).isoformat(timespec="minutes")

def future_iso(hours=0, minutes=0):
    dt = datetime.now() + timedelta(hours=hours, minutes=minutes)
    return dt.replace(second=0, microsecond=0).isoformat(timespec="minutes")

def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    conn.close()
    return user

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped

def role_required(role):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = current_user()
            if not user:
                return redirect(url_for("login"))
            if user["role"] != role:
                return redirect(url_for("home"))
            return view(*args, **kwargs)
        return wrapped
    return decorator

def deadline_info(value):
    try:
        dt = datetime.fromisoformat(value)
        mins = int((dt - datetime.now()).total_seconds() // 60)
        if mins <= 0:
            return "Expired", "expired", mins
        if mins <= 30:
            return f"{mins}m left", "urgent", mins
        if mins <= 120:
            return f"{mins}m left", "danger", mins
        h, m = divmod(mins, 60)
        label = f"{h}h {m}m left" if m else f"{h}h left"
        return label, ("warning" if mins <= 360 else "normal"), mins
    except Exception:
        return "–", "normal", 0

def listing_dict(row):
    item = dict(row)
    item["available"] = max(0, item["quantity"] - item.get("reserved", 0))
    item["deadline_text"], item["deadline_class"], item["minutes_left"] = deadline_info(item["pickup_time"])
    if item.get("is_free"):
        item["price_display"] = "Free"
        item["price_short"]   = "Free"
    elif float(item["price"]) == 0:
        item["price_display"] = "Free"
        item["price_short"]   = "Free"
    elif float(item["price"]).is_integer():
        item["price_display"] = f"₹{int(item['price'])} total"
        item["price_short"]   = f"₹{int(item['price'])}"
    else:
        item["price_display"] = f"₹{item['price']:.2f} total"
        item["price_short"]   = f"₹{item['price']:.2f}"
    pt = item.get("pickup_time", "")
    item["pickup_time_display"] = pt[11:16] if len(pt) >= 16 else pt
    return item

PICKUP_STATUSES = ["Waiting", "Assigned", "On the way", "Arrived", "Verified", "Completed"]

def pickup_step(status):
    try:
        return PICKUP_STATUSES.index(status)
    except ValueError:
        return 0

@app.context_processor
def globals_for_templates():
    return {"me": current_user(), "pickup_statuses": PICKUP_STATUSES}

# ─── seed demo data ────────────────────────────────────────────────────────────

def seed_demo():
    conn = get_db()
    # already seeded?
    if conn.execute("SELECT id FROM users WHERE email='greenleftkitchen@demo.fr'").fetchone():
        conn.close()
        return

    pw  = generate_password_hash("demo1234")
    now = now_iso()

    # restaurants
    restaurants = [
        ("Chef Rajeev Nair",  "greenleftkitchen@demo.fr", "9820011111", "business",
         "Green Leaf Kitchen",       "Hill Road, Bandra West, Mumbai 400050"),
        ("Sunita Bakshi",     "sunrisebakery@demo.fr",    "9821022222", "business",
         "Sunrise Bakery",           "S.V. Road, Andheri West, Mumbai 400058"),
        ("Amit Freshwale",    "freshmart@demo.fr",         "9822033333", "business",
         "FreshMart Bandra",         "Turner Road, Bandra West, Mumbai 400050"),
        ("Priya Annapurna",   "annapurna@demo.fr",         "9823044444", "business",
         "Annapurna Cloud Kitchen",  "Linking Road, Khar West, Mumbai 400052"),
        ("Vikram Kolache",    "cafekolache@demo.fr",       "9824055555", "business",
         "Cafe Kolache",             "Juhu Tara Road, Juhu, Mumbai 400049"),
    ]
    r_ids = []
    for row in restaurants:
        cur = conn.execute("""
            INSERT INTO users(name,email,phone,role,organization_name,address,password_hash,created_at)
            VALUES(?,?,?,?,?,?,?,?)""", (*row, pw, now))
        r_ids.append(cur.lastrowid)
    conn.commit()

    # NGOs
    ngos = [
        ("Mehul Sharma", "mumbai.relief@demo.fr",  "9810066666", "ngo",
         "Mumbai Food Relief",    "Dharavi, Mumbai 400017"),
        ("Leena Pillai", "roti.bank@demo.fr",       "9811077777", "ngo",
         "Roti Bank Foundation",  "Sion, Mumbai 400022"),
        ("Arjun Desai",  "nourish.india@demo.fr",   "9812088888", "ngo",
         "Nourish India Trust",   "Kurla West, Mumbai 400070"),
    ]
    n_ids = []
    for row in ngos:
        cur = conn.execute("""
            INSERT INTO users(name,email,phone,role,organization_name,address,password_hash,created_at)
            VALUES(?,?,?,?,?,?,?,?)""", (*row, pw, now))
        n_ids.append(cur.lastrowid)
    conn.commit()

    # listings  (bidx, name, cat, qty, price, is_free, hrs, ftype, desc)
    listings_data = [
        (0, "Veg Thali Batch",          "Restaurant", 50, 100, 0, 4, "Cooked",
            "Freshly cooked veg thali — dal, sabzi, rice, roti and pickle. Packed hot."),
        (0, "Paneer Wrap Surplus",       "Restaurant", 30,  60, 0, 3, "Cooked",
            "Paneer and capsicum wraps, individually packed. Made this afternoon."),
        (1, "Assorted Bread & Pastry",   "Bakery",     40,   0, 1, 2, "Packaged",
            "End-of-day bread loaves, dinner rolls and assorted pastries."),
        (1, "Croissant & Muffin Pack",   "Bakery",     25,  45, 0, 5, "Packaged",
            "Butter croissants and blueberry muffins baked this morning."),
        (2, "Fruit & Veg Crate",         "Grocery",    80,   0, 1, 6, "Raw",
            "Mixed seasonal fruits and vegetables. Surplus stock — perfectly edible."),
        (2, "Surplus Milk & Dairy",      "Grocery",    20,  35, 0, 3, "Packaged",
            "Full-cream milk pouches and curd packets. Well within expiry."),
        (3, "Bulk Rice & Dal",           "Restaurant", 60,   0, 1, 5, "Cooked",
            "Cooked basmati rice and yellow dal. Ideal for community feeding."),
        (3, "Biryani Surplus",           "Restaurant", 35,  80, 0, 2, "Cooked",
            "Fragrant vegetable biryani cooked fresh this evening."),
        (4, "Grilled Sandwich Surplus",  "Cafe",       22,  40, 0, 3, "Packaged",
            "Grilled veg and cheese sandwiches, individually wrapped."),
        (4, "Cold Coffee & Juice Pack",  "Cafe",       18,  30, 0, 2, "Packaged",
            "Cold coffee bottles and fresh juice packs from today's batch."),
    ]
    l_ids = []
    for row in listings_data:
        bidx, fname, cat, qty, price, is_free, hrs, ftype, desc = row
        pt = future_iso(hours=hrs)
        cur = conn.execute("""
            INSERT INTO listings(business_id,food_name,category,quantity,price,is_free,
                                 pickup_time,address,description,food_type,created_at,active,paused)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,1,0)""",
            (r_ids[bidx], fname, cat, qty, price, is_free, pt,
             restaurants[bidx][5], desc, ftype, now))
        l_ids.append(cur.lastrowid)
    conn.commit()

    # reservations  (lidx, nidx, qty, code, status)
    res_data = [
        (0, 0, 20, "FR-4827", "Reserved"),
        (2, 1, 15, "FR-5831", "Reserved"),
        (4, 2, 30, "FR-6192", "Reserved"),
        (6, 0, 25, "FR-7340", "Reserved"),
        (8, 1, 10, "FR-8803", "Completed"),
        (1, 2, 12, "FR-9115", "Reserved"),
    ]
    res_ids = []
    for row in res_data:
        lidx, nidx, qty, code, status = row
        cur = conn.execute("""
            INSERT INTO reservations(listing_id,ngo_id,quantity,code,status,created_at)
            VALUES(?,?,?,?,?,?)""",
            (l_ids[lidx], n_ids[nidx], qty, code, status, now))
        res_ids.append(cur.lastrowid)
    conn.commit()

    # pickup assignments  (ridx, name, phone, status, eta, assigned, arrived, completed)
    pickups = [
        (0, "Raju Delivery",   "9900011111", "On the way", 18, now,  None, None),
        (1, "Suresh Kumar",    "9900022222", "Arrived",     0, now,  now,  None),
        (2, "Priya Volunteer", "9900033333", "Assigned",   35, now,  None, None),
        (3, "Vinod Rider",     "9900044444", "Waiting",     0, None, None, None),
        (4, "Ashok Nair",      "9900055555", "Completed",   0, now,  now,  now),
        (5, "Deepa Courier",   "9900066666", "On the way", 22, now,  None, None),
    ]
    for row in pickups:
        ridx, pname, pphone, pstatus, eta, asgn, arrv, comp = row
        conn.execute("""
            INSERT INTO pickup_assignments(reservation_id,pickup_name,pickup_phone,
                                          status,eta_minutes,assigned_at,arrived_at,completed_at)
            VALUES(?,?,?,?,?,?,?,?)""",
            (res_ids[ridx], pname, pphone, pstatus, eta, asgn, arrv, comp))
    conn.commit()
    conn.close()

seed_demo()

# ─── public routes ─────────────────────────────────────────────────────────────

@app.route("/")
def landing():
    conn = get_db()
    total_rescued   = conn.execute("SELECT COALESCE(SUM(quantity),0) FROM reservations WHERE status='Completed'").fetchone()[0]
    active_listings = conn.execute("SELECT COUNT(*) FROM listings WHERE active=1 AND paused=0").fetchone()[0]
    partner_ngos    = conn.execute("SELECT COUNT(*) FROM users WHERE role='ngo'").fetchone()[0]
    partner_biz     = conn.execute("SELECT COUNT(*) FROM users WHERE role='business'").fetchone()[0]
    conn.close()
    return render_template("landing.html",
        total_rescued=total_rescued, active_listings=active_listings,
        partner_ngos=partner_ngos, partner_biz=partner_biz)

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email    = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE lower(email)=?", (email,)).fetchone()
        conn.close()
        if not user or not check_password_hash(user["password_hash"], password):
            return render_template("login.html", error="Incorrect email or password.")
        session.clear()
        session["user_id"] = user["id"]
        return redirect(url_for("home"))
    return render_template("login.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name     = request.form.get("name", "").strip()
        email    = request.form.get("email", "").strip().lower()
        phone    = request.form.get("phone", "").strip()
        role     = request.form.get("role", "").strip()
        org      = request.form.get("organization_name", "").strip()
        address  = request.form.get("address", "").strip()
        password = request.form.get("password", "")
        confirm  = request.form.get("confirm_password", "")

        if not all([name, email, phone, role, org, address, password, confirm]):
            return render_template("register.html", error="Please complete all required fields.")
        if role not in ("business", "ngo"):
            return render_template("register.html", error="Please choose an account type.")
        if password != confirm:
            return render_template("register.html", error="Passwords do not match.")
        if len(password) < 6:
            return render_template("register.html", error="Password must be at least 6 characters.")

        conn = get_db()
        try:
            conn.execute("""
                INSERT INTO users(name,email,phone,role,organization_name,address,password_hash,created_at)
                VALUES(?,?,?,?,?,?,?,?)""",
                (name, email, phone, role, org, address, generate_password_hash(password), now_iso()))
            conn.commit()
        except sqlite3.IntegrityError:
            conn.close()
            return render_template("register.html", error="An account with this email already exists.")
        conn.close()
        return redirect(url_for("login", registered="1"))
    return render_template("register.html")

@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    message = error = None
    if request.method == "POST":
        email  = request.form.get("email", "").strip().lower()
        new_pw = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        if not email or not new_pw or not confirm:
            error = "Please complete all fields."
        elif new_pw != confirm:
            error = "Passwords do not match."
        elif len(new_pw) < 6:
            error = "Password must be at least 6 characters."
        else:
            conn = get_db()
            user = conn.execute("SELECT id FROM users WHERE lower(email)=?", (email,)).fetchone()
            if not user:
                error = "No account found with that email."
            else:
                conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                             (generate_password_hash(new_pw), user["id"]))
                conn.commit()
                message = "Password updated. You can now log in."
            conn.close()
    return render_template("forgot_password.html", message=message, error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ─── home (role-branched) ──────────────────────────────────────────────────────

@app.route("/home")
@login_required
def home():
    user = current_user()
    conn = get_db()

    if user["role"] == "business":
        rows = conn.execute("""
            SELECT l.*, u.organization_name AS business_name,
                   COALESCE(SUM(r.quantity),0) AS reserved
            FROM listings l
            JOIN users u ON u.id = l.business_id
            LEFT JOIN reservations r ON r.listing_id = l.id AND r.status = 'Reserved'
            WHERE l.business_id = ? AND l.active = 1
            GROUP BY l.id ORDER BY l.pickup_time ASC
        """, (user["id"],)).fetchall()
        listings = [listing_dict(r) for r in rows]

        reservations_count = conn.execute("""
            SELECT COUNT(*) FROM reservations r
            JOIN listings l ON l.id = r.listing_id
            WHERE l.business_id = ? AND r.status = 'Reserved'
        """, (user["id"],)).fetchone()[0]

        upcoming_pickups = conn.execute("""
            SELECT pa.*, r.id AS res_id, r.code, r.quantity, l.food_name, l.pickup_time,
                   n.organization_name AS ngo_org
            FROM pickup_assignments pa
            JOIN reservations r ON r.id = pa.reservation_id
            JOIN listings l ON l.id = r.listing_id
            JOIN users n ON n.id = r.ngo_id
            WHERE l.business_id = ? AND pa.status NOT IN ('Completed')
            ORDER BY l.pickup_time ASC LIMIT 5
        """, (user["id"],)).fetchall()

        total_rescued = conn.execute("""
            SELECT COALESCE(SUM(r.quantity),0) FROM reservations r
            JOIN listings l ON l.id = r.listing_id
            WHERE l.business_id = ? AND r.status = 'Completed'
        """, (user["id"],)).fetchone()[0]

        conn.close()
        return render_template("home_business.html",
            listings=listings, reservations_count=reservations_count,
            upcoming_pickups=upcoming_pickups, total_rescued=total_rescued)

    # NGO home
    rows = conn.execute("""
        SELECT l.*, u.organization_name AS business_name,
               COALESCE(SUM(r.quantity),0) AS reserved
        FROM listings l
        JOIN users u ON u.id = l.business_id
        LEFT JOIN reservations r ON r.listing_id = l.id AND r.status = 'Reserved'
        WHERE l.active = 1 AND l.paused = 0
        GROUP BY l.id ORDER BY l.pickup_time ASC
    """).fetchall()
    listings = [listing_dict(r) for r in rows
                if listing_dict(r)["available"] > 0 and listing_dict(r)["minutes_left"] > 0]

    my_reservations = conn.execute("""
        SELECT r.*, l.food_name, l.pickup_time, l.address,
               b.organization_name AS business_name,
               pa.status AS pickup_status, pa.eta_minutes
        FROM reservations r
        JOIN listings l ON l.id = r.listing_id
        JOIN users b ON b.id = l.business_id
        LEFT JOIN pickup_assignments pa ON pa.reservation_id = r.id
        WHERE r.ngo_id = ? AND r.status = 'Reserved'
        ORDER BY l.pickup_time ASC LIMIT 3
    """, (user["id"],)).fetchall()

    total_rescued = conn.execute(
        "SELECT COALESCE(SUM(quantity),0) FROM reservations WHERE ngo_id=? AND status='Completed'",
        (user["id"],)).fetchone()[0]

    conn.close()
    return render_template("home_ngo.html",
        listings=listings[:6], my_reservations=my_reservations,
        total_rescued=total_rescued)

# ─── business: add / edit / toggle / remove listing ───────────────────────────

@app.route("/add-surplus", methods=["GET", "POST"])
@role_required("business")
def add_surplus():
    user = current_user()
    if request.method == "POST":
        food_name   = request.form.get("food_name", "").strip()
        category    = request.form.get("category", "").strip()
        food_type   = request.form.get("food_type", "Cooked").strip()
        address     = request.form.get("address", "").strip()
        description = request.form.get("description", "").strip()
        pickup      = request.form.get("pickup_time", "").strip()
        is_free     = 1 if request.form.get("pricing_model") == "free" else 0
        try:
            quantity = int(request.form.get("quantity", 0))
            price    = 0.0 if is_free else float(request.form.get("price", 0) or 0)
        except ValueError:
            quantity, price = 0, 0.0

        if not all([food_name, category, address, pickup]) or quantity <= 0:
            return render_template("add_surplus.html",
                error="Please complete all required fields with valid values.")
        try:
            pickup_dt = datetime.fromisoformat(pickup)
            if pickup_dt <= datetime.now():
                return render_template("add_surplus.html",
                    error="Pickup deadline must be in the future.")
        except ValueError:
            return render_template("add_surplus.html",
                error="Please enter a valid pickup deadline.")

        conn = get_db()
        conn.execute("""
            INSERT INTO listings(business_id,food_name,category,quantity,price,is_free,
                                 pickup_time,address,description,food_type,created_at,active,paused)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,1,0)""",
            (user["id"], food_name, category, quantity, price, is_free,
             pickup, address, description, food_type, now_iso()))
        conn.commit()
        conn.close()
        flash("Surplus published successfully.", "success")
        return redirect(url_for("home"))
    return render_template("add_surplus.html")

@app.route("/edit-listing/<int:listing_id>", methods=["GET", "POST"])
@role_required("business")
def edit_listing(listing_id):
    user = current_user()
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM listings WHERE id=? AND business_id=? AND active=1",
        (listing_id, user["id"])).fetchone()
    if not row:
        conn.close()
        flash("Listing not found.", "error")
        return redirect(url_for("home"))

    if request.method == "POST":
        food_name   = request.form.get("food_name", "").strip()
        category    = request.form.get("category", "").strip()
        food_type   = request.form.get("food_type", "Cooked").strip()
        address     = request.form.get("address", "").strip()
        description = request.form.get("description", "").strip()
        pickup      = request.form.get("pickup_time", "").strip()
        is_free     = 1 if request.form.get("pricing_model") == "free" else 0
        try:
            quantity = int(request.form.get("quantity", 0))
            price    = 0.0 if is_free else float(request.form.get("price", 0) or 0)
        except ValueError:
            quantity, price = 0, 0.0

        if not all([food_name, category, address, pickup]) or quantity <= 0:
            conn.close()
            return render_template("add_surplus.html",
                error="Please complete all required fields.", listing=dict(row), edit=True)

        conn.execute("""
            UPDATE listings SET food_name=?,category=?,quantity=?,price=?,is_free=?,
                                pickup_time=?,address=?,description=?,food_type=?
            WHERE id=?""",
            (food_name, category, quantity, price, is_free,
             pickup, address, description, food_type, listing_id))
        conn.commit()
        conn.close()
        flash("Listing updated.", "success")
        return redirect(url_for("home"))

    conn.close()
    return render_template("add_surplus.html", listing=dict(row), edit=True)

@app.route("/toggle-listing/<int:listing_id>", methods=["POST"])
@role_required("business")
def toggle_listing(listing_id):
    user = current_user()
    conn = get_db()
    row = conn.execute(
        "SELECT id, paused FROM listings WHERE id=? AND business_id=? AND active=1",
        (listing_id, user["id"])).fetchone()
    if not row:
        conn.close()
        flash("Listing not found.", "error")
        return redirect(url_for("home"))
    new_paused = 0 if row["paused"] else 1
    conn.execute("UPDATE listings SET paused=? WHERE id=?", (new_paused, listing_id))
    conn.commit()
    conn.close()
    flash("Listing paused." if new_paused else "Listing resumed and visible to NGOs.", "success")
    return redirect(url_for("home"))

@app.route("/remove-listing/<int:listing_id>", methods=["POST"])
@role_required("business")
def remove_listing(listing_id):
    user = current_user()
    conn = get_db()
    row = conn.execute(
        "SELECT id FROM listings WHERE id=? AND business_id=? AND active=1",
        (listing_id, user["id"])).fetchone()
    if not row:
        conn.close()
        flash("Listing not found.", "error")
        return redirect(url_for("home"))
    conn.execute("UPDATE listings SET active=0 WHERE id=?", (listing_id,))
    conn.commit()
    conn.close()
    flash("Listing removed.", "success")
    return redirect(url_for("home"))

# ─── NGO: discover ─────────────────────────────────────────────────────────────

@app.route("/discover")
@role_required("ngo")
def discover():
    q        = request.args.get("q", "").strip().lower()
    category = request.args.get("category", "All")
    pricing  = request.args.get("pricing", "All")
    conn = get_db()
    rows = conn.execute("""
        SELECT l.*, u.organization_name AS business_name,
               COALESCE(SUM(r.quantity),0) AS reserved
        FROM listings l
        JOIN users u ON u.id = l.business_id
        LEFT JOIN reservations r ON r.listing_id = l.id AND r.status = 'Reserved'
        WHERE l.active = 1 AND l.paused = 0
        GROUP BY l.id ORDER BY l.pickup_time ASC
    """).fetchall()
    result = []
    for row in rows:
        item = listing_dict(row)
        if item["available"] <= 0 or item["minutes_left"] <= 0:
            continue
        haystack = f'{item["food_name"]} {item["business_name"]} {item["address"]}'.lower()
        if q and q not in haystack:
            continue
        if category != "All" and item["category"] != category:
            continue
        if pricing == "Free" and not item["is_free"]:
            continue
        if pricing == "Low-cost" and item["is_free"]:
            continue
        result.append(item)
    conn.close()
    return render_template("discover.html", listings=result, q=q, category=category, pricing=pricing)

# ─── NGO: reserve ──────────────────────────────────────────────────────────────

@app.route("/reserve/<int:listing_id>", methods=["POST"])
@role_required("ngo")
def reserve(listing_id):
    user = current_user()
    try:
        quantity = int(request.form.get("quantity", 1))
    except ValueError:
        quantity = 0
    if quantity < 1:
        flash("Please enter a valid quantity.", "error")
        return redirect(url_for("discover"))

    conn = get_db()
    row = conn.execute("""
        SELECT l.*, COALESCE(SUM(r.quantity),0) AS reserved
        FROM listings l
        LEFT JOIN reservations r ON r.listing_id = l.id AND r.status = 'Reserved'
        WHERE l.id = ? AND l.active = 1 AND l.paused = 0
        GROUP BY l.id
    """, (listing_id,)).fetchone()

    if not row:
        conn.close()
        flash("This listing is no longer available.", "error")
        return redirect(url_for("discover"))

    item = listing_dict(row)
    if item["minutes_left"] <= 0:
        conn.close()
        flash("This pickup deadline has passed.", "error")
        return redirect(url_for("discover"))
    if quantity > item["available"]:
        conn.close()
        flash(f"Only {item['available']} available.", "error")
        return redirect(url_for("discover"))

    # generate a short readable OTP code
    code = "FR-" + str(uuid.uuid4().int)[:4]
    cur = conn.execute("""
        INSERT INTO reservations(listing_id,ngo_id,quantity,code,status,created_at)
        VALUES(?,?,?,?,?,?)""",
        (listing_id, user["id"], quantity, code, "Reserved", now_iso()))
    res_id = cur.lastrowid

    # auto-create pickup assignment in Waiting state
    conn.execute("""
        INSERT INTO pickup_assignments(reservation_id,pickup_name,pickup_phone,
                                      status,eta_minutes,assigned_at,arrived_at,completed_at)
        VALUES(?,?,?,'Waiting',0,NULL,NULL,NULL)""",
        (res_id, "Pickup Volunteer", ""))
    conn.commit()
    conn.close()

    flash(f"Reservation confirmed. Your pickup code is {code}.", "success")
    return redirect(url_for("reservation_detail", reservation_id=res_id))

# ─── reservation detail ────────────────────────────────────────────────────────

@app.route("/reservation/<int:reservation_id>")
@login_required
def reservation_detail(reservation_id):
    user = current_user()
    conn = get_db()
    r = conn.execute("""
        SELECT r.*, l.food_name, l.pickup_time, l.address, l.price, l.is_free,
               l.quantity AS total_qty, l.description, l.food_type,
               b.organization_name AS business_name,
               b.phone AS business_phone, b.email AS business_email,
               n.organization_name AS ngo_org,
               n.name AS ngo_name, n.email AS ngo_email, n.phone AS ngo_phone
        FROM reservations r
        JOIN listings l ON l.id = r.listing_id
        JOIN users b ON b.id = l.business_id
        JOIN users n ON n.id = r.ngo_id
        WHERE r.id = ?
    """, (reservation_id,)).fetchone()

    if not r:
        conn.close()
        flash("Reservation not found.", "error")
        return redirect(url_for("reservations"))

    # security
    if user["role"] == "ngo" and r["ngo_id"] != user["id"]:
        conn.close()
        flash("Access denied.", "error")
        return redirect(url_for("reservations"))

    pa = conn.execute(
        "SELECT * FROM pickup_assignments WHERE reservation_id=?", (reservation_id,)
    ).fetchone()
    conn.close()

    res = dict(r)
    res["pickup_time_display"] = res["pickup_time"][11:16] if len(res["pickup_time"]) >= 16 else res["pickup_time"]
    if res["is_free"] or float(res["price"]) == 0:
        res["price_display"] = "Free"
    elif float(res["price"]).is_integer():
        res["price_display"] = f"₹{int(res['price'])} total"
    else:
        res["price_display"] = f"₹{res['price']:.2f} total"

    pstatus = (pa["status"] if pa else "Waiting")
    return render_template("reservation_detail.html",
        reservation=res,
        pickup=dict(pa) if pa else None,
        pickup_step=pickup_step(pstatus))

# ─── reservations list ─────────────────────────────────────────────────────────

@app.route("/reservations")
@login_required
def reservations():
    user = current_user()
    conn = get_db()

    if user["role"] == "ngo":
        rows = conn.execute("""
            SELECT r.*, l.food_name, l.pickup_time, l.address, l.price, l.is_free,
                   b.organization_name AS business_name,
                   b.phone AS business_phone, b.email AS business_email,
                   pa.status AS pickup_status, pa.eta_minutes, pa.pickup_name
            FROM reservations r
            JOIN listings l ON l.id = r.listing_id
            JOIN users b ON b.id = l.business_id
            LEFT JOIN pickup_assignments pa ON pa.reservation_id = r.id
            WHERE r.ngo_id = ?
            ORDER BY r.created_at DESC
        """, (user["id"],)).fetchall()
        conn.close()
        result = []
        for row in rows:
            d = dict(row)
            d["pickup_time_display"] = d["pickup_time"][11:16] if len(d["pickup_time"]) >= 16 else d["pickup_time"]
            if d["is_free"] or float(d["price"]) == 0:
                d["price_display"] = "Free"
            elif float(d["price"]).is_integer():
                d["price_display"] = f"₹{int(d['price'])} total"
            else:
                d["price_display"] = f"₹{d['price']:.2f} total"
            d["pickup_step"] = pickup_step(d.get("pickup_status") or "Waiting")
            result.append(d)
        return render_template("reservations_ngo.html", reservations=result)

    # business view
    rows = conn.execute("""
        SELECT r.*, l.food_name, l.pickup_time, l.address,
               n.name AS ngo_name, n.email AS ngo_email, n.phone AS ngo_phone,
               n.organization_name AS ngo_org,
               pa.status AS pickup_status, pa.eta_minutes,
               pa.pickup_name, pa.pickup_phone, pa.id AS pa_id
        FROM reservations r
        JOIN listings l ON l.id = r.listing_id
        JOIN users n ON n.id = r.ngo_id
        LEFT JOIN pickup_assignments pa ON pa.reservation_id = r.id
        WHERE l.business_id = ?
        ORDER BY r.created_at DESC
    """, (user["id"],)).fetchall()
    conn.close()
    result = []
    for row in rows:
        d = dict(row)
        d["pickup_time_display"] = d["pickup_time"][11:16] if len(d["pickup_time"]) >= 16 else d["pickup_time"]
        d["pickup_step"] = pickup_step(d.get("pickup_status") or "Waiting")
        result.append(d)
    return render_template("reservations_business.html",
        reservations=result, pickup_statuses=PICKUP_STATUSES)

# ─── pickup: assign ────────────────────────────────────────────────────────────

@app.route("/pickup/assign/<int:reservation_id>", methods=["POST"])
@role_required("business")
def assign_pickup(reservation_id):
    user = current_user()
    conn = get_db()
    r = conn.execute("""
        SELECT r.id FROM reservations r
        JOIN listings l ON l.id = r.listing_id
        WHERE r.id = ? AND l.business_id = ?
    """, (reservation_id, user["id"])).fetchone()
    if not r:
        conn.close()
        flash("Access denied.", "error")
        return redirect(url_for("reservations"))

    pickup_name  = request.form.get("pickup_name", "").strip() or "Pickup Volunteer"
    pickup_phone = request.form.get("pickup_phone", "").strip()
    try:
        eta = int(request.form.get("eta_minutes", 0))
    except ValueError:
        eta = 0

    pa = conn.execute(
        "SELECT id FROM pickup_assignments WHERE reservation_id=?", (reservation_id,)
    ).fetchone()

    if pa:
        conn.execute("""
            UPDATE pickup_assignments
            SET pickup_name=?,pickup_phone=?,status='Assigned',eta_minutes=?,assigned_at=?
            WHERE reservation_id=?""",
            (pickup_name, pickup_phone, eta, now_iso(), reservation_id))
    else:
        conn.execute("""
            INSERT INTO pickup_assignments(reservation_id,pickup_name,pickup_phone,
                                          status,eta_minutes,assigned_at,arrived_at,completed_at)
            VALUES(?,?,?,'Assigned',?,?,NULL,NULL)""",
            (reservation_id, pickup_name, pickup_phone, eta, now_iso()))

    conn.commit()
    conn.close()
    flash("Pickup assigned.", "success")
    return redirect(url_for("reservations"))

# ─── pickup: update status ─────────────────────────────────────────────────────

@app.route("/pickup/status/<int:reservation_id>", methods=["POST"])
@role_required("business")
def update_pickup_status(reservation_id):
    user = current_user()
    new_status = request.form.get("status", "").strip()
    if new_status not in PICKUP_STATUSES:
        flash("Invalid status.", "error")
        return redirect(url_for("reservations"))

    conn = get_db()
    r = conn.execute("""
        SELECT r.id FROM reservations r
        JOIN listings l ON l.id = r.listing_id
        WHERE r.id = ? AND l.business_id = ?
    """, (reservation_id, user["id"])).fetchone()
    if not r:
        conn.close()
        flash("Access denied.", "error")
        return redirect(url_for("reservations"))

    arrived_at   = now_iso() if new_status == "Arrived" else None
    completed_at = now_iso() if new_status in ("Verified", "Completed") else None

    conn.execute("""
        UPDATE pickup_assignments
        SET status=?,
            arrived_at   = CASE WHEN ? IS NOT NULL THEN ? ELSE arrived_at   END,
            completed_at = CASE WHEN ? IS NOT NULL THEN ? ELSE completed_at END
        WHERE reservation_id=?""",
        (new_status, arrived_at, arrived_at, completed_at, completed_at, reservation_id))

    if new_status in ("Verified", "Completed"):
        conn.execute("UPDATE reservations SET status='Completed' WHERE id=?", (reservation_id,))

    conn.commit()
    conn.close()
    flash(f"Status updated to {new_status}.", "success")
    return redirect(url_for("reservations"))

# ─── pickup: OTP verify ────────────────────────────────────────────────────────

@app.route("/pickup/verify", methods=["POST"])
@role_required("business")
def verify_pickup():
    user = current_user()
    code = request.form.get("code", "").strip().upper()
    conn = get_db()

    r = conn.execute("""
        SELECT r.id, r.code, r.status, l.business_id
        FROM reservations r
        JOIN listings l ON l.id = r.listing_id
        WHERE upper(r.code) = ? AND l.business_id = ?
    """, (code, user["id"])).fetchone()

    if not r:
        conn.close()
        flash("Invalid pickup code. No matching reservation found for your restaurant.", "error")
        return redirect(url_for("reservations"))

    if r["status"] == "Completed":
        conn.close()
        flash("This reservation is already completed.", "error")
        return redirect(url_for("reservations"))

    conn.execute("UPDATE reservations SET status='Completed' WHERE id=?", (r["id"],))
    conn.execute("""
        UPDATE pickup_assignments
        SET status='Completed', completed_at=?
        WHERE reservation_id=?""", (now_iso(), r["id"]))
    conn.commit()
    conn.close()
    flash(f"✓ Pickup verified! Code {code} — food rescued successfully.", "success")
    return redirect(url_for("reservations"))

# ─── dashboard ─────────────────────────────────────────────────────────────────

@app.route("/dashboard")
@login_required
def dashboard():
    user = current_user()
    conn = get_db()

    if user["role"] == "business":
        listings_rows = conn.execute("""
            SELECT l.*, COALESCE(SUM(r.quantity),0) AS reserved
            FROM listings l
            LEFT JOIN reservations r ON r.listing_id = l.id AND r.status = 'Reserved'
            WHERE l.business_id = ? AND l.active = 1
            GROUP BY l.id ORDER BY l.pickup_time ASC
        """, (user["id"],)).fetchall()
        listings = [listing_dict(x) for x in listings_rows]

        total_reserved = conn.execute("""
            SELECT COALESCE(SUM(r.quantity),0) FROM reservations r
            JOIN listings l ON l.id = r.listing_id
            WHERE l.business_id = ? AND r.status = 'Reserved'
        """, (user["id"],)).fetchone()[0]

        total_rescued = conn.execute("""
            SELECT COALESCE(SUM(r.quantity),0) FROM reservations r
            JOIN listings l ON l.id = r.listing_id
            WHERE l.business_id = ? AND r.status = 'Completed'
        """, (user["id"],)).fetchone()[0]

        conn.close()
        return render_template("dashboard_business.html",
            listings=listings, total_reserved=total_reserved, total_rescued=total_rescued)

    # NGO
    reservations_count = conn.execute(
        "SELECT COUNT(*) FROM reservations WHERE ngo_id=? AND status='Reserved'",
        (user["id"],)).fetchone()[0]
    total_reserved_qty = conn.execute(
        "SELECT COALESCE(SUM(quantity),0) FROM reservations WHERE ngo_id=? AND status='Reserved'",
        (user["id"],)).fetchone()[0]
    total_rescued = conn.execute(
        "SELECT COALESCE(SUM(quantity),0) FROM reservations WHERE ngo_id=? AND status='Completed'",
        (user["id"],)).fetchone()[0]

    recent = conn.execute("""
        SELECT r.*, l.food_name, l.pickup_time, l.address,
               b.organization_name AS business_name,
               pa.status AS pickup_status, pa.eta_minutes
        FROM reservations r
        JOIN listings l ON l.id = r.listing_id
        JOIN users b ON b.id = l.business_id
        LEFT JOIN pickup_assignments pa ON pa.reservation_id = r.id
        WHERE r.ngo_id = ?
        ORDER BY r.created_at DESC LIMIT 5
    """, (user["id"],)).fetchall()

    conn.close()
    return render_template("dashboard_ngo.html",
        reservations_count=reservations_count,
        total_reserved_qty=total_reserved_qty,
        total_rescued=total_rescued,
        recent=recent)

# ─── profile ───────────────────────────────────────────────────────────────────

@app.route("/profile")
@login_required
def profile():
    return render_template("profile.html")

# ─── API ───────────────────────────────────────────────────────────────────────

@app.route("/api/listings")
def api_listings():
    conn = get_db()
    rows = conn.execute("""
        SELECT l.id, l.food_name, l.category, l.quantity, l.price, l.is_free,
               l.pickup_time, l.address, l.description,
               u.organization_name AS business_name,
               COALESCE(SUM(r.quantity),0) AS reserved
        FROM listings l
        JOIN users u ON u.id = l.business_id
        LEFT JOIN reservations r ON r.listing_id = l.id AND r.status = 'Reserved'
        WHERE l.active = 1 AND l.paused = 0
        GROUP BY l.id ORDER BY l.pickup_time ASC
    """).fetchall()
    conn.close()
    result = []
    for row in rows:
        item = listing_dict(row)
        if item["available"] > 0 and item["minutes_left"] > 0:
            result.append({k: item[k] for k in
                ["id","food_name","category","available","price","is_free",
                 "pickup_time","address","business_name","deadline_text","price_display"]})
    return jsonify(result)

if __name__ == "__main__":
    print(f"\n  FoodRescue  →  http://127.0.0.1:{PORT}\n")
    app.run(host="127.0.0.1", port=PORT, debug=True)
