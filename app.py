import os
import logging
import html
import time
import traceback
from datetime import datetime
from flask import Flask, render_template, request, jsonify
from flask_mysqldb import MySQL
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect

# ===== APP INIT =====
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret')

# ✅ FIX 1: CSRF + AJAX compatibility
app.config['WTF_CSRF_HEADERS'] = ['X-CSRFToken']

# ===== CSRF =====
csrf = CSRFProtect(app)

# ===== ENV =====
def get_env(var, default=None):
    value = os.environ.get(var, default)
    if value is None:
        raise RuntimeError(f"Missing environment variable: {var}")
    return value

ENV = os.environ.get("ENV", "DEV")

# ===== MYSQL CONFIG =====
app.config['MYSQL_HOST'] = get_env('MYSQL_HOST', 'localhost')
app.config['MYSQL_USER'] = get_env('MYSQL_USER', 'root')
app.config['MYSQL_PASSWORD'] = get_env('MYSQL_PASSWORD', 'root')
app.config['MYSQL_DB'] = get_env('MYSQL_DB', 'test')
app.config['MYSQL_CURSORCLASS'] = 'DictCursor'

mysql = MySQL(app)

# ===== RATE LIMIT =====
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["100/hour"]
)

# ===== LOGGING =====
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("app.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ===== DB WAIT (CRITICAL FIX) =====
def wait_for_db(retries=10, delay=3):
    for i in range(retries):
        try:
            cur = mysql.connection.cursor()
            cur.execute("SELECT 1")
            cur.close()
            logger.info("DB is ready")
            return True
        except Exception:
            logger.warning(f"DB not ready, retry {i+1}/{retries}")
            time.sleep(delay)
    return False

# ===== INIT DB =====
def init_db():
    try:
        cur = mysql.connection.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INT AUTO_INCREMENT PRIMARY KEY,
                message TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX(created_at)
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS metrics (
                id INT PRIMARY KEY,
                hits INT DEFAULT 0,
                clicks INT DEFAULT 0
            )
        """)

        cur.execute("""
            INSERT INTO metrics (id, hits, clicks)
            VALUES (1, 0, 0)
            ON DUPLICATE KEY UPDATE id = id
        """)

        mysql.connection.commit()
        cur.close()

        logger.info("Database initialized with metrics")

    except Exception:
        logger.error("DB INIT FAILED")
        logger.error(traceback.format_exc())

# ✅ FIX 2: SAFE INIT WITH RETRY
with app.app_context():
    if wait_for_db():
        init_db()
    else:
        logger.error("DB never became ready")

# ===== SAFE CURSOR =====
def get_cursor():
    try:
        mysql.connection.ping(True)
        return mysql.connection.cursor()
    except Exception:
        mysql.connection = mysql.connect()
        return mysql.connection.cursor()

# ===== HOME =====
@app.route('/')
def home():
    try:
        cur = get_cursor()

        cur.execute("UPDATE metrics SET hits = hits + 1 WHERE id = 1")
        mysql.connection.commit()

        cur.execute("SELECT hits, clicks FROM metrics WHERE id = 1")
        metrics = cur.fetchone()

        cur.execute("""
            SELECT message, created_at
            FROM messages
            ORDER BY id DESC
            LIMIT 50
        """)
        messages = cur.fetchall()

        cur.close()

        db_status = "healthy"
        hits = metrics['hits']
        clicks = metrics['clicks']

    except Exception:
        logger.error(traceback.format_exc())
        messages = []
        db_status = "down"
        hits = 0
        clicks = 0

    return render_template(
        'index.html',
        messages=messages,
        db_status=db_status,
        env=ENV,
        hits=hits,
        clicks=clicks
    )

# ===== SUBMIT =====
@app.route('/submit', methods=['POST'])
@limiter.limit("10/minute")
def submit():
    msg = request.form.get('new_message', '').strip()

    if not msg:
        return jsonify({'status': 'error', 'message': 'Empty'}), 400

    if len(msg) > 500:
        return jsonify({'status': 'error', 'message': 'Too long'}), 400

    msg = html.escape(msg)

    try:
        cur = get_cursor()
        cur.execute("INSERT INTO messages (message) VALUES (%s)", [msg])
        mysql.connection.commit()
        cur.close()

        logger.info(f"Message stored: {msg}")

        return jsonify({
            'status': 'success',
            'message': msg,
            'time': datetime.now().strftime('%H:%M')
        })

    except Exception:
        logger.error(traceback.format_exc())
        return jsonify({'status': 'error'}), 500

# ===== TRACK CLICKS =====
@app.route('/track_click', methods=['POST'])
def track_click():
    try:
        cur = get_cursor()
        cur.execute("UPDATE metrics SET clicks = clicks + 1 WHERE id = 1")
        mysql.connection.commit()
        cur.close()

        return jsonify({"status": "ok"})

    except Exception:
        logger.error(traceback.format_exc())
        return jsonify({"status": "error"}), 500

# ===== METRICS API =====
@app.route('/metrics')
def metrics_api():
    try:
        cur = get_cursor()
        cur.execute("SELECT hits, clicks FROM metrics WHERE id = 1")
        data = cur.fetchone()
        cur.close()

        return jsonify({
            "hits": data['hits'],
            "clicks": data['clicks']
        })

    except Exception:
        logger.error(traceback.format_exc())
        return jsonify({"hits": 0, "clicks": 0}), 500

# ===== HEALTH =====
@app.route('/health')
def health():
    start = time.time()

    try:
        cur = get_cursor()
        cur.execute("SELECT COUNT(*) as count FROM messages")
        cur.fetchone()
        cur.close()

        latency = round((time.time() - start) * 1000, 2)

        return jsonify({
            "status": "healthy",
            "service": "app+db",
            "latency_ms": latency
        })

    except Exception:
        logger.error(traceback.format_exc())

        return jsonify({
            "status": "down",
            "service": "db"
        }), 500

# ===== LOGS =====
@app.route('/logs')
def get_logs():
    try:
        with open("app.log", "r") as f:
            logs = f.readlines()[-100:]
        return jsonify({"logs": logs})

    except Exception:
        logger.error(traceback.format_exc())
        return jsonify({"logs": ["Error loading logs"]})

# ===== RUN =====
if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5000, debug=(ENV == "DEV"))
