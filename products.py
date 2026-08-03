import pandas as pd
from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required

from extensions import db
from models import Product

products_bp = Blueprint("products", __name__, url_prefix="/products")

REQUIRED_COLUMNS = {"sku", "title", "price"}
OPTIONAL_COLUMNS = ["description", "quantity", "category_id", "brand", "condition", "image_url"]


@products_bp.route("/")
@login_required
def list_products():
    products = Product.query.order_by(Product.created_at.desc()).all()
    return render_template("products.html", products=products)


@products_bp.route("/upload", methods=["GET", "POST"])
@login_required
def upload():
    if request.method == "GET":
        return render_template("upload.html")

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
    skipped = 0
    for _, row in df.iterrows():
        sku = str(row.get("sku", "")).strip()
        title = str(row.get("title", "")).strip()
        if not sku or not title or pd.isna(row.get("price")):
            skipped += 1
            continue

        product = Product(
            sku=sku,
            title=title,
            description=str(row["description"]) if "description" in df.columns and not pd.isna(row.get("description")) else None,
            price=float(row["price"]),
            quantity=int(row["quantity"]) if "quantity" in df.columns and not pd.isna(row.get("quantity")) else 1,
            category_id=str(row["category_id"]) if "category_id" in df.columns and not pd.isna(row.get("category_id")) else None,
            brand=str(row["brand"]) if "brand" in df.columns and not pd.isna(row.get("brand")) else None,
            condition=str(row["condition"]).upper() if "condition" in df.columns and not pd.isna(row.get("condition")) else "NEW",
            image_url=str(row["image_url"]) if "image_url" in df.columns and not pd.isna(row.get("image_url")) else None,
        )
        db.session.add(product)
        added += 1

    db.session.commit()
    flash(f"Imported {added} product(s)." + (f" Skipped {skipped} row(s) missing required data." if skipped else ""),
          "success")
    return redirect(url_for("products.list_products"))


@products_bp.route("/<int:product_id>/delete", methods=["POST"])
@login_required
def delete(product_id):
    product = Product.query.get_or_404(product_id)
    db.session.delete(product)
    db.session.commit()
    flash("Product removed.", "success")
    return redirect(url_for("products.list_products"))
