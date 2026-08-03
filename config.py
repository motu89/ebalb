import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-key-not-secure")
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", "sqlite:///ebaybot.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin")

    TOKEN_ENCRYPTION_KEY = os.environ.get("TOKEN_ENCRYPTION_KEY")

    EBAY_ENV = os.environ.get("EBAY_ENV", "sandbox")
    EBAY_APP_ID = os.environ.get("EBAY_APP_ID")
    EBAY_DEV_ID = os.environ.get("EBAY_DEV_ID")
    EBAY_CERT_ID = os.environ.get("EBAY_CERT_ID")
    EBAY_RUNAME = os.environ.get("EBAY_RUNAME")

    # Base URLs switch automatically between sandbox and production
    if EBAY_ENV == "production":
        EBAY_API_BASE = "https://api.ebay.com"
        EBAY_AUTH_BASE = "https://auth.ebay.com"
    else:
        EBAY_API_BASE = "https://api.sandbox.ebay.com"
        EBAY_AUTH_BASE = "https://auth.sandbox.ebay.com"

    EBAY_SCOPES = [
        "https://api.ebay.com/oauth/api_scope/sell.inventory",
        "https://api.ebay.com/oauth/api_scope/sell.account",
        "https://api.ebay.com/oauth/api_scope/sell.fulfillment",
    ]
