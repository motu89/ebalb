from flask import Blueprint, render_template
from flask_login import login_required

from models import EbayAccount, Product, Listing

main_bp = Blueprint("main", __name__)


@main_bp.route("/privacy")
def privacy():
    return render_template("privacy.html")


@main_bp.route("/")
@login_required
def dashboard():
    total_accounts = EbayAccount.query.filter_by(is_active=True).count()
    connected_accounts = EbayAccount.query.filter(
        EbayAccount.is_active == True, EbayAccount._refresh_token.isnot(None)
    ).count()
    total_products = Product.query.count()
    total_listings = Listing.query.count()
    successful_listings = Listing.query.filter_by(status="success").count()
    failed_listings = Listing.query.filter_by(status="failed").count()

    recent = Listing.query.order_by(Listing.created_at.desc()).limit(8).all()

    return render_template(
        "dashboard.html",
        total_accounts=total_accounts,
        connected_accounts=connected_accounts,
        total_products=total_products,
        total_listings=total_listings,
        successful_listings=successful_listings,
        failed_listings=failed_listings,
        recent=recent,
    )
