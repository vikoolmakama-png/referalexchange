from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin, login_user, login_required,
    logout_user, current_user
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, parse_qs
import secrets
import re

app = Flask(__name__)

CHAT_UPLOAD_FOLDER = os.path.join(app.root_path, "static", "chat_uploads")
os.makedirs(CHAT_UPLOAD_FOLDER, exist_ok=True)

ALLOWED_CHAT_IMAGES = {"jpg", "jpeg", "png", "webp", "gif"}
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024


@app.after_request
def no_cache(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

app.config["SECRET_KEY"] = os.environ.get(
    "SECRET_KEY",
    "dev-secret-key"
)

database_url = os.environ.get("DATABASE_URL")

if database_url:
    if database_url.startswith("postgres://"):
        database_url = database_url.replace(
            "postgres://",
            "postgresql+psycopg://",
            1
        )
    elif database_url.startswith("postgresql://"):
        database_url = database_url.replace(
            "postgresql://",
            "postgresql+psycopg://",
            1
        )

    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
else:
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///referals.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

app.config["PAYMENT_PROOF_FOLDER"] = os.path.join(
    app.root_path,
    "static",
    "payment_proofs"
)

app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

os.makedirs(
    app.config["PAYMENT_PROOF_FOLDER"],
    exist_ok=True
)

db = SQLAlchemy(app)

login_manager = LoginManager(app)
login_manager.login_view = "login"



# -----------------------------
# DATABASE MODELS
# -----------------------------

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(160), unique=True, nullable=False)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

    referral_code = db.Column(db.String(30), unique=True, nullable=False)
    referred_by = db.Column(db.String(30), nullable=True)

    referral_balance = db.Column(db.Integer, default=10, nullable=False)

    is_admin = db.Column(db.Boolean, default=False)
    is_banned = db.Column(db.Boolean, default=False)

    last_seen = db.Column(db.DateTime, nullable=True)


class Exchange(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    user_one_id = db.Column(db.Integer, nullable=False)
    user_two_id = db.Column(db.Integer, nullable=False)

    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime, nullable=True)

    status = db.Column(
        db.String(20),
        default="active"
    )


class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    exchange_id = db.Column(db.Integer, nullable=False)
    sender_id = db.Column(db.Integer, nullable=False)

    body = db.Column(db.Text, nullable=False)
    image_filename = db.Column(db.String(255), nullable=True)

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )


class ReferralRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    requester_id = db.Column(db.Integer, nullable=False)
    recipient_id = db.Column(db.Integer, nullable=False)

    status = db.Column(db.String(20), default="pending")

    created_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )

    responded_at = db.Column(db.DateTime, nullable=True)


class MessageRead(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(db.Integer, nullable=False)
    exchange_id = db.Column(db.Integer, nullable=False)

    last_read_at = db.Column(db.DateTime, nullable=True)


class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(db.Integer, nullable=False)

    amount = db.Column(db.Integer, default=200, nullable=False)
    method = db.Column(db.String(30), default="OPay")

    account_number = db.Column(
        db.String(30),
        default="8131602092"
    )

    account_name = db.Column(
        db.String(160),
        default="Elisha Makama Kamina"
    )

    sender_name = db.Column(
        db.String(160),
        nullable=False,
        default=""
    )

    proof_filename = db.Column(
        db.String(255),
        nullable=True
    )

    reference = db.Column(
        db.String(120),
        nullable=False
    )

    status = db.Column(
        db.String(20),
        default="pending"
    )

    submitted_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )

    reviewed_at = db.Column(
        db.DateTime,
        nullable=True
    )

    access_started_at = db.Column(
        db.DateTime,
        nullable=True
    )

    access_expires_at = db.Column(
        db.DateTime,
        nullable=True
    )

    admin_note = db.Column(
        db.Text,
        nullable=True
    )


