from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import current_user, login_user, logout_user, login_required

from models import AdminUser
from security import format_wait_time, login_rate_limiter

auth_bp = Blueprint("auth", __name__)


@auth_bp.before_app_request
def enforce_admin_period():
    if current_user.is_authenticated and not current_user.has_valid_period():
        logout_user()
        flash("Your admin account period has expired.", "error")
        return redirect(url_for("auth.login"))


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    client_key = _client_key()
    wait_seconds = login_rate_limiter.get_wait_seconds("admin", client_key)

    if request.method == "POST":
        if wait_seconds:
            flash(f"Too many failed login attempts. Try again in {format_wait_time(wait_seconds)}.", "error")
            return render_template("login.html", wait_seconds=wait_seconds)

        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        user = AdminUser.query.filter_by(username=username).first()
        if user and user.is_super_admin:
            flash("Use the separate super admin login URL.", "error")
            return render_template("login.html", wait_seconds=0)

        if user and user.check_password(password):
            if not user.has_valid_period():
                flash("Your admin account period has expired.", "error")
                return render_template("login.html")

            login_rate_limiter.clear("admin", client_key)
            login_user(user)
            return redirect(url_for("main.dashboard"))

        wait_seconds = login_rate_limiter.record_failure("admin", client_key)
        if wait_seconds:
            flash(f"Too many failed login attempts. Try again in {format_wait_time(wait_seconds)}.", "error")
        else:
            flash("Incorrect username or password.", "error")

    return render_template("login.html", wait_seconds=wait_seconds)


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))


def _client_key():
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.remote_addr or "unknown"
