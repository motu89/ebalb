"""
All direct communication with eBay's Sell APIs lives here.
Nothing outside this file should build an eBay request by hand.
"""
import base64
import time
import requests
from flask import current_app


class EbayAPIError(Exception):
    def __init__(self, message, status_code=None, payload=None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


def _basic_auth_header():
    app_id = current_app.config["EBAY_APP_ID"]
    cert_id = current_app.config["EBAY_CERT_ID"]
    creds = base64.b64encode(f"{app_id}:{cert_id}".encode()).decode()
    return f"Basic {creds}"


def build_consent_url(scopes=None):
    """Step 1: the URL you send a seller to, to grant your app access to their account."""
    scopes = scopes or current_app.config["EBAY_SCOPES"]
    scope_param = "%20".join(requests.utils.quote(s, safe="") for s in scopes)
    base = current_app.config["EBAY_AUTH_BASE"]
    app_id = current_app.config["EBAY_APP_ID"]
    runame = current_app.config["EBAY_RUNAME"]
    return (
        f"{base}/oauth2/authorize"
        f"?client_id={app_id}"
        f"&redirect_uri={runame}"
        f"&response_type=code"
        f"&scope={scope_param}"
    )


def exchange_code_for_tokens(auth_code):
    """Step 2: turn the one-time ?code=... from eBay's redirect into real tokens."""
    url = f"{current_app.config['EBAY_API_BASE']}/identity/v1/oauth2/token"
    resp = requests.post(
        url,
        headers={
            "Authorization": _basic_auth_header(),
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "authorization_code",
            "code": auth_code,
            "redirect_uri": current_app.config["EBAY_RUNAME"],
        },
        timeout=20,
    )
    if resp.status_code != 200:
        raise EbayAPIError("Failed to exchange auth code for tokens", resp.status_code, resp.text)
    return resp.json()  # contains access_token, refresh_token, expires_in, refresh_token_expires_in


# Small in-process cache so we don't refresh a new access token on every single call
_access_token_cache = {}


def get_fresh_access_token(refresh_token, scopes=None):
    """Step 3, repeated forever: no login, just trade the stored refresh_token for a short-lived access_token."""
    cached = _access_token_cache.get(refresh_token)
    if cached and cached["expires_at"] > time.time() + 30:
        return cached["access_token"]

    scopes = scopes or current_app.config["EBAY_SCOPES"]
    url = f"{current_app.config['EBAY_API_BASE']}/identity/v1/oauth2/token"
    resp = requests.post(
        url,
        headers={
            "Authorization": _basic_auth_header(),
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": " ".join(scopes),
        },
        timeout=20,
    )
    if resp.status_code != 200:
        raise EbayAPIError("Failed to refresh access token", resp.status_code, resp.text)
    data = resp.json()
    _access_token_cache[refresh_token] = {
        "access_token": data["access_token"],
        "expires_at": time.time() + data.get("expires_in", 7200),
    }
    return data["access_token"]


def _headers(access_token, content_language="en-US"):
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Content-Language": content_language,
    }


def create_inventory_location(access_token, location_key, name, address_line1, city,
                                state_or_province, postal_code, country="US"):
    """
    PUT /sell/inventory/v1/location/{merchantLocationKey}
    Every seller needs at least one of these before any offer can be published -
    it's what eBay uses to calculate shipping. Works identically in sandbox and production.
    """
    base = current_app.config["EBAY_API_BASE"]
    url = f"{base}/sell/inventory/v1/location/{location_key}"
    payload = {
        "location": {
            "address": {
                "addressLine1": address_line1,
                "city": city,
                "stateOrProvince": state_or_province,
                "postalCode": postal_code,
                "country": country,
            }
        },
        "name": name,
        "merchantLocationStatus": "ENABLED",
        "locationTypes": ["WAREHOUSE"],
    }
    resp = requests.post(url, json=payload, headers=_headers(access_token), timeout=20)
    if resp.status_code not in (200, 201, 204):
        # errorId 25803: the location already exists under this key - that's fine, it's usable.
        if resp.status_code == 400 and "25803" in resp.text:
            return True
        raise EbayAPIError(f"Creating location '{location_key}' failed", resp.status_code, resp.text)
    return True


