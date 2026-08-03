# Listing Pipeline — eBay Auto-Listing Bot

A Flask app that connects to eBay's official Sell APIs (Inventory API) to bulk-publish
listings from a CSV/Excel file, across as many connected eBay accounts as you need.

## What's included
- Dashboard login (your own username/password, separate from eBay)
- "Stores" page — connect eBay seller accounts via OAuth once, refresh tokens stored encrypted
- CSV/Excel upload for bulk product import
- Publish flow — pick a store + products, publishes via the Inventory API (item → offer → publish)
- Listings log — every publish attempt with status and error detail
- eBay Marketplace Account Deletion webhook, ready for when you move to production

## 1. Install

```bash
cd ebay_bot
pip install -r requirements.txt
```

## 2. Configure `.env`

A `.env` file is already set up with your **Sandbox** App ID, Dev ID, and Cert ID.
Two things you still need to fill in before connecting a store:

### a) Set a RuName (redirect URL)
1. Go to developer.ebay.com → Application Keys → your Sandbox keyset → click **User Tokens**
2. Under "Your eBay Redirect URL (RuName)", either use an existing one or create a new one
   - For local testing, point it at `http://localhost:5000/accounts/callback`
     (eBay sandbox does allow `localhost` redirects for testing — production will need a real HTTPS domain, e.g. your Railway URL, later)
3. Copy the **RuName value** (looks like `Your_Name-YourApp-SBX-abc123`) into `.env`:
   ```
   EBAY_RUNAME=Your_Name-YourApp-SBX-abc123
   ```

### b) Change the default admin password and secret key
Open `.env` and replace:
```
SECRET_KEY=... (any long random string)
ADMIN_PASSWORD=... (whatever you want to log into the dashboard with)
```

## 3. Run it

```bash
python run.py
```

Visit `http://localhost:5000`, log in with your `ADMIN_USERNAME` / `ADMIN_PASSWORD` from `.env`.

## 4. Connect a sandbox store
1. Go to **Stores** → add a store (give it any nickname)
2. Click **Connect to eBay (sandbox)**
3. Log in with your **Sandbox test user** (the `TESTUSER_...` account, not your real eBay login)
4. You'll be redirected back, and the store will show "Connected" with its business policies auto-filled
5. If policies show "none found," your sandbox test user needs payment/return/fulfillment
   policies set up once in Seller Hub (sandbox) first — this is normal for a brand-new test user

## 5. Set a ship-from location
Each connected store needs a **merchant location key** before you can publish — this is a
location you set up once via eBay's Account API (or Seller Hub in sandbox). Enter that key
on the Stores page once you have it.

## 6. Upload products and publish
1. **Products → Upload CSV** — use `sample_products.csv` in this folder as a template
2. **Publish** — pick the store, pick the products, hit Publish
3. **Listings** — see status per item, with error detail if anything failed

## Security notes (already built in)
- Refresh tokens are encrypted at rest (Fernet) using `TOKEN_ENCRYPTION_KEY` — never stored in plain text
- CSRF protection on every form (Flask-WTF)
- Dashboard requires login — nothing is publicly viewable without your admin password
- App ID / Cert ID / Dev ID live only in `.env`, which is not meant to be committed to git or shared

**Before you deploy this anywhere public:** change `SECRET_KEY`, `ADMIN_PASSWORD`, and
generate a fresh `TOKEN_ENCRYPTION_KEY` — don't reuse the ones in this starter `.env`.

## Moving to production later
1. Create a **Production** keyset on the developer portal
2. Complete the **Marketplace Account Deletion** step — this app's webhook is already built at
   `/webhook/ebay-account-deletion`; you just need it reachable over HTTPS (Railway gives you this)
   and to set a matching `VERIFICATION_TOKEN` in `webhook.py`
3. Swap `.env`: `EBAY_ENV=production`, plus your production App ID / Dev ID / Cert ID / RuName
4. Re-authorize each store — sandbox and production tokens are separate

## Project structure
```
ebay_bot/
├── app.py            # App factory, wires everything together
├── config.py          # Reads .env
├── extensions.py       # db, login manager, token encryption
├── models.py           # AdminUser, EbayAccount, Product, Listing
├── ebay_api.py          # All direct eBay API calls (OAuth + Inventory API)
├── auth.py               # Dashboard login/logout
├── main.py                # Dashboard/overview
├── accounts.py             # Connect/manage eBay stores
├── products.py              # CSV upload, product list
├── listings.py               # Publish + listing status
├── webhook.py                 # eBay account deletion notification (for production)
├── templates/                  # All pages
├── static/                      # CSS/JS
└── sample_products.csv           # Example CSV format
```
