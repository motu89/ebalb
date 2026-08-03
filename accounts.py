from datetime import datetime, timedelta
from flask import Blueprint, render_template, redirect, url_for, request, flash, current_app
from flask_login import login_required

from extensions import db
from models import EbayAccount
import ebay_api

accounts_bp = Blueprint("accounts", __name__, url_prefix="/accounts")


@accounts_bp.route("/")
@login_required
def list_accounts():
    accounts = EbayAccount.query.order_by(EbayAccount.created_at.desc()).all()
    return render_template("accounts.html", accounts=accounts, ebay_env=current_app.config["EBAY_ENV"])


@accounts_bp.route("/new", methods=["POST"])
@login_required
def create_account():
    nickname = request.form.get("nickname", "").strip()
    if not nickname:
        flash("Give the store a nickname first.", "error")
        return redirect(url_for("accounts.list_accounts"))

    account = EbayAccount(nickname=nickname)
    db.session.add(account)
    db.session.commit()
    flash(f'"{nickname}" added. Click Connect to authorize it with eBay.', "success")
    return redirect(url_for("accounts.list_accounts"))


@accounts_bp.route("/<int:account_id>/connect")
@login_required
def connect(account_id):
    account = EbayAccount.query.get_or_404(account_id)
    # We stash which account this consent flow is for using Flask's session-free approach:
    # eBay's redirect gives us back a code, and we pass account_id through the "state" param.
    consent_url = ebay_api.build_consent_url() + f"&state={account.id}"
    return redirect(consent_url)


@accounts_bp.route("/callback")
def oauth_callback():
    """eBay redirects here (your RuName) after the seller approves access."""
    code = request.args.get("code")
    account_id = request.args.get("state")

    if not code or not account_id:
        flash("eBay did not return an authorization code. Try connecting again.", "error")
        return redirect(url_for("accounts.list_accounts"))

    account = EbayAccount.query.get_or_404(int(account_id))

    try:
        tokens = ebay_api.exchange_code_for_tokens(code)
    except ebay_api.EbayAPIError as e:
        flash(f"Connecting to eBay failed: {e}", "error")
        return redirect(url_for("accounts.list_accounts"))

    account.refresh_token = tokens["refresh_token"]
    expires_in = tokens.get("refresh_token_expires_in", 47304000)  # ~18 months default
    account.refresh_token_expires = datetime.utcnow() + timedelta(seconds=expires_in)

    # Pull business policies right away so we're ready to publish
    try:
        access_token = ebay_api.get_fresh_access_token(account.refresh_token)
        policies = ebay_api.get_business_policies(access_token)
        account.fulfillment_policy_id = policies["fulfillment_policy_id"]
        account.payment_policy_id = policies["payment_policy_id"]
        account.return_policy_id = policies["return_policy_id"]
    except Exception:
        # Not fatal - the seller may not have policies set up yet in sandbox,
        # or eBay's response shape didn't match what we expected. Either way,
        # the account is still connected; policies can be created later.
        pass

    db.session.commit()
    flash(f'"{account.nickname}" is connected.', "success")
    return redirect(url_for("accounts.list_accounts"))


@accounts_bp.route("/<int:account_id>/disconnect", methods=["POST"])
@login_required
def disconnect(account_id):
    account = EbayAccount.query.get_or_404(account_id)
    account.refresh_token = None
    account.is_active = False
    db.session.commit()
    flash(f'"{account.nickname}" disconnected.', "success")
    return redirect(url_for("accounts.list_accounts"))


@accounts_bp.route("/<int:account_id>/create_policies", methods=["POST"])
@login_required
def create_policies(account_id):
    """One-click creation of working payment/fulfillment/return policies -
    fixes errorId 25007/25008/25009 caused by missing or broken sandbox defaults."""
    account = EbayAccount.query.get_or_404(account_id)

    if not account.is_connected:
        flash("Connect this store to eBay before creating policies.", "error")
        return redirect(url_for("accounts.list_accounts"))

    try:
        access_token = ebay_api.get_fresh_access_token(account.refresh_token)

        try:
            ebay_api.opt_in_to_business_policies(access_token)
        except ebay_api.EbayAPIError:
            pass  # already opted in, or opt-in not needed on this account - safe to continue

        account.fulfillment_policy_id = ebay_api.create_fulfillment_policy(
            access_token, name=f"{account.nickname} Shipping Policy"
        )
        account.payment_policy_id = ebay_api.create_payment_policy(
            access_token, name=f"{account.nickname} Payment Policy"
        )
        account.return_policy_id = ebay_api.create_return_policy(
            access_token, name=f"{account.nickname} Return Policy"
        )
    except ebay_api.EbayAPIError as e:
        flash(f"Creating policies failed: {e.payload or e}", "error")
        return redirect(url_for("accounts.list_accounts"))

    db.session.commit()
    flash(f'New working business policies created for "{account.nickname}".', "success")
    return redirect(url_for("accounts.list_accounts"))


@accounts_bp.route("/<int:account_id>/location", methods=["POST"])
@login_required
def set_location(account_id):
    """Actually creates the location on eBay's side (not just a local label)."""
    account = EbayAccount.query.get_or_404(account_id)

    if not account.is_connected:
        flash("Connect this store to eBay before adding a location.", "error")
        return redirect(url_for("accounts.list_accounts"))

    location_key = request.form.get("location_key", "").strip()
    name = request.form.get("location_name", "").strip()
    address_line1 = request.form.get("address_line1", "").strip()
    city = request.form.get("city", "").strip()
    state = request.form.get("state", "").strip()
    postal_code = request.form.get("postal_code", "").strip()
    country = request.form.get("country", "US").strip() or "US"

    if not all([location_key, name, address_line1, city, state, postal_code]):
        flash("Fill in every field to create a location.", "error")
        return redirect(url_for("accounts.list_accounts"))

    try:
        access_token = ebay_api.get_fresh_access_token(account.refresh_token)
        ebay_api.create_inventory_location(
            access_token, location_key, name, address_line1, city, state, postal_code, country,
        )
    except ebay_api.EbayAPIError as e:
        flash(f"Creating the location on eBay failed: {e.payload or e}", "error")
        return redirect(url_for("accounts.list_accounts"))

    account.merchant_location_key = location_key
    db.session.commit()
    flash(f'Location "{location_key}" created on eBay and saved to "{account.nickname}".', "success")
    return redirect(url_for("accounts.list_accounts"))
