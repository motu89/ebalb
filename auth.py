from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import current_user, login_user, logout_user, login_required

from models import AdminUser

auth_bp = Blueprint("auth", __name__)


@auth_bp.before_app_request
def enforce_admin_period():
    if current_user.is_authenticated and not current_user.has_valid_period():
        logout_user()
        flash("Your admin account period has expired.", "error")
        return redirect(url_for("auth.login"))


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        user = AdminUser.query.filter_by(username=username).first()
        if user and user.check_password(password):
            if not user.has_valid_period():
                flash("Your admin account period has expired.", "error")
                return render_template("login.html")

            login_user(user)
            if user.is_super_admin:
                return redirect(url_for("superadmin.manage_users"))
            return redirect(url_for("main.dashboard"))

        flash("Incorrect username or password.", "error")

    return render_template("login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
