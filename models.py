from datetime import datetime
import json
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from extensions import db, TokenCipher


class AdminUser(UserMixin, db.Model):
    """Dashboard login user (not an eBay account)."""

    ROLE_ADMIN = "admin"
    ROLE_SUPER_ADMIN = "super_admin"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default=ROLE_ADMIN)
    expires_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, raw_password):
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password):
        return check_password_hash(self.password_hash, raw_password)

    @property
    def is_super_admin(self):
        return self.role == self.ROLE_SUPER_ADMIN

    def has_valid_period(self):
        return self.is_super_admin or self.expires_at is None or self.expires_at >= datetime.utcnow()

    @property
    def is_active(self):
        return self.has_valid_period()


class EbayAccount(db.Model):
    """One connected eBay seller account (sandbox or production)."""
    id = db.Column(db.Integer, primary_key=True)
    nickname = db.Column(db.String(120), nullable=False)
    ebay_username = db.Column(db.String(120), nullable=True)

    _refresh_token = db.Column("refresh_token", db.Text, nullable=True)
    refresh_token_expires = db.Column(db.DateTime, nullable=True)

    payment_policy_id = db.Column(db.String(120), nullable=True)
    fulfillment_policy_id = db.Column(db.String(120), nullable=True)
    return_policy_id = db.Column(db.String(120), nullable=True)
    merchant_location_key = db.Column(db.String(120), nullable=True)

    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    listings = db.relationship("Listing", backref="account", lazy=True)
    # Deliberately NOT cascade-deleted: removing a connected account shouldn't wipe out
    # its product catalog, it should just leave those products "unassigned" (see below).
    products = db.relationship("Product", backref="account", lazy=True)

    @property
    def refresh_token(self):
        return TokenCipher.decrypt(self._refresh_token) if self._refresh_token else None

    @refresh_token.setter
    def refresh_token(self, value):
        self._refresh_token = TokenCipher.encrypt(value) if value else None

    @property
    def is_connected(self):
        return bool(self._refresh_token)


class Product(db.Model):
    """A row imported from a CSV, ready to be listed. Always belongs to exactly one
    connected eBay account - each store's product data is kept fully separate."""
    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey("ebay_account.id"), nullable=True)
    sku = db.Column(db.String(120), nullable=False)
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)
    price = db.Column(db.Float, nullable=False, default=0)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    category_id = db.Column(db.String(50), nullable=True)
    brand = db.Column(db.String(120), nullable=True)
    condition = db.Column(db.String(50), default="NEW")

    # Holds one or more image URLs joined with "|". A plain single URL with no
    # "|" in it (the old format) still works fine — get_image_urls() just
    # returns a one-item list, so nothing older breaks.
    image_url = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    listings = db.relationship("Listing", backref="product", lazy=True, cascade="all, delete-orphan")

    MAX_IMAGES = 12  # eBay's hard limit per listing

    def get_image_urls(self):
        """Returns the stored image(s) as a clean list, capped at MAX_IMAGES."""
        if not self.image_url:
            return []
        urls = [u.strip() for u in self.image_url.split("|") if u.strip()]
        return urls[: self.MAX_IMAGES]

    def set_image_urls(self, urls):
        """Takes a list of URLs, dedupes/caps/cleans them, and stores them."""
        seen = set()
        cleaned = []
        for u in urls:
            u = (u or "").strip()
            if not u or u in seen:
                continue
            seen.add(u)
            cleaned.append(u)
            if len(cleaned) == self.MAX_IMAGES:
                break
        self.image_url = "|".join(cleaned) if cleaned else None

    @property
    def image_count(self):
        return len(self.get_image_urls())

    # Extra category-specific details (Size, Color, Material, etc.) beyond the
    # fixed columns above. Stored as JSON: {"Size": ["Large"], "Color": ["Black"]}
    # eBay's aspect format always wraps each value in a list, so we mirror that.
    aspects_json = db.Column(db.Text, nullable=True)

    def get_aspects(self):
        """Returns the extra aspects as a dict, e.g. {'Size': ['Large'], 'Color': ['Black']}."""
        if not self.aspects_json:
            return {}
        try:
            return json.loads(self.aspects_json)
        except (ValueError, TypeError):
            return {}

    def set_aspects(self, aspects_dict):
        """Takes a dict of name -> value (or name -> [values]) and stores it as JSON."""
        cleaned = {}
        for name, value in (aspects_dict or {}).items():
            name = str(name).strip()
            if not name:
                continue
            values = value if isinstance(value, list) else [value]
            values = [str(v).strip() for v in values if str(v).strip()]
            if values:
                cleaned[name] = values
        self.aspects_json = json.dumps(cleaned) if cleaned else None

    def all_aspects(self):
        """Brand + Color/Size/etc merged into one dict, ready to send to eBay as 'aspects'."""
        merged = dict(self.get_aspects())
        if self.brand and "Brand" not in merged:
            merged["Brand"] = [self.brand]
        return merged


class Listing(db.Model):
    """One publish attempt of a Product to a specific EbayAccount."""
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False)
    account_id = db.Column(db.Integer, db.ForeignKey("ebay_account.id"), nullable=False)

    method = db.Column(db.String(20), default="inventory_api")  # inventory_api | feed_api
    status = db.Column(db.String(20), default="pending")  # pending | success | failed
    ebay_listing_id = db.Column(db.String(120), nullable=True)
    offer_id = db.Column(db.String(120), nullable=True)
    error_message = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