class Report(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    reporter_id = db.Column(db.Integer, nullable=False)
    reported_user_id = db.Column(db.Integer, nullable=False)
    exchange_id = db.Column(db.Integer, nullable=True)

    reason = db.Column(db.String(120), nullable=False)
    details = db.Column(db.Text, nullable=True)

    status = db.Column(
        db.String(20),
        default="pending"
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


# -----------------------------
# HELPERS
# -----------------------------

def generate_referral_code():
    while True:
        code = "REF-" + secrets.token_hex(4).upper()
        if not User.query.filter_by(referral_code=code).first():
            return code


def touch_user():
    if current_user.is_authenticated:
        current_user.last_seen = datetime.now(timezone.utc).replace(tzinfo=None)
        db.session.commit()


def is_user_online(user):
    if user.is_banned or not user.last_seen:
        return False

    return user.last_seen >= datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=40)


def has_active_paid_access(user_id):
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    payment = Payment.query.filter(
        Payment.user_id == user_id,
        Payment.status == "approved",
        Payment.access_started_at.isnot(None),
        Payment.access_expires_at > now
    ).order_by(
        Payment.access_expires_at.desc()
    ).first()

    return payment is not None


def get_active_paid_payment(user_id):
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    return Payment.query.filter(
        Payment.user_id == user_id,
        Payment.status == "approved",
        Payment.access_started_at.isnot(None),
        Payment.access_expires_at > now
    ).order_by(
        Payment.access_expires_at.desc()
    ).first()


def get_active_exchange(user_a, user_b):
    return Exchange.query.filter(
        Exchange.status == "active",
        (
            (
                (Exchange.user_one_id == user_a) &
                (Exchange.user_two_id == user_b)
            )
            |
            (
                (Exchange.user_one_id == user_b) &
                (Exchange.user_two_id == user_a)
            )
        )
    ).first()


def get_referral_code_from_input(value):
    if not value:
        return None

    value = value.strip()

    # Direct referral code
    if value.upper().startswith("REF-"):
        return value.upper()

    # Full referral URL
    try:
        parsed = urlparse(value)
        params = parse_qs(parsed.query)

        if "ref" in params and params["ref"]:
            return params["ref"][0].upper()
    except Exception:
        pass

    return value.upper()


# -----------------------------
# PUBLIC PAGES
# -----------------------------

@app.route("/")
def home():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():

    referral_from_link = request.args.get("ref", "").upper()

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()

        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        referral_input = request.form.get("referral_code", "").strip()

        # If no form referral code, use ?ref=
        if not referral_input:
            referral_input = referral_from_link

        referral_code = get_referral_code_from_input(referral_input)

        if not full_name:
            flash("Please enter your full name.", "error")
            return render_template(
                "register.html",
                referral_code=referral_input
            )

        if not username or len(username) < 3:
            flash("Username must contain at least 3 characters.", "error")
            return render_template(
                "register.html",
                referral_code=referral_input
            )

        if not re.match(r"^[A-Za-z0-9_]+$", username):
            flash(
                "Username can only contain letters, numbers and underscores.",
                "error"
            )
            return render_template(
                "register.html",
                referral_code=referral_input
            )

        if not email.endswith("@gmail.com"):
            flash("Please use a valid Gmail address.", "error")
            return render_template(
                "register.html",
                referral_code=referral_input
            )

        if len(password) < 6:
            flash("Password must contain at least 6 characters.", "error")
            return render_template(
                "register.html",
                referral_code=referral_input
            )

        if password != confirm_password:
            flash("Passwords do not match.", "error")
            return render_template(
                "register.html",
                referral_code=referral_input
            )

        if User.query.filter_by(username=username).first():
            flash("That username is already taken.", "error")
            return render_template(
                "register.html",
                referral_code=referral_input
            )

        if User.query.filter_by(email=email).first():
            flash("That Gmail address is already registered.", "error")
            return render_template(
                "register.html",
                referral_code=referral_input
            )

        referred_user = None

        if referral_code:
            referred_user = User.query.filter_by(
                referral_code=referral_code
            ).first()

            if not referred_user:
                flash("That referral link or code is not valid.", "error")
                return render_template(
                    "register.html",
                    referral_code=referral_input
                )

        user = User(
            full_name=full_name,
            username=username,
            email=email,
            password_hash=generate_password_hash(password),
            referral_code=generate_referral_code(),
            referred_by=referral_code if referred_user else None,
            referral_balance=10,
            last_seen=datetime.now(timezone.utc).replace(tzinfo=None)
        )

        db.session.add(user)

        # Reward the person who invited this new member.
        if referred_user:
            referred_user.referral_balance += 4

        db.session.commit()

        login_user(user)

        flash("Welcome to Referals Exchange!", "success")
        return redirect(url_for("dashboard"))

    return render_template(
        "register.html",
        referral_code=referral_from_link
    )


@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        user = User.query.filter_by(username=username).first()

        if not user or not check_password_hash(
            user.password_hash,
            password
        ):
            flash("Invalid username or password.", "error")
            return render_template("login.html")

        if user.is_banned:
            flash("This account has been banned.", "error")
            return render_template("login.html")

        login_user(user)

        user.last_seen = datetime.now(timezone.utc).replace(tzinfo=None)
        db.session.commit()

        return redirect(url_for("admin")) if user.is_admin else redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("home"))


