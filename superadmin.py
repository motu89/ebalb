from datetime import datetime, timedelta
from functools import wraps

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_user, logout_user

from extensions import db
from models import AdminUser
from security import CREDENTIAL_LENGTH, format_wait_time, generate_random_credential, login_rate_limiter

superadmin_bp = Blueprint("superadmin", __name__)


def super_admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            flash("Super admin access is required.", "error")
            return redirect(url_for("superadmin.login"))
        if not current_user.is_super_admin:
            logout_user()
            flash("Use a super admin account for this area.", "error")
            return redirect(url_for("superadmin.login"))
        return view(*args, **kwargs)

    return wrapped


@superadmin_bp.route("/")
def index():
    if current_user.is_authenticated and current_user.is_super_admin:
        return redirect(url_for("superadmin.manage_users"))
    if current_user.is_authenticated:
        logout_user()
    return redirect(url_for("superadmin.login"))


@superadmin_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated and current_user.is_super_admin:
        return redirect(url_for("superadmin.manage_users"))
    if current_user.is_authenticated:
        logout_user()

    client_key = _client_key()
    wait_seconds = login_rate_limiter.get_wait_seconds("super_admin", client_key)

    if request.method == "POST":
        if wait_seconds:
            flash(f"Too many failed login attempts. Try again in {format_wait_time(wait_seconds)}.", "error")
            return render_template("superadmin_login.html", wait_seconds=wait_seconds)

        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = AdminUser.query.filter_by(username=username, role=AdminUser.ROLE_SUPER_ADMIN).first()

        if user and user.check_password(password):
            login_rate_limiter.clear("super_admin", client_key)
            login_user(user)
            return redirect(url_for("superadmin.manage_users"))

        wait_seconds = login_rate_limiter.record_failure("super_admin", client_key)
        if wait_seconds:
            flash(f"Too many failed login attempts. Try again in {format_wait_time(wait_seconds)}.", "error")
        else:
            flash("Incorrect super admin username or password.", "error")

    return render_template("superadmin_login.html", wait_seconds=wait_seconds)


@superadmin_bp.route("/logout")
def logout():
    logout_user()
    return redirect(url_for("superadmin.login"))


@superadmin_bp.route("/users", methods=["GET", "POST"])
@super_admin_required
def manage_users():
    generated_credentials = None

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        period_days = _read_period_days(request.form.get("period_days"))

        if period_days is None:
            flash("Use period days between 1 and 3650.", "error")
        else:
            if not username:
                username = generate_random_credential()

            if AdminUser.query.filter_by(username=username).first():
                flash("That username already exists.", "error")
            else:
                password = generate_random_credential()
                user = AdminUser(
                    username=username,
                    role=AdminUser.ROLE_ADMIN,
                    expires_at=datetime.utcnow() + timedelta(days=period_days),
                )
                user.set_password(password)
                db.session.add(user)
                db.session.commit()
                generated_credentials = {
                    "username": username,
                    "password": password,
                    "expires_at": user.expires_at,
                }
                flash("Admin account created. Copy the password now; it is shown only once.", "success")

    users = AdminUser.query.order_by(AdminUser.role.desc(), AdminUser.created_at.desc()).all()
    return render_template(
        "superadmin_users.html",
        users=users,
        generated_credentials=generated_credentials,
        credential_length=CREDENTIAL_LENGTH,
        now=datetime.utcnow(),
    )


@superadmin_bp.route("/users/<int:user_id>/reset-password", methods=["POST"])
@super_admin_required
def reset_user_password(user_id):
    user = AdminUser.query.get_or_404(user_id)
    if user.is_super_admin:
        flash("Use the CLI to rotate a super admin password.", "error")
        return redirect(url_for("superadmin.manage_users"))

    password = generate_random_credential()
    user.set_password(password)
    db.session.commit()
    users = AdminUser.query.order_by(AdminUser.role.desc(), AdminUser.created_at.desc()).all()
    flash("Admin password reset. Copy the new password now; it is shown only once.", "success")
    return render_template(
        "superadmin_users.html",
        users=users,
        generated_credentials={
            "username": user.username,
            "password": password,
            "expires_at": user.expires_at,
        },
        credential_length=CREDENTIAL_LENGTH,
        now=datetime.utcnow(),
    )


@superadmin_bp.route("/users/<int:user_id>/extend", methods=["POST"])
@super_admin_required
def extend_user(user_id):
    user = AdminUser.query.get_or_404(user_id)
    if user.is_super_admin:
        flash("Super admin accounts do not expire.", "error")
        return redirect(url_for("superadmin.manage_users"))

    period_days = _read_period_days(request.form.get("period_days"))
    if period_days is None:
        flash("Use period days between 1 and 3650.", "error")
        return redirect(url_for("superadmin.manage_users"))

    base = user.expires_at if user.expires_at and user.expires_at > datetime.utcnow() else datetime.utcnow()
    user.expires_at = base + timedelta(days=period_days)
    db.session.commit()
    flash(f"{user.username}'s access was extended.", "success")
    return redirect(url_for("superadmin.manage_users"))


@superadmin_bp.route("/users/<int:user_id>/expire", methods=["POST"])
@super_admin_required
def expire_user(user_id):
    user = AdminUser.query.get_or_404(user_id)
    if user.is_super_admin:
        flash("Super admin accounts cannot be expired from the web panel.", "error")
        return redirect(url_for("superadmin.manage_users"))

    user.expires_at = datetime.utcnow() - timedelta(seconds=1)
    db.session.commit()
    flash(f"{user.username}'s access is now expired.", "success")
    return redirect(url_for("superadmin.manage_users"))


def _read_period_days(raw_value):
    try:
        days = int(raw_value or "")
    except ValueError:
        return None
    if days < 1 or days > 3650:
        return None
    return days


def _client_key():
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.remote_addr or "unknown"
