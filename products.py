import pandas as pd
from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required

from extensions import db
from models import Product, EbayAccount
import ebay_api

products_bp = Blueprint("products", __name__, url_prefix="/products")

REQUIRED_COLUMNS = {"sku", "title", "price"}
OPTIONAL_COLUMNS = ["description", "quantity", "category_id", "brand", "color", "condition", "image_url", "aspects"]


def parse_image_urls(raw):
    """
    Turns one CSV cell into a clean list of image URLs.
    Accepts multiple URLs separated by "|" (preferred) or "," (also fine).
    Silently drops anything that isn't a real http(s) link.
    Caps at 12 (eBay's limit) — returns (urls, how_many_were_dropped_for_being_over_12).
    """
    if not raw or not str(raw).strip():
        return [], 0

    raw = str(raw).strip()
    parts = raw.split("|") if "|" in raw else raw.split(",")

    seen = set()
    urls = []
    for p in parts:
        p = p.strip()
        if not p or p in seen:
            continue
        if not (p.startswith("http://") or p.startswith("https://")):
            continue
        seen.add(p)
        urls.append(p)

    overflow = max(0, len(urls) - 12)
    return urls[:12], overflow


def parse_aspects(raw):
    """
    Turns one CSV cell into an aspects dict, e.g.:
      "Color:Black|Size:Large|Material:Cotton" -> {"Color": ["Black"], "Size": ["Large"], "Material": ["Cotton"]}
    A key can have multiple values separated by commas: "Size:Large,XL" -> {"Size": ["Large", "XL"]}
    Anything that doesn't have a "Key:Value" shape is silently skipped.
    """
    if not raw or not str(raw).strip():
        return {}

    aspects = {}
    for pair in str(raw).strip().split("|"):
        if ":" not in pair:
            continue
        key, _, value = pair.partition(":")
        key = key.strip()
        if not key:
            continue
        values = [v.strip() for v in value.split(",") if v.strip()]
        if not values:
            continue
        bucket = aspects.setdefault(key, [])
        for v in values:
            if v not in bucket:
                bucket.append(v)
    return aspects


@products_bp.route("/")
@login_required
def list_products():
    accounts = EbayAccount.query.order_by(EbayAccount.nickname).all()
    account_filter = request.args.get("account_id", "")

    query = Product.query
    if account_filter == "unassigned":
        query = query.filter(Product.account_id.is_(None))
    elif account_filter:
        query = query.filter(Product.account_id == int(account_filter))

    products = query.order_by(Product.created_at.desc()).all()
    return render_template(
        "products.html", products=products, accounts=accounts, account_filter=account_filter,
    )