# -----------------------------
# DASHBOARD
# -----------------------------

@app.route("/dashboard")
@login_required
def dashboard():

    touch_user()

    active_cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=40)

    active_users = User.query.filter(
        User.id != current_user.id,
        User.is_banned == False,
        User.last_seen >= active_cutoff
    ).order_by(
        User.last_seen.desc()
    ).all()

    exchanges = Exchange.query.filter(
        Exchange.status == "active",
        (
            (Exchange.user_one_id == current_user.id) |
            (Exchange.user_two_id == current_user.id)
        )
    ).order_by(
        Exchange.started_at.desc()
    ).all()

    conversations = []

    for exchange in exchanges:

        other_id = (
            exchange.user_two_id
            if exchange.user_one_id == current_user.id
            else exchange.user_one_id
        )

        other_user = db.session.get(User, other_id)

        if not other_user or other_user.is_banned:
            continue

        last_message = Message.query.filter_by(
            exchange_id=exchange.id
        ).order_by(
            Message.created_at.desc()
        ).first()

        conversations.append({
            "exchange": exchange,
            "user": other_user,
            "last_message": last_message
        })

    completed_exchanges = Exchange.query.filter(
        Exchange.status == "completed",
        (
            (Exchange.user_one_id == current_user.id) |
            (Exchange.user_two_id == current_user.id)
        )
    ).count()

    pending_requests_count = ReferralRequest.query.filter_by(
        recipient_id=current_user.id,
        status="pending"
    ).count()

    unread_messages_count = 0

    for exchange in exchanges:

        read_state = MessageRead.query.filter_by(
            user_id=current_user.id,
            exchange_id=exchange.id
        ).first()

        query = Message.query.filter(
            Message.exchange_id == exchange.id,
            Message.sender_id != current_user.id
        )

        if read_state and read_state.last_read_at:
            query = query.filter(
                Message.created_at > read_state.last_read_at
            )

        unread_messages_count += query.count()

    return render_template(
        "dashboard.html",
        active_users=active_users,
        conversations=conversations,
        completed_exchanges=completed_exchanges,
        pending_requests_count=pending_requests_count,
        unread_messages_count=unread_messages_count
    )


