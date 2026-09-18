
from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from datetime import datetime
from pathlib import Path
import sqlite3, uuid

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "foodrescue.db"

app = Flask(__name__)
app.secret_key = "foodrescue-localhost-2026"
PORT = 5055

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
        created_at TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1,
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
    """)
    conn.commit()
    conn.close()

init_db()

def now_iso():
    return datetime.now().replace(microsecond=0).isoformat(timespec="minutes")

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
            return f"{mins} min left", "urgent", mins
        if mins <= 120:
            return f"{mins} min left", "danger", mins
        if mins <= 360:
            return f"{mins // 60}h {mins % 60}m left", "warning", mins
        return f"{mins // 60}h {mins % 60}m left", "normal", mins
    except Exception:
        return "Deadline unavailable", "normal", 0

def listing_dict(row, conn=None):
    item = dict(row)
    item["available"] = max(0, item["quantity"] - item.get("reserved", 0))
    item["deadline_text"], item["deadline_class"], item["minutes_left"] = deadline_info(item["pickup_time"])
    if item["is_free"]:
        item["price_display"] = "Free"
    elif float(item["price"]).is_integer():
        item["price_display"] = f"₹{int(item['price'])}"
    else:
        item["price_display"] = f"₹{item['price']:.2f}"
    return item

@app.context_processor
def globals_for_templates():
    return {"me": current_user()}


@app.route("/")
def landing():
    return render_template("landing.html")

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
            JOIN users u ON u.id=l.business_id
            LEFT JOIN reservations r ON r.listing_id=l.id AND r.status='Reserved'
            WHERE l.business_id=? AND l.active=1
            GROUP BY l.id
            ORDER BY l.created_at DESC
        """, (user["id"],)).fetchall()
        listings = [listing_dict(r) for r in rows]
        reservations_count = conn.execute("""
            SELECT COUNT(*) FROM reservations r
            JOIN listings l ON l.id=r.listing_id
            WHERE l.business_id=? AND r.status='Reserved'
        """, (user["id"],)).fetchone()[0]
        conn.close()
        return render_template("home_business.html", listings=listings, reservations_count=reservations_count)

    rows = conn.execute("""
        SELECT l.*, u.organization_name AS business_name,
               COALESCE(SUM(r.quantity),0) AS reserved
        FROM listings l
        JOIN users u ON u.id=l.business_id
        LEFT JOIN reservations r ON r.listing_id=l.id AND r.status='Reserved'
        WHERE l.active=1
        GROUP BY l.id
        ORDER BY l.pickup_time ASC
    """).fetchall()
    listings = [listing_dict(r) for r in rows if listing_dict(r)["available"] > 0 and listing_dict(r)["minutes_left"] > 0]
    conn.close()
    return render_template("home_ngo.html", listings=listings[:6])

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email","").strip().lower()
        password = request.form.get("password","")
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE lower(email)=?", (email,)).fetchone()
        conn.close()
        if not user or not check_password_hash(user["password_hash"], password):
            return render_template("login.html", error="Incorrect email or password.")
        session.clear()
        session["user_id"] = user["id"]
        return redirect(url_for("home"))
    return render_template("login.html")

@app.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name","").strip()
        email = request.form.get("email","").strip().lower()
        phone = request.form.get("phone","").strip()
        role = request.form.get("role","").strip()
        organization = request.form.get("organization_name","").strip()
        address = request.form.get("address","").strip()
        password = request.form.get("password","")
        confirm = request.form.get("confirm_password","")

        if not all([name,email,phone,role,organization,address,password,confirm]):
            return render_template("register.html", error="Please complete all required fields.")
        if role not in ("business","ngo"):
            return render_template("register.html", error="Please choose Restaurant / Food Business or NGO / Organization.")
        if password != confirm:
            return render_template("register.html", error="Passwords do not match.")
        if len(password) < 6:
            return render_template("register.html", error="Password must be at least 6 characters.")

        conn = get_db()
        try:
            conn.execute("""
                INSERT INTO users(name,email,phone,role,organization_name,address,password_hash,created_at)
                VALUES(?,?,?,?,?,?,?,?)
            """, (name,email,phone,role,organization,address,generate_password_hash(password),now_iso()))
            conn.commit()
        except sqlite3.IntegrityError:
            conn.close()
            return render_template("register.html", error="An account with this email already exists.")
        conn.close()
        return redirect(url_for("login", registered="1"))
    return render_template("register.html")