@products_bp.route("/upload", methods=["GET", "POST"])
@login_required
def upload():
    accounts = EbayAccount.query.filter_by(is_active=True).order_by(EbayAccount.nickname).all()

    if request.method == "GET":
        return render_template("upload.html", accounts=accounts)

    account_id = request.form.get("account_id", "").strip()
    if not account_id:
        flash("Choose which store this CSV is for.", "error")
        return redirect(url_for("products.upload"))
    account = EbayAccount.query.get_or_404(int(account_id))

    file = request.files.get("csv_file")
    if not file or file.filename == "":
        flash("Choose a CSV or Excel file first.", "error")
        return redirect(url_for("products.upload"))

    try:
        if file.filename.lower().endswith((".xlsx", ".xls")):
            df = pd.read_excel(file)
        else:
            df = pd.read_csv(file)
    except Exception as e:
        flash(f"Could not read that file: {e}", "error")
        return redirect(url_for("products.upload"))

    df.columns = [c.strip().lower() for c in df.columns]
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        flash(f"Missing required column(s): {', '.join(sorted(missing))}. "
              f"Required: sku, title, price.", "error")
        return redirect(url_for("products.upload"))

    added = 0
    updated = 0
    skipped_rows = []       # rows that couldn't be imported at all, with exactly what's wrong
    rows_with_overflow = 0
    imported_products = []  # every Product touched this run (new or updated), for the category check below

    # Match existing products by (sku, account) so a re-upload updates them instead of
    # creating duplicates - scoped to this account only, since two different stores can
    # reasonably reuse the same SKU for two completely different products. Fetched in one
    # query rather than one-per-row, since this needs to scale to CSVs with thousands of rows.
    all_skus_in_file = [
        str(row.get("sku")).strip() for _, row in df.iterrows()
        if not pd.isna(row.get("sku")) and str(row.get("sku")).strip()
    ]
    existing_by_sku = {
        p.sku: p for p in Product.query.filter(
            Product.sku.in_(all_skus_in_file), Product.account_id == account.id,
        ).all()
    } if all_skus_in_file else {}

    for idx, row in df.iterrows():
        sku = "" if pd.isna(row.get("sku")) else str(row.get("sku")).strip()
        title = "" if pd.isna(row.get("title")) else str(row.get("title")).strip()

        missing_fields = []
        if not sku:
            missing_fields.append("sku")
        if not title:
            missing_fields.append("title")

        price = None
        if pd.isna(row.get("price")):
            missing_fields.append("price")
        else:
            try:
                price = float(row["price"])
            except (TypeError, ValueError):
                missing_fields.append("price (not a valid number)")

        quantity = 1
        if "quantity" in df.columns and not pd.isna(row.get("quantity")):
            try:
                quantity = int(row["quantity"])
            except (TypeError, ValueError):
                missing_fields.append("quantity (not a valid whole number)")

        if missing_fields:
            skipped_rows.append({
                "row": idx + 2,  # +2: pandas is 0-indexed, and row 1 is the header
                "sku": sku or "(blank)",
                "title": title or "(blank)",
                "missing": missing_fields,
            })
            continue

        image_urls, overflow = parse_image_urls(
            row.get("image_url") if "image_url" in df.columns and not pd.isna(row.get("image_url")) else None
        )
        if overflow:
            rows_with_overflow += 1

        # Generic "aspects" column (Key:Value|Key:Value) for anything beyond the fixed
        # columns (Size, Material, Department, etc.) - however this category needs.
        aspects_from_row = parse_aspects(
            row.get("aspects") if "aspects" in df.columns and not pd.isna(row.get("aspects")) else None
        )
        # A plain "color" column is common enough to support directly too - folds
        # into the same aspects dict, without overriding an explicit Color: in aspects.
        if "color" in df.columns and not pd.isna(row.get("color")) and "Color" not in aspects_from_row:
            color_val = str(row["color"]).strip()
            if color_val:
                aspects_from_row["Color"] = [color_val]

        existing = existing_by_sku.get(sku)

        if existing:
            # Update in place. A column left OUT of the CSV entirely means "don't touch this
            # field" - so re-uploading just sku/title/price to bump a price doesn't wipe out
            # images/category/etc that were set some other way. A column that IS present but
            # blank for this row means "clear it".
            product = existing
            product.title = title
            product.price = price
            if "quantity" in df.columns:
                product.quantity = quantity
            if "description" in df.columns:
                product.description = None if pd.isna(row.get("description")) else str(row["description"])
            if "category_id" in df.columns:
                product.category_id = None if pd.isna(row.get("category_id")) else str(row["category_id"])
            if "brand" in df.columns:
                product.brand = None if pd.isna(row.get("brand")) else str(row["brand"])
            if "condition" in df.columns:
                product.condition = "NEW" if pd.isna(row.get("condition")) else str(row["condition"]).upper()
            if "image_url" in df.columns:
                product.set_image_urls(image_urls)
            # Aspects are merged rather than replaced, so specifics added later via quick-fix
            # (or from a different CSV) aren't wiped out by a file that only updates price/qty.
            if aspects_from_row:
                merged = product.get_aspects()
                merged.update(aspects_from_row)
                product.set_aspects(merged)
            updated += 1
        else:
            product = Product(
                account_id=account.id,
                sku=sku,
                title=title,
                description=str(row["description"]) if "description" in df.columns and not pd.isna(row.get("description")) else None,
                price=price,
                quantity=quantity,
                category_id=str(row["category_id"]) if "category_id" in df.columns and not pd.isna(row.get("category_id")) else None,
                brand=str(row["brand"]) if "brand" in df.columns and not pd.isna(row.get("brand")) else None,
                condition=str(row["condition"]).upper() if "condition" in df.columns and not pd.isna(row.get("condition")) else "NEW",
            )
            product.set_image_urls(image_urls)
            product.set_aspects(aspects_from_row)
            db.session.add(product)
            existing_by_sku[sku] = product  # so a duplicate SKU later in the same file updates this row, not re-creates it
            added += 1

        imported_products.append(product)

    db.session.commit()

    # Now that products are saved (and have category_id/aspects), check them against
    # what eBay actually requires for their category - same check publish uses, just
    # run proactively here so problems surface at upload time instead of at publish time.
    # Uses THIS upload's account specifically (not just any connected one), since that's
    # exactly who these products belong to and who'll eventually publish them.
    category_issues = []       # products whose category is missing a required aspect
    no_category_products = []  # products with no category_id at all, so nothing to check yet
    checked_categories = False

    if account.is_connected and imported_products:
        try:
            access_token = ebay_api.get_fresh_access_token(account.refresh_token)
            checked_categories = True
            aspects_cache = {}
            for product in imported_products:
                if not product.category_id:
                    no_category_products.append({"product_id": product.id, "sku": product.sku, "title": product.title})
                    continue
                if product.category_id not in aspects_cache:
                    try:
                        aspects_cache[product.category_id] = ebay_api.get_required_aspects_for_category(
                            access_token, product.category_id,
                        )
                    except ebay_api.EbayAPIError:
                        aspects_cache[product.category_id] = None  # invalid/unrecognized category id
                required = aspects_cache[product.category_id]
                if required is None:
                    continue
                have = {name.lower() for name in product.all_aspects().keys()}
                missing = [a["name"] for a in required if a["required"] and a["name"].lower() not in have]
                if missing:
                    category_issues.append({
                        "product_id": product.id, "sku": product.sku, "title": product.title,
                        "category_id": product.category_id, "missing": missing,
                    })
        except ebay_api.EbayAPIError:
            checked_categories = False

    # Clean import, nothing to flag -> keep the fast path, just flash and move on.
    if not skipped_rows and not category_issues and not no_category_products and not rows_with_overflow:
        parts = []
        if added:
            parts.append(f"imported {added} new product(s)")
        if updated:
            parts.append(f"updated {updated} existing product(s)")
        flash(f"Done: {' and '.join(parts) or 'nothing to do'} for \"{account.nickname}\". All good to publish.", "success")
        return redirect(url_for("products.list_products", account_id=account.id))

    return render_template(
        "upload_result.html",
        added=added,
        updated=updated,
        skipped_rows=skipped_rows,
        rows_with_overflow=rows_with_overflow,
        category_issues=category_issues,
        no_category_products=no_category_products,
        checked_categories=checked_categories,
        has_connected_account=account.is_connected,
        account=account,
    )


