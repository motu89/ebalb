import click
from flask import Flask
from flask_login import LoginManager
from flask_wtf import CSRFProtect

from config import Config
from extensions import db, login_manager, TokenCipher
from models import AdminUser


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

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

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(accounts_bp)
    app.register_blueprint(products_bp)
    app.register_blueprint(listings_bp)
    app.register_blueprint(webhook_bp)

    with app.app_context():
        db.create_all()
        _ensure_admin_user(app)

    register_cli(app)
    return app


def _ensure_admin_user(app):
    """Creates the dashboard login user from .env if it doesn't exist yet."""
    if AdminUser.query.filter_by(username=app.config["ADMIN_USERNAME"]).first():
        return
    user = AdminUser(username=app.config["ADMIN_USERNAME"])
    user.set_password(app.config["ADMIN_PASSWORD"])
    db.session.add(user)
    db.session.commit()


def register_cli(app):
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
