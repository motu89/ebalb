import click
from datetime import datetime
from flask import Flask
from flask_login import LoginManager
from flask_wtf import CSRFProtect

from config import Config
from extensions import db, login_manager, TokenCipher
from models import AdminUser
from security import CREDENTIAL_LENGTH, generate_random_credential


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    _validate_super_admin_route_key(app.config["SUPER_ADMIN_ROUTE_KEY"])

    TokenCipher.init(app.config["TOKEN_ENCRYPTION_KEY"])

    db.init_app(app)
    login_manager.init_app(app)
    CSRFProtect(app)

    @login_manager.user_loader
    def load_user(user_id):
        return AdminUser.query.get(int(user_id))

    from auth import auth_bp
    from main import main_bp
    from accounts import accounts_bp
    from products import products_bp
    from listings import listings_bp
    from webhook import webhook_bp
    from superadmin import superadmin_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(accounts_bp)
    app.register_blueprint(products_bp)
    app.register_blueprint(listings_bp)
    app.register_blueprint(webhook_bp)
    app.register_blueprint(superadmin_bp, url_prefix=f"/{app.config['SUPER_ADMIN_ROUTE_KEY']}")

    with app.app_context():
        db.create_all()
        _run_light_migrations()
        _ensure_super_admin_user(app)
        _ensure_admin_user(app)

    register_cli(app)
    return app


def _validate_super_admin_route_key(route_key):
    if not route_key or len(route_key) != CREDENTIAL_LENGTH or not route_key.isalnum():
        raise RuntimeError(
            f"SUPER_ADMIN_ROUTE_KEY must be exactly {CREDENTIAL_LENGTH} letters/numbers."
        )


def _run_light_migrations():
    """
    db.create_all() only creates tables that don't exist yet - it never adds new
    columns to a table that's already there. So for anyone running this app against
    an existing database, we add the newer columns by hand here, once, safely.
    If they're already there, this
    is a no-op. This is a lightweight stand-in for a real migration tool (e.g. Alembic)
    and only ever ADDs nullable columns, so it can't destroy existing data.
    """
    from sqlalchemy import inspect, text
    inspector = inspect(db.engine)
    table_names = set(inspector.get_table_names())

    if "admin_user" in table_names:
        admin_columns = {col["name"] for col in inspector.get_columns("admin_user")}
        with db.engine.begin() as conn:
            if "role" not in admin_columns:
                conn.execute(text("ALTER TABLE admin_user ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'admin'"))
            if "expires_at" not in admin_columns:
                conn.execute(text("ALTER TABLE admin_user ADD COLUMN expires_at DATETIME"))
            if "created_at" not in admin_columns:
                conn.execute(text("ALTER TABLE admin_user ADD COLUMN created_at DATETIME"))

    if "product" not in table_names:
        return  # fresh install, db.create_all() above already made it correctly
    existing_columns = {col["name"] for col in inspector.get_columns("product")}
    if "aspects_json" not in existing_columns:
        with db.engine.begin() as conn:
            conn.execute(text("ALTER TABLE product ADD COLUMN aspects_json TEXT"))
    if "account_id" not in existing_columns:
        with db.engine.begin() as conn:
            conn.execute(text("ALTER TABLE product ADD COLUMN account_id INTEGER"))
        # Existing products predate per-account separation - they land in the "Unassigned"
        # pool (account_id NULL) rather than being guessed into some account, since guessing
        # wrong would silently mix one store's catalog into another's.


def _ensure_super_admin_user(app):
    """Optionally creates a super admin from .env when both values are present."""
    if AdminUser.query.filter_by(role=AdminUser.ROLE_SUPER_ADMIN).first():
        return

    username = app.config.get("SUPER_ADMIN_USERNAME")
    password = app.config.get("SUPER_ADMIN_PASSWORD")
    if not username and not password:
        return
    if not username or not password:
        raise RuntimeError("Set both SUPER_ADMIN_USERNAME and SUPER_ADMIN_PASSWORD, or neither.")
    if len(password) < CREDENTIAL_LENGTH:
        raise RuntimeError(f"SUPER_ADMIN_PASSWORD must be at least {CREDENTIAL_LENGTH} characters.")

    user = AdminUser(username=username, role=AdminUser.ROLE_SUPER_ADMIN, expires_at=None)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()


def _ensure_admin_user(app):
    """Creates the dashboard login user from .env if it doesn't exist yet."""
    if AdminUser.query.filter_by(username=app.config["ADMIN_USERNAME"]).first():
        return
    user = AdminUser(username=app.config["ADMIN_USERNAME"])
    user.set_password(app.config["ADMIN_PASSWORD"])
    db.session.add(user)
    db.session.commit()


def register_cli(app):
    @app.cli.command("create-super-admin")
    @click.option("--username", help=f"Optional username. Defaults to a random {CREDENTIAL_LENGTH}-character string.")
    @click.option("--password", help=f"Optional password. Defaults to a random {CREDENTIAL_LENGTH}-character string.")
    def create_super_admin(username, password):
        """Create the first super admin, if one does not already exist."""
        existing = AdminUser.query.filter_by(role=AdminUser.ROLE_SUPER_ADMIN).first()
        if existing:
            click.echo(f"Super admin already exists: {existing.username}")
            click.echo("Use flask reset-super-admin-password <username> to rotate its password.")
            return

        username = (username or generate_random_credential()).strip()
        password = password or generate_random_credential()

        if len(password) < CREDENTIAL_LENGTH:
            click.echo(f"Password must be at least {CREDENTIAL_LENGTH} characters.")
            return
        if AdminUser.query.filter_by(username=username).first():
            click.echo("That username already exists.")
            return

        user = AdminUser(
            username=username,
            role=AdminUser.ROLE_SUPER_ADMIN,
            expires_at=None,
            created_at=datetime.utcnow(),
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        click.echo("Super admin created.")
        click.echo(f"Username: {username}")
        click.echo(f"Password: {password}")
        click.echo("Save these credentials now. The password is not stored in plain text.")

    @app.cli.command("reset-super-admin-password")
    @click.argument("username")
    def reset_super_admin_password(username):
        """Reset a super admin password to a fresh 16-character random string."""
        user = AdminUser.query.filter_by(username=username, role=AdminUser.ROLE_SUPER_ADMIN).first()
        if not user:
            click.echo("Super admin user not found.")
            return

        password = generate_random_credential()
        user.set_password(password)
        db.session.commit()
        click.echo("Super admin password reset.")
        click.echo(f"Username: {username}")
        click.echo(f"Password: {password}")
        click.echo("Save this password now. It is not stored in plain text.")

    @app.cli.command("reset-admin-password")
    @click.argument("new_password")
    def reset_admin_password(new_password):
        """Usage: flask reset-admin-password <new_password>"""
        user = AdminUser.query.filter_by(username=app.config["ADMIN_USERNAME"]).first()
        if not user:
            click.echo("Admin user not found.")
            return
        user.set_password(new_password)
        db.session.commit()
        click.echo("Password updated.")


app = create_app()

if __name__ == "__main__":
    app.run(debug=True, port=5000)