import json


def _extract_duplicate_policy_id(payload_text):
    """
    When eBay rejects a policy creation because the name already exists (errorId 20400),
    it hands back the existing policy's ID in the error parameters - reuse it instead of failing.
    """
    try:
        data = json.loads(payload_text)
        params = data["errors"][0].get("parameters", [])
        for p in params:
            if p.get("name") in ("Shipping Profile Id", "Payment Profile Id",
                                   "Return Profile Id", "DuplicateProfileId"):
                return p.get("value")
    except Exception:
        return None
    return None


def opt_in_to_business_policies(access_token):
    """
    POST /sell/account/v1/program/opt_in
    Sandbox (and some new production) accounts must opt in to the Business Policies
    program before fulfillment/payment/return policies can be created at all.
    Safe to call even if already opted in.
    """
    base = current_app.config["EBAY_API_BASE"]
    url = f"{base}/sell/account/v1/program/opt_in"
    payload = {"programType": "SELLING_POLICY_MANAGEMENT"}
    resp = requests.post(url, json=payload, headers=_headers(access_token), timeout=20)
    if resp.status_code not in (200, 204):
        raise EbayAPIError("Opting in to business policies failed", resp.status_code, resp.text)
    return True


def create_fulfillment_policy(access_token, name, shipping_service="USPSPriority",
                                handling_time_days=1, marketplace_id="EBAY_US"):
    """
    POST /sell/account/v1/fulfillment_policy
    Creates a shipping policy with at least one valid shipping service - the missing
    piece behind errorId 25007 ("invalid data in the associated Fulfillment policy").
    """
    base = current_app.config["EBAY_API_BASE"]
    url = f"{base}/sell/account/v1/fulfillment_policy"
    payload = {
        "name": name,
        "marketplaceId": marketplace_id,
        "categoryTypes": [{"name": "ALL_EXCLUDING_MOTORS_VEHICLES"}],
        "handlingTime": {"value": handling_time_days, "unit": "DAY"},
        "shippingOptions": [
            {
                "optionType": "DOMESTIC",
                "costType": "FLAT_RATE",
                "shippingServices": [
                    {
                        "sortOrder": 1,
                        "shippingServiceCode": shipping_service,
                        "shippingCost": {"value": "0.00", "currency": "USD"},
                        "freeShipping": True,
                    }
                ],
            }
        ],
    }
    resp = requests.post(url, json=payload, headers=_headers(access_token), timeout=20)
    if resp.status_code not in (200, 201):
        existing_id = _extract_duplicate_policy_id(resp.text)
        if existing_id:
            return existing_id
        raise EbayAPIError("Creating fulfillment policy failed", resp.status_code, resp.text)
    return resp.json().get("fulfillmentPolicyId")


def create_payment_policy(access_token, name, marketplace_id="EBAY_US"):
    """POST /sell/account/v1/payment_policy - modern eBay marketplaces use immediate payment via eBay's system."""
    base = current_app.config["EBAY_API_BASE"]
    url = f"{base}/sell/account/v1/payment_policy"
    payload = {
        "name": name,
        "marketplaceId": marketplace_id,
        "categoryTypes": [{"name": "ALL_EXCLUDING_MOTORS_VEHICLES"}],
        "immediatePay": False,
    }
    resp = requests.post(url, json=payload, headers=_headers(access_token), timeout=20)
    if resp.status_code not in (200, 201):
        existing_id = _extract_duplicate_policy_id(resp.text)
        if existing_id:
            return existing_id
        raise EbayAPIError("Creating payment policy failed", resp.status_code, resp.text)
    return resp.json().get("paymentPolicyId")


