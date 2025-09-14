import os
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from textblob import TextBlob
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import mysql.connector
import io
from math import ceil
import csv
from flask import Response
from fpdf import FPDF
import json
from flask import jsonify
load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', '33e08e03e0378c72f3785b7fe6012eb548a1cb610fe7dcf7')

db = mysql.connector.connect(
    host=os.environ.get('DB_HOST', 'localhost'),
    user=os.environ.get('DB_USER'),
    password=os.environ.get('DB_PASSWORD'),
    database=os.environ.get('DB_NAME')
)
cursor = db.cursor(dictionary=True)

login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.init_app(app)

class User(UserMixin):
    def __init__(self, id_, username, password):
        self.id = id_
        self.username = username
        self.password = password

@login_manager.user_loader
def load_user(user_id):
    cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
    user = cursor.fetchone()
    if user:
        return User(user['id'], user['username'], user['password'])
    return None

def analyze_sentiment(text):
    blob = TextBlob(text)
    polarity = blob.sentiment.polarity
    if polarity > 0.15:
        return "Positive", "😊"
    elif polarity < -0.15:
        return "Negative", "😔"
    else:
        return "Neutral", "😐"

# --------- Tag helpers ---------
def get_or_create_tag(tag_name):
    tag_name = tag_name.strip().lower()
    cursor.execute("SELECT id FROM tags WHERE name = %s", (tag_name,))
    tag = cursor.fetchone()
    if tag:
        return tag['id']
    cursor.execute("INSERT INTO tags (name) VALUES (%s)", (tag_name,))
    db.commit()
    return cursor.lastrowid

def get_entry_tags(entry_id):
    cursor.execute("""
        SELECT t.name FROM tags t
        JOIN entry_tags et ON t.id = et.tag_id
        WHERE et.entry_id = %s
    """, (entry_id,))
    return [r['name'] for r in cursor.fetchall()]

# --------- Auth routes ---------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if not username or not password:
            flash("Please fill out both fields.", "warning")
            return redirect(url_for('register'))
        cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
        if cursor.fetchone():
            flash("Username already exists.", "danger")
            return redirect(url_for('register'))
        hashed_password = generate_password_hash(password)
        cursor.execute("INSERT INTO users (username, password) VALUES (%s, %s)", (username, hashed_password))
        db.commit()
        flash("Registration successful. Please login.", "success")
        return redirect(url_for('login'))
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
        user = cursor.fetchone()
        if user and check_password_hash(user['password'], password):
            user_obj = User(user['id'], user['username'], user['password'])
            login_user(user_obj)
            flash("Logged in successfully.", "success")
            return redirect(url_for("home"))
        else:
            flash("Invalid credentials.", "danger")
            return redirect(url_for("login"))
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))

# --------- HOME with Tag Submission ---------
@app.route("/", methods=["GET", "POST"])
@login_required
def home():
    if request.method == "POST":
        entry_text = request.form.get('entry', '').strip()
        tags_str = request.form.get('tags', '').strip()
        sentiment, emoji = analyze_sentiment(entry_text)
        cursor.execute(
            "INSERT INTO entries (entry, sentiment, user_id) VALUES (%s, %s, %s)",
            (entry_text, sentiment, current_user.id)
        )
        entry_id = cursor.lastrowid
        db.commit()
        tags = [t.strip() for t in tags_str.split(',') if t.strip()]
        for tag_name in tags:
            tag_id = get_or_create_tag(tag_name)
            cursor.execute(
                "INSERT IGNORE INTO entry_tags (entry_id, tag_id) VALUES (%s, %s)",
                (entry_id, tag_id)
            )
        db.commit()
        return render_template("home.html", sentiment=sentiment, emoji=emoji, entry=entry_text, tags=tags_str)
    return render_template("home.html")

# --------- Entries with Tag Display ---------
@app.route("/entries")
@login_required
def entries():
    page = request.args.get('page', 1, type=int)
    keyword = request.args.get('keyword', '', type=str)
    sentiment = request.args.get('sentiment', '', type=str)
    date_from = request.args.get('date_from', '', type=str)
    date_to = request.args.get('date_to', '', type=str)

    filters = []
    params = [current_user.id]

    count_query = "SELECT COUNT(*) as count FROM entries WHERE user_id = %s"
    filter_query = ""

    if keyword:
        filters.append("entry LIKE %s")
        params.append(f"%{keyword}%")
    if sentiment and sentiment in ["Positive", "Neutral", "Negative"]:
        filters.append("sentiment = %s")
        params.append(sentiment)
    if date_from:
        filters.append("DATE(date) >= %s")
        params.append(date_from)
    if date_to:
        filters.append("DATE(date) <= %s")
        params.append(date_to)
    if filters:
        filter_query = " AND " + " AND ".join(filters)

    count_query += filter_query
    cursor.execute(count_query, tuple(params))
    total_entries = cursor.fetchone()['count']

    per_page = 10
    total_pages = ceil(total_entries / per_page)
    offset = (page - 1) * per_page

    select_query = f"""
        SELECT * FROM entries
        WHERE user_id = %s {filter_query}
        ORDER BY date DESC
        LIMIT %s OFFSET %s
    """
    final_params = params + [per_page, offset]
    cursor.execute(select_query, tuple(final_params))
    entries_data = cursor.fetchall()

    # Attach tags to each entry
    for row in entries_data:
        row['tags'] = get_entry_tags(row['id'])

    return render_template("entries.html",
                           entries=entries_data,
                           page=page,
                           total_pages=total_pages,
                           keyword=keyword,
                           sentiment=sentiment,
                           date_from=date_from,
                           date_to=date_to
    )