@app.route("/messages")
@login_required
def messages():

    touch_user()

    exchanges = Exchange.query.filter(
        Exchange.status == "active",
        (
            (Exchange.user_one_id == current_user.id) |
            (Exchange.user_two_id == current_user.id)
        )
    ).order_by(
        Exchange.started_at.desc()
    ).all()

    conversations = []

    for exchange in exchanges:

        other_id = (
            exchange.user_two_id
            if exchange.user_one_id == current_user.id
            else exchange.user_one_id
        )

        other_user = db.session.get(User, other_id)

        if not other_user or other_user.is_banned:
            continue

        last_message = Message.query.filter_by(
            exchange_id=exchange.id
        ).order_by(
            Message.created_at.desc()
        ).first()

        conversations.append({
            "exchange": exchange,
            "user": other_user,
            "last_message": last_message
        })

    return render_template(
        "messages.html",
        conversations=conversations
    )


@app.route("/heartbeat", methods=["POST"])
@login_required
def heartbeat():

    current_user.last_seen = datetime.now(timezone.utc).replace(tzinfo=None)
    db.session.commit()

    return jsonify({
        "success": True,
        "online": True
    })


# -----------------------------
# START PRIVATE EXCHANGE
# -----------------------------

@app.route("/exchange/start/<int:user_id>", methods=["POST"])
@login_required
def start_exchange(user_id):

    touch_user()

    if user_id == current_user.id:
        return jsonify({
            "success": False,
            "message": "You cannot exchange referrals with yourself."
        }), 400

    other_user = db.session.get(User, user_id)

    if not other_user or other_user.is_banned:
        return jsonify({
            "success": False,
            "message": "That user is not available."
        }), 404

    existing = get_active_exchange(
        current_user.id,
        other_user.id
    )

    if existing:
        return jsonify({
            "success": True,
            "exchange_id": existing.id
        })

    # Check paid access BEFORE checking the free referral limit.
    paid_access = has_active_paid_access(current_user.id)

    # Paid users can start unlimited exchanges during their 3-day access.
    if paid_access:
        exchange = Exchange(
            user_one_id=current_user.id,
            user_two_id=other_user.id,
            started_at=datetime.now(timezone.utc).replace(tzinfo=None),
            status="active"
        )

        db.session.add(exchange)
        db.session.commit()

        return jsonify({
            "success": True,
            "exchange_id": exchange.id
        })

    # Free access: 10 referral exchanges.
    completed_count = Exchange.query.filter(
        Exchange.status == "completed",
        (
            (Exchange.user_one_id == current_user.id) |
            (Exchange.user_two_id == current_user.id)
        )
    ).count()

    if completed_count >= 20 or current_user.referral_balance <= 0:
        return jsonify({
            "success": False,
            "payment_required": True,
            "message": "Your free referral exchanges have finished. Please pay ₦200 for 3 days of access."
        }), 402

    # Consume one free referral.
    current_user.referral_balance -= 1

    exchange = Exchange(
        user_one_id=current_user.id,
        user_two_id=other_user.id,
        started_at=datetime.now(timezone.utc).replace(tzinfo=None),
        status="active"
    )

    db.session.add(exchange)
    db.session.commit()

    return jsonify({
        "success": True,
        "exchange_id": exchange.id
    })

# -----------------------------
# PRIVATE CHAT
# -----------------------------

@app.route("/exchange/<int:exchange_id>")
@login_required
def exchange(exchange_id):

    touch_user()

    exchange = db.session.get(Exchange, exchange_id)

    if not exchange:
        flash("Exchange not found.", "error")
        return redirect(url_for("dashboard"))

    if current_user.id not in [
        exchange.user_one_id,
        exchange.user_two_id
    ]:
        flash("You do not have access to this exchange.", "error")
        return redirect(url_for("dashboard"))

    if exchange.status != "active":
        flash("This exchange has already been completed.", "error")
        return redirect(url_for("dashboard"))

    other_id = (
        exchange.user_two_id
        if current_user.id == exchange.user_one_id
        else exchange.user_one_id
    )

    other_user = db.session.get(User, other_id)

    messages = Message.query.filter_by(
        exchange_id=exchange.id
    ).order_by(
        Message.created_at.asc()
    ).all()

    read_state = MessageRead.query.filter_by(
        user_id=current_user.id,
        exchange_id=exchange.id
    ).first()

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    if not read_state:
        read_state = MessageRead(
            user_id=current_user.id,
            exchange_id=exchange.id,
            last_read_at=now
        )
        db.session.add(read_state)
    else:
        read_state.last_read_at = now

    db.session.commit()

    elapsed = int(
        (datetime.now(timezone.utc).replace(tzinfo=None) - exchange.started_at).total_seconds()
    )

    remaining = max(0, 480 - elapsed)

    return render_template(
        "chat.html",
        exchange=exchange,
        other_user=other_user,
        messages=messages,
        remaining=remaining
    )