def create_return_policy(access_token, name, return_days=30, marketplace_id="EBAY_US"):
    """POST /sell/account/v1/return_policy"""
    base = current_app.config["EBAY_API_BASE"]
    url = f"{base}/sell/account/v1/return_policy"
    payload = {
        "name": name,
        "marketplaceId": marketplace_id,
        "categoryTypes": [{"name": "ALL_EXCLUDING_MOTORS_VEHICLES"}],
        "returnsAccepted": True,
        "returnPeriod": {"value": return_days, "unit": "DAY"},
        "returnShippingCostPayer": "SELLER",
        "refundMethod": "MONEY_BACK",
    }
    resp = requests.post(url, json=payload, headers=_headers(access_token), timeout=20)
    if resp.status_code not in (200, 201):
        existing_id = _extract_duplicate_policy_id(resp.text)
        if existing_id:
            return existing_id
        raise EbayAPIError("Creating return policy failed", resp.status_code, resp.text)
    return resp.json().get("returnPolicyId")


def get_business_policies(access_token, marketplace_id="EBAY_US"):
    """Fetch payment / fulfillment / return policy IDs already configured on the seller's account."""
    base = current_app.config["EBAY_API_BASE"]
    headers = {"Authorization": f"Bearer {access_token}"}

    def _first_id(resp_json, key, id_field):
        items = resp_json.get(key, [])
        return items[0].get(id_field) if items else None

    fulfillment = requests.get(
        f"{base}/sell/account/v1/fulfillment_policy?marketplace_id={marketplace_id}",
        headers=headers, timeout=20,
    ).json()
    payment = requests.get(
        f"{base}/sell/account/v1/payment_policy?marketplace_id={marketplace_id}",
        headers=headers, timeout=20,
    ).json()
    ret = requests.get(
        f"{base}/sell/account/v1/return_policy?marketplace_id={marketplace_id}",
        headers=headers, timeout=20,
    ).json()

    return {
        "fulfillment_policy_id": _first_id(fulfillment, "fulfillmentPolicies", "fulfillmentPolicyId"),
        "payment_policy_id": _first_id(payment, "paymentPolicies", "paymentPolicyId"),
        "return_policy_id": _first_id(ret, "returnPolicies", "returnPolicyId"),
    }


def get_fulfillment_policy(access_token, fulfillment_policy_id):
    """GET /sell/account/v1/fulfillment_policy/{id} - raw policy detail, used for diagnostics."""
    base = current_app.config["EBAY_API_BASE"]
    url = f"{base}/sell/account/v1/fulfillment_policy/{fulfillment_policy_id}"
    resp = requests.get(url, headers=_headers(access_token), timeout=20)
    if resp.status_code != 200:
        raise EbayAPIError("Fetching fulfillment policy failed", resp.status_code, resp.text)
    return resp.json()


def get_valid_shipping_services(access_token, marketplace_id="EBAY_US"):
    """
    GET /sell/metadata/v1/shipping/marketplace/{marketplace_id}/get_shipping_services
    Returns every shipping service code eBay currently accepts for this marketplace,
    each flagged with validForSellingFlow - the definitive way to check whether a
    code like 'USPSPriority' is still usable, instead of guessing.
    """
    base = current_app.config["EBAY_API_BASE"]
    url = f"{base}/sell/metadata/v1/shipping/marketplace/{marketplace_id}/get_shipping_services"
    resp = requests.get(url, headers=_headers(access_token), timeout=20)
    if resp.status_code != 200:
        raise EbayAPIError("Fetching valid shipping services failed", resp.status_code, resp.text)
    return resp.json().get("shippingServices", [])


def create_or_update_inventory_item(access_token, sku, product):
    """PUT /sell/inventory/v1/inventory_item/{sku} — step (a) of publishing a listing."""
    base = current_app.config["EBAY_API_BASE"]
    url = f"{base}/sell/inventory/v1/inventory_item/{sku}"

    image_urls = [product.image_url] if product.image_url else []
    aspects = {}
    if product.brand:
        aspects["Brand"] = [product.brand]

    payload = {
        "product": {
            "title": product.title[:80],  # eBay title limit
            "description": product.description or product.title,
            "aspects": aspects,
            "imageUrls": image_urls,
        },
        "condition": product.condition or "NEW",
        "availability": {
            "shipToLocationAvailability": {"quantity": product.quantity}
        },
    }
    resp = requests.put(url, json=payload, headers=_headers(access_token), timeout=20)
    if resp.status_code not in (200, 201, 204):
        raise EbayAPIError(f"Inventory item creation failed for {sku}", resp.status_code, resp.text)
    return True


