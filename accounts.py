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
    except ebay_api.EbayAPIError:
        # Not fatal - the seller may not have policies set up yet in sandbox
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


@accounts_bp.route("/<int:account_id>/location", methods=["POST"])
@login_required
def set_location(account_id):
    account = EbayAccount.query.get_or_404(account_id)
    account.merchant_location_key = request.form.get("merchant_location_key", "").strip()
    db.session.commit()
    flash("Ship-from location saved.", "success")
    return redirect(url_for("accounts.list_accounts"))