@app.route("/exchange/<int:exchange_id>/message", methods=["POST"])
@login_required
def send_message(exchange_id):

    exchange = db.session.get(Exchange, exchange_id)

    if not exchange or exchange.status != "active":
        return jsonify({
            "success": False,
            "message": "Exchange is no longer active."
        }), 400

    if current_user.id not in [
        exchange.user_one_id,
        exchange.user_two_id
    ]:
        return jsonify({
            "success": False,
            "message": "Unauthorized."
        }), 403

    body = request.form.get("body", "").strip()
    image = request.files.get("image")

    if len(body) > 2000:
        return jsonify({
            "success": False,
            "message": "Message is too long."
        }), 400

    image_filename = None

    if image and image.filename:

        original_name = secure_filename(image.filename)
        extension = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else ""

        if extension not in ALLOWED_CHAT_IMAGES:
            return jsonify({
                "success": False,
                "message": "Only JPG, JPEG, PNG, WEBP and GIF images are allowed."
            }), 400

        import secrets

        image_filename = (
            secrets.token_hex(12) + "." + extension
        )

        image.save(
            os.path.join(
                CHAT_UPLOAD_FOLDER,
                image_filename
            )
        )

    if not body and not image_filename:
        return jsonify({
            "success": False,
            "message": "Write a message or select a screenshot."
        }), 400

    message = Message(
        exchange_id=exchange.id,
        sender_id=current_user.id,
        body=body,
        image_filename=image_filename
    )

    db.session.add(message)
    db.session.commit()

    return jsonify({
        "success": True
    })

@app.route("/exchange/<int:exchange_id>/complete", methods=["POST"])
@login_required
def complete_exchange(exchange_id):

    exchange = db.session.get(Exchange, exchange_id)

    if not exchange or exchange.status != "active":
        return jsonify({
            "success": False,
            "message": "Exchange is no longer active."
        }), 400

    if current_user.id not in [
        exchange.user_one_id,
        exchange.user_two_id
    ]:
        return jsonify({
            "success": False,
            "message": "Unauthorized."
        }), 403

    elapsed = (
        datetime.now(timezone.utc).replace(tzinfo=None) - exchange.started_at
    ).total_seconds()

    if elapsed < 480:
        remaining = int(480 - elapsed)

        return jsonify({
            "success": False,
            "message": f"Please wait {remaining} more seconds."
        }), 400

    exchange.status = "completed"
    exchange.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)

    # Delete private conversation messages permanently.
    Message.query.filter_by(
        exchange_id=exchange.id
    ).delete()

    db.session.commit()

    return jsonify({
        "success": True
    })


# -----------------------------
# REPORT USER
# -----------------------------

@app.route("/exchange/<int:exchange_id>/report", methods=["POST"])
@login_required
def report_user(exchange_id):

    exchange = db.session.get(Exchange, exchange_id)

    if not exchange:
        return jsonify({
            "success": False,
            "message": "Exchange not found."
        }), 404

    if current_user.id not in [
        exchange.user_one_id,
        exchange.user_two_id
    ]:
        return jsonify({
            "success": False,
            "message": "Unauthorized."
        }), 403

    reported_user_id = (
        exchange.user_two_id
        if current_user.id == exchange.user_one_id
        else exchange.user_one_id
    )

    reason = request.form.get(
        "reason",
        "Other"
    ).strip()

    details = request.form.get(
        "details",
        ""
    ).strip()

    report = Report(
        reporter_id=current_user.id,
        reported_user_id=reported_user_id,
        exchange_id=exchange.id,
        reason=reason,
        details=details,
        status="pending"
    )

    db.session.add(report)
    db.session.commit()

    return jsonify({
        "success": True,
        "message": "Report submitted."
    })


