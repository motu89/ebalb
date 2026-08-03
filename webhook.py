"""
eBay's Marketplace Account Deletion / Closure Notification endpoint.
You will need this reachable over HTTPS before eBay activates your PRODUCTION keyset.
Not needed for sandbox testing - included here so it's ready when you move to production.
"""
import hashlib
from flask import Blueprint, request, jsonify, current_app

webhook_bp = Blueprint("webhook", __name__, url_prefix="/webhook")

# Set this to any secret string of your choosing, then enter the SAME value
# in the eBay Developer Portal's "Notifications" settings for your keyset.
VERIFICATION_TOKEN = "set-a-verification-token-here-and-match-it-in-ebay-portal"


@webhook_bp.route("/ebay-account-deletion", methods=["GET", "POST"])
def ebay_account_deletion():
    if request.method == "GET":
        # eBay sends a one-time challenge_code to prove this endpoint is real
        challenge_code = request.args.get("challenge_code", "")
        endpoint_url = request.url_root.rstrip("/") + "/webhook/ebay-account-deletion"

        to_hash = (challenge_code + VERIFICATION_TOKEN + endpoint_url).encode("utf-8")
        response_hash = hashlib.sha256(to_hash).hexdigest()

        return jsonify({"challengeResponse": response_hash}), 200

    # POST: eBay is telling us a user deleted/closed their eBay account.
    # In production you would look up any stored data tied to that user and delete it.
    payload = request.get_json(silent=True) or {}
    current_app.logger.info("eBay account deletion notification received: %s", payload)
    return "", 200
