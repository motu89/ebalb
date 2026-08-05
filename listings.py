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
    accounts = EbayAccount.query.filter_by(is_active=True).order_by(EbayAccount.nickname).all()

    if request.method == "GET":
        selected_account_id = request.args.get("account_id", "")
        products = []
        if selected_account_id:
            products = Product.query.filter_by(account_id=int(selected_account_id)).order_by(Product.title).all()
        return render_template(
            "publish.html", accounts=accounts, products=products, selected_account_id=selected_account_id,
        )

    account_id = request.form.get("account_id")
    product_ids = request.form.getlist("product_ids")
    category_id = request.form.get("category_id", "9355")  # default fallback category

    account = EbayAccount.query.get_or_404(int(account_id))

    if not account.is_connected:
        flash(f'"{account.nickname}" is not connected to eBay yet. Connect it first.', "error")
        return redirect(url_for("listings.publish", account_id=account.id))

    if not account.merchant_location_key:
        flash(f'"{account.nickname}" has no ship-from location set. Add one on the Accounts page first.', "error")
        return redirect(url_for("listings.publish", account_id=account.id))

    if not product_ids:
        flash("Select at least one product to publish.", "error")
        return redirect(url_for("listings.publish", account_id=account.id))

    try:
        access_token = ebay_api.get_fresh_access_token(account.refresh_token)
    except ebay_api.EbayAPIError as e:
        flash(f"Could not refresh eBay access for this account: {e}", "error")
        return redirect(url_for("listings.publish", account_id=account.id))

    success_count = 0
    fail_count = 0
    blocked_count = 0
    skipped_wrong_account = 0
    aspects_cache = {}  # category_id -> required aspects list, so we don't re-fetch per product

    for pid in product_ids:
        product = Product.query.get(int(pid))
        if not product:
            continue

        # Defensive: a product should only ever be published through the store it
        # belongs to. This shouldn't normally happen since the form only ever shows
        # one account's own products, but guards against a tampered/stale submission
        # accidentally publishing one store's item through a different store's listing.
        if product.account_id and product.account_id != account.id:
            skipped_wrong_account += 1
            continue

        listing = Listing(product_id=product.id, account_id=account.id, method="inventory_api")
        effective_category_id = product.category_id or category_id

        # Check eBay's actual required item specifics for this category before
        # spending API calls creating an inventory item/offer that'll just get rejected.
        try:
            if effective_category_id not in aspects_cache:
                aspects_cache[effective_category_id] = ebay_api.get_required_aspects_for_category(
                    access_token, effective_category_id,
                )
            required = aspects_cache[effective_category_id]
            have = {name.lower() for name in product.all_aspects().keys()}
            missing = [a["name"] for a in required if a["required"] and a["name"].lower() not in have]
        except ebay_api.EbayAPIError:
            # Couldn't look up requirements (bad category id, sandbox quirk, etc.) -
            # don't block the whole publish over it, just skip validation for this one.
            missing = []

        if missing:
            listing.status = "failed"
            listing.error_message = (
                f"Missing required item specifics for category {effective_category_id}: "
                f"{', '.join(missing)}. Add these to the product's 'aspects' column and re-upload."
            )
            blocked_count += 1
            db.session.add(listing)
            continue

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
    if blocked_count:
        flash(f"{blocked_count} listing(s) were blocked before publishing - missing required item "
              f"specifics. Check the Listings page for exactly what's needed.", "error")
    if fail_count:
        flash(f"{fail_count} listing(s) failed - check the Listings page for details.", "error")
    if skipped_wrong_account:
        flash(f"{skipped_wrong_account} product(s) were skipped - they belong to a different store than \"{account.nickname}\".", "error")

    return redirect(url_for("listings.list_listings"))