@app.route("/forgot-password", methods=["GET","POST"])
def forgot_password():
    message = None
    error = None
    if request.method == "POST":
        email = request.form.get("email","").strip().lower()
        new_password = request.form.get("new_password","")
        confirm = request.form.get("confirm_password","")
        if not email or not new_password or not confirm:
            error = "Please complete all fields."
        elif new_password != confirm:
            error = "Passwords do not match."
        elif len(new_password) < 6:
            error = "Password must be at least 6 characters."
        else:
            conn = get_db()
            user = conn.execute("SELECT id FROM users WHERE lower(email)=?", (email,)).fetchone()
            if not user:
                error = "No account was found with that email."
            else:
                conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                             (generate_password_hash(new_password), user["id"]))
                conn.commit()
                message = "Password updated successfully. You can now log in."
            conn.close()
    return render_template("forgot_password.html", message=message, error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/add-surplus", methods=["GET","POST"])
@role_required("business")
def add_surplus():
    user = current_user()
    if request.method == "POST":
        food_name = request.form.get("food_name","").strip()
        category = request.form.get("category","").strip()
        location = request.form.get("address","").strip()
        description = request.form.get("description","").strip()
        pickup = request.form.get("pickup_time","").strip()
        try:
            quantity = int(request.form.get("quantity","0"))
            price = float(request.form.get("price","0") or 0)
        except ValueError:
            quantity, price = 0, 0
        is_free = 1 if request.form.get("is_free") == "on" else 0

        if not all([food_name,category,location,pickup]) or quantity <= 0:
            return render_template("add_surplus.html", error="Please complete all required fields correctly.")
        try:
            pickup_dt = datetime.fromisoformat(pickup)
            if pickup_dt <= datetime.now():
                return render_template("add_surplus.html", error="Pickup deadline must be in the future.")
        except ValueError:
            return render_template("add_surplus.html", error="Please enter a valid pickup deadline.")

        conn = get_db()
        conn.execute("""
            INSERT INTO listings(business_id,food_name,category,quantity,price,is_free,pickup_time,address,description,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)
        """, (user["id"],food_name,category,quantity,price,is_free,pickup,location,description,now_iso()))
        conn.commit()
        conn.close()
        flash("Surplus food published successfully.", "success")
        return redirect(url_for("home"))
    return render_template("add_surplus.html")

@app.route("/remove-listing/<int:listing_id>", methods=["POST"])
@role_required("business")
def remove_listing(listing_id):
    user = current_user()
    conn = get_db()
    row = conn.execute("SELECT id FROM listings WHERE id=? AND business_id=? AND active=1",
                       (listing_id,user["id"])).fetchone()
    if not row:
        conn.close()
        flash("That listing could not be found.", "error")
        return redirect(url_for("home"))
    conn.execute("UPDATE listings SET active=0 WHERE id=?", (listing_id,))
    conn.commit()
    conn.close()
    flash("Food listing removed.", "success")
    return redirect(url_for("home"))

@app.route("/discover")
@role_required("ngo")
def discover():
    q = request.args.get("q","").strip().lower()
    category = request.args.get("category","All")
    conn = get_db()
    rows = conn.execute("""
        SELECT l.*, u.organization_name AS business_name,
               COALESCE(SUM(r.quantity),0) AS reserved
        FROM listings l
        JOIN users u ON u.id=l.business_id
        LEFT JOIN reservations r ON r.listing_id=l.id AND r.status='Reserved'
        WHERE l.active=1
        GROUP BY l.id
        ORDER BY l.pickup_time ASC
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
        result.append(item)
    conn.close()
    return render_template("discover.html", listings=result, q=q, category=category)

@app.route("/reserve/<int:listing_id>", methods=["POST"])
@role_required("ngo")
def reserve(listing_id):
    user = current_user()
    try:
        quantity = int(request.form.get("quantity","1"))
    except ValueError:
        quantity = 0
    if quantity < 1:
        flash("Please enter a valid quantity.", "error")
        return redirect(url_for("discover"))

    conn = get_db()
    row = conn.execute("""
        SELECT l.*, COALESCE(SUM(r.quantity),0) AS reserved
        FROM listings l
        LEFT JOIN reservations r ON r.listing_id=l.id AND r.status='Reserved'
        WHERE l.id=? AND l.active=1
        GROUP BY l.id
    """, (listing_id,)).fetchone()
    if not row:
        conn.close()
        flash("This food listing is no longer available.", "error")
        return redirect(url_for("discover"))
    item = listing_dict(row)
    if item["minutes_left"] <= 0:
        conn.close()
        flash("This pickup deadline has expired.", "error")
        return redirect(url_for("discover"))
    if quantity > item["available"]:
        conn.close()
        flash(f"Only {item['available']} item(s) are available.", "error")
        return redirect(url_for("discover"))

    code = "FR-" + uuid.uuid4().hex[:8].upper()
    conn.execute("""
        INSERT INTO reservations(listing_id,ngo_id,quantity,code,status,created_at)
        VALUES(?,?,?,?,?,?)
    """, (listing_id,user["id"],quantity,code,"Reserved",now_iso()))
    conn.commit()
    conn.close()
    flash(f"Reservation confirmed. Your code is {code}.", "success")
    return redirect(url_for("reservations"))

@app.route("/reservations")
@login_required
def reservations():
    user = current_user()
    conn = get_db()
    if user["role"] == "ngo":
        rows = conn.execute("""
            SELECT r.*, l.food_name, l.pickup_time, l.address, l.price, l.is_free,
                   b.organization_name AS business_name, b.phone AS business_phone,
                   b.email AS business_email
            FROM reservations r
            JOIN listings l ON l.id=r.listing_id
            JOIN users b ON b.id=l.business_id
            WHERE r.ngo_id=?
            ORDER BY r.created_at DESC
        """, (user["id"],)).fetchall()
        conn.close()
        return render_template("reservations_ngo.html", reservations=rows)

    rows = conn.execute("""
        SELECT r.*, l.food_name, l.pickup_time, l.address,
               n.name AS ngo_name, n.email AS ngo_email, n.phone AS ngo_phone,
               n.organization_name AS ngo_org
        FROM reservations r
        JOIN listings l ON l.id=r.listing_id
        JOIN users n ON n.id=r.ngo_id
        WHERE l.business_id=?
        ORDER BY r.created_at DESC
    """, (user["id"],)).fetchall()
    conn.close()
    return render_template("reservations_business.html", reservations=rows)

@app.route("/dashboard")
@login_required
def dashboard():
    user = current_user()
    conn = get_db()
    if user["role"] == "business":
        listings_rows = conn.execute("""
            SELECT l.*, COALESCE(SUM(r.quantity),0) AS reserved
            FROM listings l
            LEFT JOIN reservations r ON r.listing_id=l.id AND r.status='Reserved'
            WHERE l.business_id=? AND l.active=1
            GROUP BY l.id
            ORDER BY l.created_at DESC
        """, (user["id"],)).fetchall()
        listings = [listing_dict(x) for x in listings_rows]
        total_reserved = conn.execute("""
            SELECT COALESCE(SUM(r.quantity),0) FROM reservations r
            JOIN listings l ON l.id=r.listing_id
            WHERE l.business_id=? AND r.status='Reserved'
        """, (user["id"],)).fetchone()[0]
        conn.close()
        return render_template("dashboard_business.html", listings=listings, total_reserved=total_reserved)

    reservations_count = conn.execute(
        "SELECT COUNT(*) FROM reservations WHERE ngo_id=? AND status='Reserved'", (user["id"],)
    ).fetchone()[0]
    items = conn.execute(
        "SELECT COALESCE(SUM(quantity),0) FROM reservations WHERE ngo_id=? AND status='Reserved'", (user["id"],)
    ).fetchone()[0]
    conn.close()
    return render_template("dashboard_ngo.html", reservations_count=reservations_count, items=items)

@app.route("/profile")
@login_required
def profile():
    return render_template("profile.html")

if __name__ == "__main__":
    print(f"FoodRescue running at http://localhost:{PORT}")
    app.run(host="127.0.0.1", port=PORT, debug=True)
