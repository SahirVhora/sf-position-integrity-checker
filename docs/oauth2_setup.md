# OAuth2 Setup Guide

This guide walks you through configuring OAuth2 SAML Bearer Token authentication
for the SF Position Integrity Checker. OAuth2 is more secure than Basic Auth —
no passwords are stored, tokens auto-refresh, and it is required by many enterprise
security policies.

---

## Step 1 — Generate an RSA key pair

Generate a 2048-bit RSA private key and extract the public key. The public key is
uploaded to SF Admin; the private key stays on your machine and is **never committed
to version control**.

```bash
# Create a secure directory outside your project
mkdir -p ~/.sf_keys

# Generate private key
openssl genrsa -out ~/.sf_keys/sf_private_key.pem 2048

# Extract public key (you'll need this for SF Admin)
openssl rsa -in ~/.sf_keys/sf_private_key.pem -pubout -out ~/.sf_keys/sf_public_key.pem

# Restrict file permissions
chmod 600 ~/.sf_keys/sf_private_key.pem
chmod 644 ~/.sf_keys/sf_public_key.pem
```

---

## Step 2 — Register the OAuth2 Client in SF Admin

1. Log in to your SAP SuccessFactors tenant as an admin.
2. Navigate to: **Admin Center → Tools → OAuth2 Client Applications**.
3. Click **Register Client Application**.
4. Fill in the fields:

   | Field | Value |
   |-------|-------|
   | Application Name | `SF Position Integrity Checker` (or any descriptive name) |
   | Application URL | `https://localhost` (placeholder — not used for SAML flow) |
   | X.509 Certificate | Paste the contents of `~/.sf_keys/sf_public_key.pem` |

5. Click **Register**. SF will auto-generate a **Client ID** — copy it.

> **Note:** The X.509 Certificate field expects the full PEM block including
> `-----BEGIN PUBLIC KEY-----` and `-----END PUBLIC KEY-----`.

---

## Step 3 — Configure your .env file

Copy `.env.example` to `.env` and fill in the OAuth2 section:

```env
SF_AUTH_METHOD=oauth2

SF_CLIENT_ID=abc123def456              # The Client ID from SF Admin (Step 2)
SF_COMPANY_ID=mycompany                # Your SF company/tenant ID
SF_USER_ID=api_technical_user          # The SF user the token is issued for
SF_TOKEN_URL=https://api4.successfactors.com/oauth/token
SF_PRIVATE_KEY_PATH=/home/yourname/.sf_keys/sf_private_key.pem

SF_ODATA_BASE_URL=https://api4.successfactors.com/odata/v2/
```

> Replace `api4` with the data centre code for your tenant
> (e.g. `api2`, `api4`, `api8`, `apisalesdemo`, etc.).
> Check your tenant URL to confirm the correct subdomain.

---

## Step 4 — Test the connection

Run the tool and select a lightweight extract to confirm the auth flow works:

```bash
python main.py --country CA
# Select [3] Only Extract when prompted
# Watch for "Auth: OAuth2 SAML Bearer Token" in the startup banner
```

A successful run will show the banner with `Auth: OAuth2 SAML Bearer Token` and
fetch data without any 401 or 403 errors.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `401 Unauthorized` | Client ID mismatch or wrong token URL | Verify `SF_CLIENT_ID` matches the value in SF Admin; check `SF_TOKEN_URL` data centre code |
| `invalid_grant` in response | SAML assertion time skew | Ensure your system clock is accurate (`timedatectl status` on Linux) |
| `FileNotFoundError: Private key file not found` | Wrong path in `SF_PRIVATE_KEY_PATH` | Run `ls -l "$SF_PRIVATE_KEY_PATH"` to verify the file exists at that path |
| `403 Forbidden` | API user lacks Role-Based Permissions | Ask your SF admin to grant the technical user OData API permissions in RBP |
| `ValueError: ... required environment variables are not set` | Missing `.env` entries | Ensure all five OAuth2 vars are present and non-empty in `.env` |
| `ImportError: ... lxml / signxml / cryptography` | Missing dependencies | Run `pip install -r requirements.txt` |
