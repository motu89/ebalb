from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required

from extensions import db
from models import Product, EbayAccount, Listing
import ebay_api

listings_bp = Blueprint("listings", __name__, url_prefix="/listings")


@listings_bp.route("/")
@login_required
def list_listings():
    listings = Listing.query.order_by(Listing.created_at.desc()).limit(200).all()
    return render_template("listings.html", listings=listings)


@listings_bp.route("/<int:listing_id>")
@login_required
def listing_detail(listing_id):
    """Full, untruncated detail for one publish attempt - use this to read the complete
    eBay error JSON when the Listings table's preview column cuts it off."""
    listing = Listing.query.get_or_404(listing_id)
    return render_template("listing_detail.html", listing=listing)


@listings_bp.route("/publish", methods=["GET", "POST"])
@login_required
def publish():
    accounts = EbayAccount.query.filter_by(is_active=True).all()
    products = Product.query.all()

    if request.method == "GET":
        return render_template("publish.html", accounts=accounts, products=products)

    account_id = request.form.get("account_id")
    product_ids = request.form.getlist("product_ids")
    category_id = request.form.get("category_id", "9355")  # default fallback category

    account = EbayAccount.query.get_or_404(int(account_id))

    if not account.is_connected:
        flash(f'"{account.nickname}" is not connected to eBay yet. Connect it first.', "error")
        return redirect(url_for("listings.publish"))

    if not account.merchant_location_key:
        flash(f'"{account.nickname}" has no ship-from location set. Add one on the Accounts page first.', "error")
        return redirect(url_for("listings.publish"))

    if not product_ids:
        flash("Select at least one product to publish.", "error")
        return redirect(url_for("listings.publish"))

    try:
        access_token = ebay_api.get_fresh_access_token(account.refresh_token)
    except ebay_api.EbayAPIError as e:
        flash(f"Could not refresh eBay access for this account: {e}", "error")
        return redirect(url_for("listings.publish"))

    success_count = 0
    fail_count = 0

    for pid in product_ids:
        product = Product.query.get(int(pid))
        if not product:
            continue

        listing = Listing(product_id=product.id, account_id=account.id, method="inventory_api")

        try:
            listing_id, offer_id = ebay_api.publish_product_to_account(
                access_token, product, account, category_id=category_id,
            )
            listing.status = "success"
            listing.ebay_listing_id = listing_id
            listing.offer_id = offer_id
            success_count += 1
        except ebay_api.EbayAPIError as e:
            listing.status = "failed"
            listing.error_message = str(e.payload)[:2000] if e.payload else str(e)
            fail_count += 1

        db.session.add(listing)

    db.session.commit()

    if success_count:
        flash(f"Published {success_count} listing(s) to \"{account.nickname}\".", "success")
    if fail_count:
        flash(f"{fail_count} listing(s) failed - check the Listings page for details.", "error")

    return redirect(url_for("listings.list_listings"))