# -----------------------------
# PAID ACCESS
# -----------------------------

@app.route("/payment", methods=["GET", "POST"])
@login_required
def payment():

    touch_user()

    active_payment = get_active_paid_payment(
        current_user.id
    )

    if request.method == "POST":

        sender_name = request.form.get(
            "sender_name",
            ""
        ).strip()

        reference = request.form.get(
            "reference",
            ""
        ).strip()

        proof = request.files.get("proof")

        if not sender_name:
            flash(
                "Please enter the sender name used for the OPay payment.",
                "error"
            )
            return redirect(url_for("payment"))

        if not reference:
            flash(
                "Please enter your OPay payment reference.",
                "error"
            )
            return redirect(url_for("payment"))

        if not proof or not proof.filename:
            flash(
                "Please upload your payment screenshot.",
                "error"
            )
            return redirect(url_for("payment"))

        allowed_extensions = {
            "jpg",
            "jpeg",
            "png",
            "webp"
        }

        extension = (
            proof.filename.rsplit(".", 1)[-1].lower()
            if "." in proof.filename
            else ""
        )

        if extension not in allowed_extensions:
            flash(
                "Screenshot must be JPG, JPEG, PNG or WEBP.",
                "error"
            )
            return redirect(url_for("payment"))

        existing = Payment.query.filter_by(
            user_id=current_user.id,
            reference=reference
        ).first()

        if existing:
            flash(
                "This payment reference has already been submitted.",
                "error"
            )
            return redirect(url_for("payment"))

        pending = Payment.query.filter_by(
            user_id=current_user.id,
            status="pending"
        ).first()

        if pending:
            flash(
                "You already have a payment waiting for admin approval.",
                "error"
            )
            return redirect(url_for("payment"))

        safe_name = secure_filename(
            proof.filename
        )

        unique_name = (
            str(current_user.id)
            + "_"
            + str(int(datetime.now().timestamp()))
            + "_"
            + safe_name
        )

        proof_path = os.path.join(
            app.config["PAYMENT_PROOF_FOLDER"],
            unique_name
        )

        proof.save(proof_path)

        payment_record = Payment(
            user_id=current_user.id,
            amount=200,
            method="OPay",
            sender_name=sender_name,
            proof_filename=unique_name,
            account_number="8131602092",
            account_name="Elisha Makama Kamina",
            reference=reference,
            status="pending"
        )

        db.session.add(payment_record)
        db.session.commit()

        flash(
            "Payment submitted. Please wait for admin approval.",
            "success"
        )

        return redirect(url_for("payment"))

    pending_payment = Payment.query.filter_by(
        user_id=current_user.id,
        status="pending"
    ).order_by(
        Payment.submitted_at.desc()
    ).first()

    return render_template(
        "payment.html",
        active_payment=active_payment,
        pending_payment=pending_payment
    )


# -----------------------------
# ADMIN PAYMENT REVIEW
# -----------------------------

@app.route("/admin/payments")
@login_required
def admin_payments():

    if not current_user.is_admin:
        return "Unauthorized", 403

    payments = Payment.query.order_by(
        Payment.submitted_at.desc()
    ).all()

    return render_template(
        "admin_payments.html",
        payments=payments
    )