@app.route('/export/csv')
@login_required
def export_csv():
    cursor.execute("SELECT date, entry, sentiment FROM entries WHERE user_id = %s ORDER BY date DESC", (current_user.id,))
    entries = cursor.fetchall()

    def generate():
        data = io.StringIO()
        writer = csv.writer(data)
        writer.writerow(('Date', 'Entry', 'Sentiment'))
        yield data.getvalue()
        data.seek(0)
        data.truncate(0)

        for row in entries:
            writer.writerow([row['date'].strftime('%Y-%m-%d %H:%M'), row['entry'], row['sentiment']])
            yield data.getvalue()
            data.seek(0)
            data.truncate(0)

    headers = {
        'Content-Disposition': 'attachment; filename=journal_export.csv',
        'Content-Type': 'text/csv',
    }
    return Response(generate(), headers=headers)

@app.route('/export/pdf')
@login_required
def export_pdf():
    cursor.execute("SELECT date, entry, sentiment FROM entries WHERE user_id = %s ORDER BY date DESC", (current_user.id,))
    entries = cursor.fetchall()

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)
    pdf.cell(0, 10, "Sentiment Journal Entries", ln=True, align='C')
    pdf.ln(10)

    # Table headers
    pdf.set_font("Arial", 'B', 11)
    pdf.cell(40, 10, "Date", border=1)
    pdf.cell(100, 10, "Entry", border=1)
    pdf.cell(40, 10, "Sentiment", border=1)
    pdf.ln()

    pdf.set_font("Arial", size=10)
    for row in entries:
        date_str = row['date'].strftime('%Y-%m-%d %H:%M')
        entry_text = row['entry']
        # Truncate long entries for PDF to avoid overflow
        entry_pdf = entry_text if len(entry_text) <= 80 else entry_text[:80] + "..."

        pdf.cell(40, 10, date_str, border=1)
        pdf.cell(100, 10, entry_pdf, border=1)
        pdf.cell(40, 10, row['sentiment'], border=1)
        pdf.ln()

    pdf_output = pdf.output(dest='S').encode('latin1')  # string output

    return Response(pdf_output, mimetype='application/pdf',
                    headers={"Content-Disposition": "attachment;filename=journal_export.pdf"})
@app.route('/dashboard')
@login_required
def dashboard():
    cursor.execute("""
        SELECT DATE(date) as day, sentiment, COUNT(*) as count
        FROM entries
        WHERE user_id = %s
        GROUP BY day, sentiment
        ORDER BY day ASC
    """, (current_user.id,))
    results = cursor.fetchall()

    # Organize data for frontend chart
    days = sorted(list({row['day'].strftime('%Y-%m-%d') for row in results}))
    categories = ["Positive", "Neutral", "Negative"]
    trend_data = {cat: [0]*len(days) for cat in categories}
    day_index = {day: i for i, day in enumerate(days)}

    for row in results:
        sentiment = row["sentiment"]
        day = row["day"].strftime("%Y-%m-%d")
        trend_data[sentiment][day_index[day]] = row["count"]

    return render_template(
        'dashboard.html',
        days=days,
        trend_data=json.dumps(trend_data)
    )
@app.route("/trend")
@login_required
def trend():
    cursor.execute("""
        SELECT DATE(date) as day, sentiment, COUNT(*) as count
        FROM entries
        WHERE user_id = %s AND date > NOW() - INTERVAL 7 DAY
        GROUP BY day, sentiment
        ORDER BY day ASC
    """, (current_user.id,))
    results = cursor.fetchall()
    days = sorted(list({row['day'].strftime("%Y-%m-%d") for row in results}))
    categories = ["Positive", "Neutral", "Negative"]
    trend_data = {cat: [0] * len(days) for cat in categories}
    day_index = {day: i for i, day in enumerate(days)}
    for row in results:
        sentiment = row["sentiment"]
        day = row["day"].strftime("%Y-%m-%d")
        trend_data[sentiment][day_index[day]] = row["count"]

    fig, ax = plt.subplots()
    for cat in categories:
        ax.plot(days, trend_data[cat], marker='o', label=cat)
    ax.set_title("Mood Trend (Past 7 Days)")
    ax.set_xlabel("Date")
    ax.set_ylabel("Entries")
    ax.legend()
    plt.xticks(rotation=30)
    img = io.BytesIO()
    plt.tight_layout()
    plt.savefig(img, format="png")
    img.seek(0)
    plt.close(fig)
    return send_file(img, mimetype="image/png")

if __name__ == "__main__":
    app.run(debug=True)
