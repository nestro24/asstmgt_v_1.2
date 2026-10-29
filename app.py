import csv
import io
import os
import secrets
import shutil
import sqlite3
import sys
import threading
from datetime import datetime, date
from functools import wraps
from flask import Flask, flash, redirect, render_template, request, url_for, session, Response, jsonify
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "assets.db")

app = Flask(__name__, template_folder=os.path.join(BASE_DIR, "templates"))
app.secret_key = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(32))
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Initializes tables for Assets, Categories, Locations, Users, and Audit Logs."""
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS assets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_tag TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                item_type TEXT,
                category TEXT NOT NULL DEFAULT 'General',
                serial_number TEXT,
                purchase_date TEXT,
                purchase_cost REAL DEFAULT 0,
                supplier TEXT,
                warranty_expiry TEXT,
                location TEXT,
                assigned_to TEXT,
                status TEXT CHECK(status IN ('In Use', 'In Storage', 'Under Maintenance', 'Retired')) DEFAULT 'In Storage',
                salvage_value REAL DEFAULT 0,
                useful_life_years INTEGER DEFAULT 0,
                depreciation_method TEXT DEFAULT 'straight-line',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL
            );
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS locations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL
            );
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                full_name TEXT,
                role TEXT CHECK(role IN ('Administrator', 'Data_Entry', 'Auditor', 'Viewer')) DEFAULT 'Viewer',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                username TEXT NOT NULL,
                action TEXT NOT NULL,
                details TEXT
            );
        """)

        # Seed standard categories if empty
        if not conn.execute("SELECT id FROM categories LIMIT 1").fetchone():
            default_cats = ["IT equipment", "Laptops", "Monitors", "Networking", "Servers", "Office Furniture", "Vehicles", "Mobile Devices"]
            for c in default_cats:
                conn.execute("INSERT OR IGNORE INTO categories (name) VALUES (?)", (c,))

        # Seed standard locations if empty
        if not conn.execute("SELECT id FROM locations LIMIT 1").fetchone():
            default_locs = ["Yenagoa HQ", "Abuja Branch", "Lagos Office", "Server Room A", "Floor 1 IT", "Floor 2 Executive", "Warehouse / Storage", "Remote Office"]
            for l in default_locs:
                conn.execute("INSERT OR IGNORE INTO locations (name) VALUES (?)", (l,))

        # Seed default administrator if empty
        user = conn.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()
        if not user:
            conn.execute(
                "INSERT INTO users (username, password_hash, full_name, role) VALUES (?, ?, ?, ?)",
                ("admin", generate_password_hash("Admin@12345"), "System Administrator", "Administrator")
            )
            conn.execute(
                "INSERT INTO audit_logs (username, action, details) VALUES (?, ?, ?)",
                ("System", "INIT", "System initialized with default Administrator account.")
            )
        conn.commit()


def log_action(username, action, details):
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO audit_logs (username, action, details) VALUES (?, ?, ?)",
                (username or "Anonymous", action, details)
            )
            conn.commit()
    except Exception as e:
        print(f"[Audit Log Error] {e}")


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function