@products_bp.route("/<int:product_id>/quick-fix", methods=["POST"])
@login_required
def quick_fix(product_id):
    """
    Patches a single product's category_id and/or aspects right from the upload
    results page - so a flagged "missing: Size, Color" can be fixed on the spot
    instead of editing the CSV and re-uploading the whole file.
    """
    product = Product.query.get_or_404(product_id)

    new_category_id = request.form.get("category_id", "").strip()
    if new_category_id:
        product.category_id = new_category_id

    aspects_text = request.form.get("aspects_text", "").strip()
    if aspects_text:
        merged = product.get_aspects()
        merged.update(parse_aspects(aspects_text))  # new values win, old untouched keys stay
        product.set_aspects(merged)

    db.session.commit()

    # Re-check against eBay right away so the user knows immediately whether this fixed it.
    still_missing = None
    if product.category_id and product.account and product.account.is_connected:
        try:
            access_token = ebay_api.get_fresh_access_token(product.account.refresh_token)
            still_missing = ebay_api.find_missing_required_aspects(access_token, product, product.category_id)
        except ebay_api.EbayAPIError:
            still_missing = None

    if still_missing:
        flash(f'Saved "{product.title}" - still missing: {", ".join(still_missing)}.', "error")
    elif still_missing == []:
        flash(f'Saved "{product.title}" - all required fields are present now.', "success")
    else:
        flash(f'Saved "{product.title}".', "success")

    return redirect(request.referrer or url_for("products.list_products"))


@products_bp.route("/<int:product_id>/delete", methods=["POST"])
@login_required
def delete(product_id):
    product = Product.query.get_or_404(product_id)
    db.session.delete(product)
    db.session.commit()
    flash("Product removed.", "success")
    return redirect(url_for("products.list_products"))


@products_bp.route("/bulk-delete", methods=["POST"])
@login_required
def bulk_delete():
    product_ids = request.form.getlist("product_ids")
    if not product_ids:
        flash("Select at least one product to delete.", "error")
        return redirect(url_for("products.list_products"))

    products = Product.query.filter(Product.id.in_([int(pid) for pid in product_ids])).all()
    count = len(products)
    for product in products:
        db.session.delete(product)
    db.session.commit()
    flash(f"Removed {count} product(s).", "success")
    return redirect(url_for("products.list_products"))