@app.route(
    "/admin/payment/<int:payment_id>/approve",
    methods=["POST"]
)
@login_required
def approve_payment(payment_id):

    if not current_user.is_admin:
        return jsonify({
            "success": False,
            "message": "Unauthorized."
        }), 403

    payment_record = db.session.get(
        Payment,
        payment_id
    )

    if not payment_record:
        return jsonify({
            "success": False,
            "message": "Payment not found."
        }), 404

    if payment_record.status != "pending":
        return jsonify({
            "success": False,
            "message": "This payment has already been reviewed."
        }), 400

    now = datetime.now(
        timezone.utc
    ).replace(tzinfo=None)

    payment_record.status = "approved"

    # IMPORTANT:
    # The 3-day access starts NOW,
    # at the exact time admin approves it.
    payment_record.access_started_at = now
    payment_record.access_expires_at = (
        now + timedelta(days=3)
    )
    payment_record.reviewed_at = now

    db.session.commit()

    return jsonify({
        "success": True,
        "message": "Payment approved. 3-day access activated."
    })


@app.route(
    "/admin/payment/<int:payment_id>/reject",
    methods=["POST"]
)
@login_required
def reject_payment(payment_id):

    if not current_user.is_admin:
        return jsonify({
            "success": False,
            "message": "Unauthorized."
        }), 403

    payment_record = db.session.get(
        Payment,
        payment_id
    )

    if not payment_record:
        return jsonify({
            "success": False,
            "message": "Payment not found."
        }), 404

    if payment_record.status != "pending":
        return jsonify({
            "success": False,
            "message": "This payment has already been reviewed."
        }), 400

    payment_record.status = "rejected"

    payment_record.reviewed_at = datetime.now(
        timezone.utc
    ).replace(tzinfo=None)

    db.session.commit()

    return jsonify({
        "success": True,
        "message": "Payment rejected."
    })


# -----------------------------
# ADMIN
# -----------------------------

@app.route("/admin")
@login_required
def admin():

    if not current_user.is_admin:
        return "Unauthorized", 403

    users = User.query.order_by(
        User.id.desc()
    ).all()

    reports = Report.query.order_by(
        Report.created_at.desc()
    ).all()

    payments = Payment.query.order_by(
        Payment.submitted_at.desc()
    ).all()

    return render_template(
        "admin.html",
        users=users,
        reports=reports,
        payments=payments
    )



# -----------------------------
# ADMIN USER CONTROLS
# -----------------------------

@app.route("/admin/user/<int:user_id>/ban", methods=["POST"])
@login_required
def admin_ban_user(user_id):
    if not current_user.is_admin:
        return jsonify({"success": False, "message": "Unauthorized."}), 403

    user = db.session.get(User, user_id)

    if not user:
        return jsonify({"success": False, "message": "User not found."}), 404

    if user.id == current_user.id:
        return jsonify({"success": False, "message": "You cannot ban yourself."}), 400

    user.is_banned = True
    db.session.commit()

    return jsonify({
        "success": True,
        "message": f"{user.username} has been banned."
    })


@app.route("/admin/user/<int:user_id>/unban", methods=["POST"])
@login_required
def admin_unban_user(user_id):
    if not current_user.is_admin:
        return jsonify({"success": False, "message": "Unauthorized."}), 403

    user = db.session.get(User, user_id)

    if not user:
        return jsonify({"success": False, "message": "User not found."}), 404

    user.is_banned = False
    db.session.commit()

    return jsonify({
        "success": True,
        "message": f"{user.username} has been unbanned."
    })


# -----------------------------
# DATABASE
# -----------------------------

with app.app_context():
    db.create_all()

    admin = User.query.filter_by(username="admin").first()

    if not admin:
        admin = User(
            full_name="Administrator",
            email="admin@referalsexchange.com",
            username="admin",
            password_hash=generate_password_hash("Money@2020"),
            referral_code="ADMIN",
            referral_balance=0,
            is_admin=True,
            is_banned=False
        )
        db.session.add(admin)
        db.session.commit()


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False,
        use_reloader=False
    )