def roles_required(*allowed_roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if "user_id" not in session:
                return redirect(url_for("login"))
            user_role = session.get("role", "Viewer")
            if user_role not in allowed_roles:
                flash(f"Access Denied: Your role ({user_role}) lacks permission.", "danger")
                return redirect(url_for("index", tab="assets"))
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def sanitize_csv_field(value: str) -> str:
    if not value:
        return ""
    clean_val = str(value).strip()
    if clean_val.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + clean_val
    return clean_val


def parse_float(val, default=0.0):
    try:
        clean = str(val).replace(",", "").replace("$", "").replace("₦", "").strip()
        return float(clean) if clean else default
    except (ValueError, TypeError):
        return default


def parse_int(val, default=0):
    try:
        clean = str(val).replace(",", "").strip()
        return int(float(clean)) if clean else default
    except (ValueError, TypeError):
        return default


def calculate_financials(asset):
    """Calculates straight-line depreciation, book value, and warranty alerts."""
    cost = parse_float(asset["purchase_cost"], 0.0)
    salvage = parse_float(asset["salvage_value"], 0.0)
    useful_years = parse_int(asset["useful_life_years"], 0)
    purch_date_str = asset["purchase_date"]
    
    current_book_value = cost
    annual_depreciation = 0.0
    age_years = 0.0

    if purch_date_str and useful_years > 0:
        try:
            purch_date = datetime.strptime(purch_date_str[:10], "%Y-%m-%d").date()
            today = date.today()
            age_days = (today - purch_date).days
            age_years = max(0.0, age_days / 365.25)
            
            depreciable_base = max(0.0, cost - salvage)
            annual_depreciation = depreciable_base / useful_years
            accumulated_depreciation = min(depreciable_base, annual_depreciation * age_years)
            current_book_value = max(salvage, cost - accumulated_depreciation)
        except Exception:
            pass

    # Warranty status
    warranty_status = "No Info"
    warranty_class = "secondary"
    w_expiry_str = asset["warranty_expiry"]
    if w_expiry_str:
        try:
            w_date = datetime.strptime(w_expiry_str[:10], "%Y-%m-%d").date()
            diff_days = (w_date - date.today()).days
            if diff_days < 0:
                warranty_status = "Expired"
                warranty_class = "danger"
            elif diff_days <= 60:
                warranty_status = f"Expiring ({diff_days}d)"
                warranty_class = "warning"
            else:
                warranty_status = "Active"
                warranty_class = "success"
        except Exception:
            pass

    return {
        "current_book_value": round(current_book_value, 2),
        "annual_depreciation": round(annual_depreciation, 2),
        "warranty_status": warranty_status,
        "warranty_class": warranty_class
    }


# ================= AUTHENTICATION =================

@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("index"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        with get_db() as conn:
            user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()

        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["full_name"] = user["full_name"]
            session["role"] = user["role"]
            log_action(user["username"], "LOGIN", "Signed in to console.")
            return redirect(url_for("index"))
        else:
            flash("Invalid username or password.", "danger")

    return render_template("login.html")


@app.route("/logout")
def logout():
    username = session.get("username", "Unknown")
    log_action(username, "LOGOUT", "Signed out of console.")
    session.clear()
    flash("You have been signed out.", "info")
    return redirect(url_for("login"))


# ================= MAIN CONSOLE & TABS =================

@app.route("/", methods=["GET"])
@login_required
def index():
    tab = request.args.get("tab", "assets")
    search_query = request.args.get("q", "").strip()
    category_filter = request.args.get("category", "").strip()
    status_filter = request.args.get("status", "").strip()
    location_filter = request.args.get("location", "").strip()

    if tab == "audit" and session.get("role") not in ["Administrator", "Auditor"]:
        flash("Access Denied: Only Administrators and Auditors may view audit logs.", "danger")
        return redirect(url_for("index", tab="assets"))

    with get_db() as conn:
        # Fetch managed dropdown options
        all_categories = [r["name"] for r in conn.execute("SELECT name FROM categories ORDER BY name ASC").fetchall()]
        all_locations = [r["name"] for r in conn.execute("SELECT name FROM locations ORDER BY name ASC").fetchall()]

        # Query all assets
        sql = "SELECT * FROM assets WHERE 1=1"
        params = []
        if search_query:
            sql += """ AND (asset_tag LIKE ? OR name LIKE ? OR serial_number LIKE ? 
                            OR assigned_to LIKE ? OR location LIKE ? OR supplier LIKE ? OR item_type LIKE ?)"""
            wild = f"%{search_query}%"
            params.extend([wild, wild, wild, wild, wild, wild, wild])
        if category_filter:
            sql += " AND category = ?"
            params.append(category_filter)
        if status_filter:
            sql += " AND status = ?"
            params.append(status_filter)
        if location_filter:
            sql += " AND location = ?"
            params.append(location_filter)

        sql += " ORDER BY id DESC"
        raw_assets = conn.execute(sql, params).fetchall()

        # Compute fleet metrics & financial depreciation
        total_fleet_cost = 0.0
        total_fleet_book_value = 0.0
        warranty_expiring_count = 0
        enriched_assets = []

        for row in raw_assets:
            row_dict = dict(row)
            fin = calculate_financials(row_dict)
            row_dict.update(fin)
            total_fleet_cost += parse_float(row_dict.get("purchase_cost", 0))
            total_fleet_book_value += fin["current_book_value"]
            if fin["warranty_class"] in ["warning", "danger"]:
                warranty_expiring_count += 1
            enriched_assets.append(row_dict)

        # Tab breakdown metrics
        cat_stats = conn.execute("""
            SELECT category, COUNT(*) as count 
            FROM assets WHERE category IS NOT NULL AND category != '' 
            GROUP BY category ORDER BY count DESC
        """).fetchall()

        loc_stats = conn.execute("""
            SELECT COALESCE(NULLIF(location, ''), 'Unassigned / HQ') as loc_name, COUNT(*) as count 
            FROM assets GROUP BY loc_name ORDER BY count DESC
        """).fetchall()

        assignee_stats = conn.execute("""
            SELECT COALESCE(NULLIF(assigned_to, ''), 'Unassigned') as assignee_name, COUNT(*) as count 
            FROM assets GROUP BY assignee_name ORDER BY count DESC
        """).fetchall()

        logs = []
        if session.get("role") in ["Administrator", "Auditor"]:
            logs = conn.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT 250").fetchall()

        user_list = []
        if session.get("role") == "Administrator":
            user_list = conn.execute("SELECT id, username, full_name, role, created_at FROM users ORDER BY id ASC").fetchall()

    return render_template(
        "index.html",
        active_tab=tab,
        assets=enriched_assets,
        categories=all_categories,
        locations=all_locations,
        cat_stats=cat_stats,
        loc_stats=loc_stats,
        assignee_stats=assignee_stats,
        audit_logs=logs,
        user_list=user_list,
        total_fleet_cost=total_fleet_cost,
        total_fleet_book_value=total_fleet_book_value,
        warranty_expiring_count=warranty_expiring_count,
        search_query=search_query,
        selected_category=category_filter,
        selected_status=status_filter,
        selected_location=location_filter,
        user=session
    )


# ================= ASSET CRUD, CHECK-IN/OUT, EDIT =================

@app.route("/assets/create", methods=["POST"])
@login_required
@roles_required("Administrator", "Data_Entry")
def create_asset():
    name = sanitize_csv_field(request.form.get("name", ""))
    asset_tag = sanitize_csv_field(request.form.get("asset_tag", ""))
    if not asset_tag:
        asset_tag = f"AST-{secrets.token_hex(3).upper()}"

    item_type = sanitize_csv_field(request.form.get("item_type", ""))
    category = sanitize_csv_field(request.form.get("category", "General")) or "General"
    serial_number = sanitize_csv_field(request.form.get("serial_number", ""))
    purchase_date = sanitize_csv_field(request.form.get("purchase_date", ""))
    purchase_cost = parse_float(request.form.get("purchase_cost", 0))
    supplier = sanitize_csv_field(request.form.get("supplier", ""))
    warranty_expiry = sanitize_csv_field(request.form.get("warranty_expiry", ""))
    location = sanitize_csv_field(request.form.get("location", ""))
    assigned_to = sanitize_csv_field(request.form.get("assigned_to", ""))

    raw_status = request.form.get("status", "In Storage").strip().title()
    status = raw_status if raw_status in {"In Use", "In Storage", "Under Maintenance", "Retired"} else "In Storage"

    salvage_value = parse_float(request.form.get("salvage_value", 0))
    useful_life_years = parse_int(request.form.get("useful_life_years", 0))
    depreciation_method = sanitize_csv_field(request.form.get("depreciation_method", "straight-line"))

    if not name:
        flash("Item / Asset Name is required.", "danger")
        return redirect(url_for("index", tab="assets"))

    try:
        with get_db() as conn:
            conn.execute("""
                INSERT INTO assets (
                    asset_tag, name, item_type, category, serial_number, 
                    purchase_date, purchase_cost, supplier, warranty_expiry, 
                    location, assigned_to, status, salvage_value, useful_life_years, depreciation_method
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                asset_tag, name, item_type or None, category, serial_number or None,
                purchase_date or None, purchase_cost, supplier or None, warranty_expiry or None,
                location or None, assigned_to or None, status, salvage_value, useful_life_years, depreciation_method
            ))
            # Keep categories & locations master tables in sync
            if category:
                conn.execute("INSERT OR IGNORE INTO categories (name) VALUES (?)", (category,))
            if location:
                conn.execute("INSERT OR IGNORE INTO locations (name) VALUES (?)", (location,))
            conn.commit()

        log_action(session.get("username"), "CREATE_ASSET", f"Added asset: {asset_tag} ({name})")
        flash(f"Asset '{name}' (Tag: {asset_tag}) registered successfully.", "success")
    except sqlite3.IntegrityError:
        flash(f"Error: Asset Tag '{asset_tag}' already exists in system.", "danger")

    return redirect(url_for("index", tab="assets"))


@app.route("/assets/get/<int:asset_id>")
@login_required
def get_asset_json(asset_id):
    """Returns single asset data as JSON for edit/detail modals."""
    with get_db() as conn:
        asset = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
        if not asset:
            return jsonify({"status": "error", "message": "Asset not found"}), 404
        return jsonify(dict(asset))


@app.route("/assets/edit/<int:asset_id>", methods=["POST"])
@login_required
@roles_required("Administrator", "Data_Entry")
def edit_asset(asset_id):
    name = sanitize_csv_field(request.form.get("name", ""))
    item_type = sanitize_csv_field(request.form.get("item_type", ""))
    category = sanitize_csv_field(request.form.get("category", "General")) or "General"
    serial_number = sanitize_csv_field(request.form.get("serial_number", ""))
    purchase_date = sanitize_csv_field(request.form.get("purchase_date", ""))
    purchase_cost = parse_float(request.form.get("purchase_cost", 0))
    supplier = sanitize_csv_field(request.form.get("supplier", ""))
    warranty_expiry = sanitize_csv_field(request.form.get("warranty_expiry", ""))
    location = sanitize_csv_field(request.form.get("location", ""))
    assigned_to = sanitize_csv_field(request.form.get("assigned_to", ""))

    raw_status = request.form.get("status", "In Storage").strip().title()
    status = raw_status if raw_status in {"In Use", "In Storage", "Under Maintenance", "Retired"} else "In Storage"

    salvage_value = parse_float(request.form.get("salvage_value", 0))
    useful_life_years = parse_int(request.form.get("useful_life_years", 0))
    depreciation_method = sanitize_csv_field(request.form.get("depreciation_method", "straight-line"))

    with get_db() as conn:
        conn.execute("""
            UPDATE assets SET 
                name = ?, item_type = ?, category = ?, serial_number = ?, 
                purchase_date = ?, purchase_cost = ?, supplier = ?, warranty_expiry = ?, 
                location = ?, assigned_to = ?, status = ?, salvage_value = ?, 
                useful_life_years = ?, depreciation_method = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (
            name, item_type or None, category, serial_number or None,
            purchase_date or None, purchase_cost, supplier or None, warranty_expiry or None,
            location or None, assigned_to or None, status, salvage_value, 
            useful_life_years, depreciation_method, asset_id
        ))
        if category:
            conn.execute("INSERT OR IGNORE INTO categories (name) VALUES (?)", (category,))
        if location:
            conn.execute("INSERT OR IGNORE INTO locations (name) VALUES (?)", (location,))
        conn.commit()

    log_action(session.get("username"), "EDIT_ASSET", f"Updated asset ID #{asset_id} ({name})")
    flash(f"Asset '{name}' updated successfully.", "success")
    return redirect(url_for("index", tab="assets"))


@app.route("/assets/checkout/<int:asset_id>", methods=["POST"])
@login_required
@roles_required("Administrator", "Data_Entry")
def checkout_asset(asset_id):
    """Assigns asset to employee and marks In Use."""
    assignee = sanitize_csv_field(request.form.get("assigned_to", ""))
    new_location = sanitize_csv_field(request.form.get("location", ""))

    with get_db() as conn:
        item = conn.execute("SELECT asset_tag, name FROM assets WHERE id = ?", (asset_id,)).fetchone()
        conn.execute("""
            UPDATE assets SET assigned_to = ?, location = COALESCE(NULLIF(?, ''), location), 
                              status = 'In Use', updated_at = CURRENT_TIMESTAMP 
            WHERE id = ?
        """, (assignee, new_location, asset_id))
        conn.commit()

    tag = item["asset_tag"] if item else f"#{asset_id}"
    log_action(session.get("username"), "CHECKOUT", f"Checked out {tag} to '{assignee}'")
    flash(f"Asset '{tag}' checked out to {assignee}.", "success")
    return redirect(url_for("index", tab="assets"))


@app.route("/assets/checkin/<int:asset_id>", methods=["POST"])
@login_required
@roles_required("Administrator", "Data_Entry")
def checkin_asset(asset_id):
    """Returns asset to storage and clears assignee."""
    storage_loc = sanitize_csv_field(request.form.get("location", "Warehouse / Storage"))

    with get_db() as conn:
        item = conn.execute("SELECT asset_tag, assigned_to FROM assets WHERE id = ?", (asset_id,)).fetchone()
        prev_assignee = item["assigned_to"] if item else "Unknown"
        conn.execute("""
            UPDATE assets SET assigned_to = NULL, location = ?, 
                              status = 'In Storage', updated_at = CURRENT_TIMESTAMP 
            WHERE id = ?
        """, (storage_loc, asset_id))
        conn.commit()

    tag = item["asset_tag"] if item else f"#{asset_id}"
    log_action(session.get("username"), "CHECKIN", f"Checked in {tag} from '{prev_assignee}' to '{storage_loc}'")
    flash(f"Asset '{tag}' returned to storage.", "info")
    return redirect(url_for("index", tab="assets"))


@app.route("/assets/delete/<int:asset_id>", methods=["POST"])
@login_required
@roles_required("Administrator")
def delete_asset(asset_id):
    with get_db() as conn:
        item = conn.execute("SELECT asset_tag, name FROM assets WHERE id = ?", (asset_id,)).fetchone()
        conn.execute("DELETE FROM assets WHERE id = ?", (asset_id,))
        conn.commit()
    tag = item["asset_tag"] if item else f"ID {asset_id}"
    log_action(session.get("username"), "DELETE_ASSET", f"Deleted asset record: {tag}")
    flash(f"Asset '{tag}' deleted successfully.", "success")
    return redirect(url_for("index", tab="assets"))


# ================= CATEGORY & LOCATION MANAGERS =================

@app.route("/categories/add", methods=["POST"])
@login_required
@roles_required("Administrator", "Data_Entry")
def add_category():
    name = sanitize_csv_field(request.form.get("name", ""))
    if name:
        with get_db() as conn:
            conn.execute("INSERT OR IGNORE INTO categories (name) VALUES (?)", (name,))
            conn.commit()
        log_action(session.get("username"), "ADD_CATEGORY", f"Created category '{name}'")
        flash(f"Category '{name}' added.", "success")
    return redirect(url_for("index", tab="categories"))


@app.route("/locations/add", methods=["POST"])
@login_required
@roles_required("Administrator", "Data_Entry")
def add_location():
    name = sanitize_csv_field(request.form.get("name", ""))
    if name:
        with get_db() as conn:
            conn.execute("INSERT OR IGNORE INTO locations (name) VALUES (?)", (name,))
            conn.commit()
        log_action(session.get("username"), "ADD_LOCATION", f"Created location '{name}'")
        flash(f"Location '{name}' added.", "success")
    return redirect(url_for("index", tab="locations"))


# ================= BULK CSV IMPORT =================

@app.route("/assets/upload", methods=["POST"])
@login_required
@roles_required("Administrator", "Data_Entry")
def upload_csv():
    if "file" not in request.files:
        flash("No file was uploaded.", "danger")
        return redirect(url_for("index", tab="assets"))

    file = request.files["file"]
    if not file or not file.filename.lower().endswith(".csv"):
        flash("Please upload a valid .csv file.", "danger")
        return redirect(url_for("index", tab="assets"))

    try:
        content = file.stream.read().decode("utf-8-sig", errors="replace")
        stream = io.StringIO(content)
        reader = csv.DictReader(stream)

        if not reader.fieldnames:
            flash("The uploaded CSV is empty.", "danger")
            return redirect(url_for("index", tab="assets"))

        alias_map = {
            "name": ["item name", "name", "device name", "model", "asset name", "item"],
            "asset_tag": ["asset tag", "tag", "asset_tag", "barcode", "tag id"],
            "item_type": ["type of item", "item type", "type"],
            "category": ["category", "cat"],
            "serial_number": ["serial / product id", "serial number", "serial", "s/n", "serial_no"],
            "purchase_date": ["purchase date", "purchased on", "date purchased"],
            "purchase_cost": ["purchase cost", "cost", "price", "amount"],
            "supplier": ["supplier", "vendor"],
            "warranty_expiry": ["warranty expiry", "warranty", "warranty expiration"],
            "location": ["location", "site", "office", "branch"],
            "assigned_to": ["assigned employee", "assigned to", "assigned_to", "staff", "employee"],
            "status": ["status", "condition", "state"],
            "salvage_value": ["salvage value", "salvage_value", "residual value"],
            "useful_life_years": ["useful life (years)", "useful life", "useful_life"],
            "depreciation_method": ["depreciation method", "depreciation_method", "depreciation"]
        }

        col_lookup = {}
        for internal_key, variations in alias_map.items():
            for header in reader.fieldnames:
                if header.strip().lower() in variations:
                    col_lookup[internal_key] = header
                    break

        if "name" not in col_lookup:
            flash("CSV must contain an 'Item Name' or 'name' column.", "danger")
            return redirect(url_for("index", tab="assets"))

        imported = 0
        skipped = 0
        status_lookup = {
            "in use": "In Use",
            "in storage": "In Storage",
            "under maintenance": "Under Maintenance",
            "retired": "Retired"
        }

        with get_db() as conn:
            for row in reader:
                name = sanitize_csv_field(row.get(col_lookup.get("name"), ""))
                if not name:
                    skipped += 1
                    continue

                raw_tag = row.get(col_lookup.get("asset_tag"), "") if "asset_tag" in col_lookup else ""
                tag = sanitize_csv_field(raw_tag)
                if not tag:
                    tag = f"AST-{secrets.token_hex(3).upper()}"

                item_type = sanitize_csv_field(row.get(col_lookup.get("item_type"), "")) if "item_type" in col_lookup else ""
                category = sanitize_csv_field(row.get(col_lookup.get("category"), "General")) if "category" in col_lookup else "General"
                serial = sanitize_csv_field(row.get(col_lookup.get("serial_number"), "")) if "serial_number" in col_lookup else ""
                purchase_date = sanitize_csv_field(row.get(col_lookup.get("purchase_date"), "")) if "purchase_date" in col_lookup else ""
                purchase_cost = parse_float(row.get(col_lookup.get("purchase_cost"), 0) if "purchase_cost" in col_lookup else 0)
                supplier = sanitize_csv_field(row.get(col_lookup.get("supplier"), "")) if "supplier" in col_lookup else ""
                warranty_expiry = sanitize_csv_field(row.get(col_lookup.get("warranty_expiry"), "")) if "warranty_expiry" in col_lookup else ""
                location = sanitize_csv_field(row.get(col_lookup.get("location"), "")) if "location" in col_lookup else ""
                assigned_to = sanitize_csv_field(row.get(col_lookup.get("assigned_to"), "")) if "assigned_to" in col_lookup else ""

                raw_status = (row.get(col_lookup.get("status"), "") or "").strip().lower() if "status" in col_lookup else "in storage"
                status = status_lookup.get(raw_status, "In Storage")

                salvage_value = parse_float(row.get(col_lookup.get("salvage_value"), 0) if "salvage_value" in col_lookup else 0)
                useful_life = parse_int(row.get(col_lookup.get("useful_life_years"), 0) if "useful_life_years" in col_lookup else 0)
                deprec = sanitize_csv_field(row.get(col_lookup.get("depreciation_method"), "straight-line") if "depreciation_method" in col_lookup else "straight-line")

                try:
                    conn.execute("""
                        INSERT INTO assets (
                            asset_tag, name, item_type, category, serial_number, 
                            purchase_date, purchase_cost, supplier, warranty_expiry, 
                            location, assigned_to, status, salvage_value, useful_life_years, depreciation_method
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        tag, name, item_type or None, category or "General", serial or None,
                        purchase_date or None, purchase_cost, supplier or None, warranty_expiry or None,
                        location or None, assigned_to or None, status, salvage_value, useful_life, deprec or "straight-line"
                    ))
                    if category:
                        conn.execute("INSERT OR IGNORE INTO categories (name) VALUES (?)", (category,))
                    if location:
                        conn.execute("INSERT OR IGNORE INTO locations (name) VALUES (?)", (location,))
                    imported += 1
                except sqlite3.IntegrityError:
                    skipped += 1
            conn.commit()

        log_action(session.get("username"), "CSV_IMPORT", f"Imported {imported} assets, skipped {skipped}.")
        flash(f"Asset Import Complete: {imported} imported successfully, {skipped} skipped.", "info")
    except Exception as e:
        flash(f"Import failed: {str(e)}", "danger")

    return redirect(url_for("index", tab="assets"))


# ================= EXPORT FILTERED INVENTORY =================

@app.route("/assets/export")
@login_required
def export_assets_csv():
    """Exports current filtered assets as a clean CSV download."""
    search_query = request.args.get("q", "").strip()
    category_filter = request.args.get("category", "").strip()
    status_filter = request.args.get("status", "").strip()
    location_filter = request.args.get("location", "").strip()

    sql = "SELECT * FROM assets WHERE 1=1"
    params = []
    if search_query:
        sql += """ AND (asset_tag LIKE ? OR name LIKE ? OR serial_number LIKE ? 
                        OR assigned_to LIKE ? OR location LIKE ? OR supplier LIKE ? OR item_type LIKE ?)"""
        wild = f"%{search_query}%"
        params.extend([wild, wild, wild, wild, wild, wild, wild])
    if category_filter:
        sql += " AND category = ?"
        params.append(category_filter)
    if status_filter:
        sql += " AND status = ?"
        params.append(status_filter)
    if location_filter:
        sql += " AND location = ?"
        params.append(location_filter)
    sql += " ORDER BY id DESC"

    with get_db() as conn:
        rows = conn.execute(sql, params).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Asset Tag", "Item Name", "Type of Item", "Category", "Serial / Product ID",
        "Purchase Date", "Purchase Cost", "Current Book Value", "Supplier", "Warranty Expiry",
        "Warranty Status", "Location", "Assigned Employee", "Status", "Salvage Value", "Useful Life (Years)"
    ])

    for r in rows:
        r_dict = dict(r)
        fin = calculate_financials(r_dict)
        writer.writerow([
            r["asset_tag"], r["name"], r["item_type"] or "", r["category"], r["serial_number"] or "",
            r["purchase_date"] or "", r["purchase_cost"], fin["current_book_value"], r["supplier"] or "",
            r["warranty_expiry"] or "", fin["warranty_status"], r["location"] or "", r["assigned_to"] or "",
            r["status"], r["salvage_value"], r["useful_life_years"]
        ])

    log_action(session.get("username"), "EXPORT_CSV", f"Exported {len(rows)} asset records.")
    return Response(
        "\uFEFF" + output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=AssetInventory_{date.today().strftime('%Y%m%d')}.csv"}
    )


# ================= DATABASE BACKUP (ONE-CLICK) =================

@app.route("/api/backup-database", methods=["POST"])
@login_required
@roles_required("Administrator")
def backup_database():
    """Copies assets.db with timestamp to Downloads folder."""
    downloads_dir = os.path.join(os.path.expanduser("~"), "Downloads")
    os.makedirs(downloads_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"assets_backup_{timestamp}.db"
    destination = os.path.join(downloads_dir, backup_filename)

    try:
        shutil.copy2(DB_PATH, destination)
        log_action(session.get("username"), "BACKUP_DB", f"Created database backup: {backup_filename}")
        return jsonify({
            "status": "success",
            "message": f"Database backup saved: {backup_filename}",
            "path": destination
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ================= USER MANAGEMENT & BATCH INGESTION =================

@app.route("/users/create", methods=["POST"])
@login_required
@roles_required("Administrator")
def create_user():
    username = request.form.get("username", "").strip()
    full_name = request.form.get("full_name", "").strip()
    password = request.form.get("password", "")
    role = request.form.get("role", "Viewer")

    valid_roles = {"Administrator", "Data_Entry", "Auditor", "Viewer"}
    if role not in valid_roles:
        role = "Viewer"

    if not username or len(password) < 6:
        flash("Username is required and password must be at least 6 characters.", "danger")
        return redirect(url_for("index", tab="settings"))

    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO users (username, password_hash, full_name, role) VALUES (?, ?, ?, ?)",
                (username, generate_password_hash(password), full_name, role)
            )
            conn.commit()
        log_action(session.get("username"), "CREATE_USER", f"Created user '{username}' ({role})")
        flash(f"User '{username}' ({role}) created successfully.", "success")
    except sqlite3.IntegrityError:
        flash(f"Error: Username '{username}' already exists.", "danger")

    return redirect(url_for("index", tab="settings"))


@app.route("/users/upload", methods=["POST"])
@login_required
@roles_required("Administrator")
def upload_users_csv():
    if "file" not in request.files:
        flash("No user file uploaded.", "danger")
        return redirect(url_for("index", tab="settings"))

    file = request.files["file"]
    if not file or not file.filename.lower().endswith(".csv"):
        flash("Please upload a standard .csv spreadsheet.", "danger")
        return redirect(url_for("index", tab="settings"))

    try:
        content = file.stream.read().decode("utf-8-sig", errors="replace")
        stream = io.StringIO(content)
        reader = csv.DictReader(stream)

        if not reader.fieldnames:
            flash("The uploaded user CSV is empty.", "danger")
            return redirect(url_for("index", tab="settings"))

        normalized = {h.strip().lower(): h for h in reader.fieldnames}
        user_col = normalized.get("username")
        pwd_col = normalized.get("password")
        role_col = normalized.get("role")
        name_col = normalized.get("full_name") or normalized.get("full name") or normalized.get("name")

        if not user_col or not pwd_col:
            flash("User CSV requires 'Username' and 'Password' columns.", "danger")
            return redirect(url_for("index", tab="settings"))

        imported = 0
        skipped = 0
        role_map = {
            "administrator": "Administrator",
            "admin": "Administrator",
            "data_entry": "Data_Entry",
            "data entry": "Data_Entry",
            "auditor": "Auditor",
            "viewer": "Viewer"
        }

        with get_db() as conn:
            for row in reader:
                username = sanitize_csv_field(row.get(user_col, ""))
                password = str(row.get(pwd_col, "")).strip()
                full_name = sanitize_csv_field(row.get(name_col, "")) if name_col else username
                raw_role = (row.get(role_col, "") or "Viewer").strip().lower()
                role = role_map.get(raw_role, "Viewer")

                if not username or len(password) < 6:
                    skipped += 1
                    continue

                try:
                    conn.execute(
                        "INSERT INTO users (username, password_hash, full_name, role) VALUES (?, ?, ?, ?)",
                        (username, generate_password_hash(password), full_name, role)
                    )
                    imported += 1
                except sqlite3.IntegrityError:
                    skipped += 1
            conn.commit()

        log_action(session.get("username"), "USER_IMPORT", f"Imported {imported} user accounts, skipped {skipped}.")
        flash(f"User Accounts Ingested: {imported} created, {skipped} skipped.", "info")
    except Exception as e:
        flash(f"User import error: {str(e)}", "danger")

    return redirect(url_for("index", tab="settings"))


@app.route("/settings/password", methods=["POST"])
@login_required
def change_password():
    current_pwd = request.form.get("current_password", "")
    new_pwd = request.form.get("new_password", "")

    if len(new_pwd) < 6:
        flash("New password must be at least 6 characters.", "danger")
        return redirect(url_for("index", tab="settings"))

    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchone()
        if user and check_password_hash(user["password_hash"], current_pwd):
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (generate_password_hash(new_pwd), session["user_id"])
            )
            conn.commit()
            log_action(session.get("username"), "PWD_CHANGE", "Updated account password.")
            flash("Password updated successfully.", "success")
        else:
            flash("Incorrect current password.", "danger")

    return redirect(url_for("index", tab="settings"))


# ================= FAIL-SAFE CSV TEMPLATE GENERATOR =================

ASSETS_CSV_CONTENT = (
    "Item Name,Type of Item,Category,Serial / Product ID,Purchase Date,Purchase Cost,"
    "Supplier,Warranty Expiry,Location,Assigned Employee,Status,Salvage Value,"
    "Useful Life (Years),Depreciation Method\n"
    "HP Core i7 16GB 512 SSD,Laptop,IT equipment,5CD1234ABC,2026-01-15,850000,"
    "Tech Supplies Ltd,2029-01-15,Yenagoa HQ,Jane Doe,In use,50000,5,straight-line\n"
)

USERS_CSV_CONTENT = (
    "Username,Password,Role\n"
    "jdoe,ChangeMe123,Viewer\n"
    "rsmith,StaffPass#2026,Data_Entry\n"
)


@app.route("/api/save-native-template", methods=["POST"])
@login_required
def save_native_template():
    target = request.json.get("type") if request.is_json else request.form.get("type")
    downloads_dir = os.path.join(os.path.expanduser("~"), "Downloads")
    os.makedirs(downloads_dir, exist_ok=True)

    if target == "users":
        filename = "users_template.csv"
        content = USERS_CSV_CONTENT
    else:
        filename = "assets_template.csv"
        content = ASSETS_CSV_CONTENT

    filepath = os.path.join(downloads_dir, filename)
    try:
        with open(filepath, "w", encoding="utf-8-sig") as f:
            f.write(content)
        return jsonify({
            "status": "success",
            "message": f"Saved '{filename}' to your Downloads folder!",
            "path": filepath
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/download/assets-template")
@login_required
def download_assets_template():
    return Response(
        ASSETS_CSV_CONTENT,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=assets_template.csv"}
    )


@app.route("/download/users-template")
@login_required
@roles_required("Administrator")
def download_users_template():
    return Response(
        USERS_CSV_CONTENT,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=users_template.csv"}
    )


# ================= DESKTOP RUNNER =================

def run_server():
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)


if __name__ == "__main__":
    init_db()

    is_desktop = "--server-only" not in sys.argv

    if is_desktop:
        try:
            import webview
            threading.Thread(target=run_server, daemon=True).start()
            webview.create_window(
                "Enterprise Asset Management",
                "http://127.0.0.1:5000",
                width=1380,
                height=900,
                resizable=True,
            )
            webview.start()
        except ImportError:
            print("[INFO] pywebview not installed. Running in browser mode at http://127.0.0.1:5000")
            app.run(host="127.0.0.1", port=5000, debug=True)
    else:
        app.run(host="0.0.0.0", port=5000)