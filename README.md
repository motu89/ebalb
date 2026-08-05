# Listing Pipeline - eBay Auto-Listing Bot

A Flask app that connects to eBay's official Sell APIs to bulk-publish listings
from a CSV/Excel file across connected eBay seller accounts.

## Install

```bash
cd ebay_bot
pip install -r requirements.txt
```

## Configure `.env`

Set your Flask and eBay values in `.env`.

For local testing, your eBay RuName redirect can point to:

```text
http://localhost:5000/accounts/callback
```

Change these before any real deployment:

```text
SECRET_KEY=your-long-random-secret
ADMIN_PASSWORD=your-admin-password
TOKEN_ENCRYPTION_KEY=your-fernet-key
```

## Run

```bash
python run.py
```

Open:

```text
http://localhost:5000
```

## Super Admin

Create the first super admin once:

```bash
python -m flask --app app create-super-admin
```

The command prints a random 16-character username and a random 16-character
password. Save them immediately because the password is stored only as a hash.

This local copy already has this super-admin login in `instance/ebaybot.db`:

```text
Username: Z6vcpovQPSrAf3Cs
Password: hyTtKLpgpUXjsy8b
```

The secret super-admin URL is:

```text
http://localhost:5000/qOZKWRDXX2gsdW2p/users
```

The route key is set in `.env`:

```text
SUPER_ADMIN_ROUTE_KEY=qOZKWRDXX2gsdW2p
```

From that page, create admin accounts, choose their use period, reset their
passwords, extend their access, or expire them. Expired admins cannot log in.

If you lose the super-admin password, rotate it with:

```bash
python -m flask --app app reset-super-admin-password <super_admin_username>
```

## Privacy Policy URL

The public privacy policy page is available at:

```text
http://localhost:5000/privacy
```

This URL is also shown on the super-admin users page. For eBay production API
review, use the deployed HTTPS version of the same path, for example:

```text
https://your-domain.com/privacy
```

## Connect a Sandbox Store

1. Go to **Stores** and add a store nickname.
2. Click **Connect to eBay (sandbox)**.
3. Log in with your sandbox test user.
4. Return to the app and confirm the store shows as connected.

## Upload Products and Publish

1. Open **Products** and upload a CSV/Excel file.
2. Open **Publish**, choose the store and products.
3. Open **Listings** to review success or failure details.

## Security Notes

- Dashboard passwords are hashed.
- Normal admin users can expire.
- Super-admin access uses a secret 16-character URL path plus login.
- eBay refresh tokens are encrypted at rest with `TOKEN_ENCRYPTION_KEY`.
- Forms use CSRF protection.
- Public production deployments should use HTTPS.