def get_offer_by_sku(access_token, sku, marketplace_id="EBAY_US"):
    """GET /sell/inventory/v1/offer?sku=... - finds an offer already created for this SKU."""
    base = current_app.config["EBAY_API_BASE"]
    url = f"{base}/sell/inventory/v1/offer?sku={sku}&marketplace_id={marketplace_id}"
    resp = requests.get(url, headers=_headers(access_token), timeout=20)
    if resp.status_code != 200:
        return None
    offers = resp.json().get("offers", [])
    return offers[0]["offerId"] if offers else None


def create_offer(access_token, sku, product, category_id, policies, merchant_location_key,
                  marketplace_id="EBAY_US"):
    """POST /sell/inventory/v1/offer — step (b), turns the inventory item into a sellable offer."""
    base = current_app.config["EBAY_API_BASE"]
    url = f"{base}/sell/inventory/v1/offer"

    payload = {
        "sku": sku,
        "marketplaceId": marketplace_id,
        "format": "FIXED_PRICE",
        "availableQuantity": product.quantity,
        "categoryId": category_id or product.category_id,
        "listingDescription": product.description or product.title,
        "pricingSummary": {
            "price": {"value": f"{product.price:.2f}", "currency": "USD"}
        },
        "listingPolicies": {
            "fulfillmentPolicyId": policies["fulfillment_policy_id"],
            "paymentPolicyId": policies["payment_policy_id"],
            "returnPolicyId": policies["return_policy_id"],
        },
        "merchantLocationKey": merchant_location_key,
    }
    resp = requests.post(url, json=payload, headers=_headers(access_token), timeout=20)
    if resp.status_code not in (200, 201):
        # errorId 25002: an offer for this SKU already exists (likely from an earlier attempt) - reuse it.
        if resp.status_code == 400 and "25002" in resp.text and "Offer entity already exist" in resp.text:
            existing_offer_id = get_offer_by_sku(access_token, sku, marketplace_id)
            if existing_offer_id:
                return existing_offer_id
        raise EbayAPIError(f"Offer creation failed for {sku}", resp.status_code, resp.text)
    return resp.json().get("offerId")


def publish_offer(access_token, offer_id):
    """POST /sell/inventory/v1/offer/{offerId}/publish — step (c), goes live."""
    base = current_app.config["EBAY_API_BASE"]
    url = f"{base}/sell/inventory/v1/offer/{offer_id}/publish"
    resp = requests.post(url, headers=_headers(access_token), timeout=20)
    data = resp.json() if resp.content else {}
    if resp.status_code not in (200, 201):
        raise EbayAPIError(f"Publish failed for offer {offer_id}", resp.status_code, resp.text)
    return data.get("listingId")


def publish_product_to_account(access_token, product, account, category_id="9355",
                                 marketplace_id="EBAY_US"):
    """
    The full 3-step flow, wrapped in one call:
    inventory item -> offer -> publish. Returns (listing_id, offer_id).
    Raises EbayAPIError on any failure, with enough detail to show the user why.
    """
    sku = f"{account.id}-{product.sku}"

    create_or_update_inventory_item(access_token, sku, product)

    policies = {
        "fulfillment_policy_id": account.fulfillment_policy_id,
        "payment_policy_id": account.payment_policy_id,
        "return_policy_id": account.return_policy_id,
    }
    offer_id = create_offer(
        access_token, sku, product, category_id, policies,
        account.merchant_location_key, marketplace_id,
    )
    listing_id = publish_offer(access_token, offer_id)
    return listing_id, offer_id
