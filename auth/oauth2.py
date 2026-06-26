"""
auth/oauth2.py - OAuth2 SAML Bearer Token handler for SF Position Integrity Checker.

Flow:
  1. Build a signed SAML assertion XML document
  2. POST it to SF_TOKEN_URL to exchange for a Bearer token
  3. Cache the token in memory; auto-refresh on expiry
"""

import base64
import os
import uuid
from datetime import UTC, datetime, timedelta

import requests

# ---------------------------------------------------------------------------
# Module-level token cache
# ---------------------------------------------------------------------------

_cached_token: str | None = None
_token_expiry: datetime | None = None


def _load_required_vars() -> dict:
    """Load and validate required OAuth2 env vars. Raises ValueError if any are missing."""
    required = {
        "SF_CLIENT_ID": os.environ.get("SF_CLIENT_ID", ""),
        "SF_COMPANY_ID": os.environ.get("SF_COMPANY_ID", ""),
        "SF_USER_ID": os.environ.get("SF_USER_ID", ""),
        "SF_TOKEN_URL": os.environ.get("SF_TOKEN_URL", ""),
        "SF_PRIVATE_KEY_PATH": os.environ.get("SF_PRIVATE_KEY_PATH", ""),
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise ValueError(
            f"OAuth2 is configured (SF_AUTH_METHOD=oauth2) but the following "
            f"required environment variables are not set: {', '.join(missing)}\n"
            f"Set them in your .env file or environment before running the tool."
        )
    return required


def _build_saml_assertion(vars: dict) -> bytes:
    """
    Construct and sign a SAML assertion XML document.
    Returns the signed XML as bytes.
    """
    try:
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
        from lxml import etree
        from signxml import XMLSigner
    except ImportError as exc:
        raise ImportError(
            f"OAuth2 requires additional packages. "
            f"Run: pip install lxml signxml cryptography\n"
            f"Missing: {exc}"
        ) from exc

    key_path = vars["SF_PRIVATE_KEY_PATH"]
    if not os.path.exists(key_path):
        raise FileNotFoundError(
            f"Private key file not found: {key_path}\n"
            f"Check SF_PRIVATE_KEY_PATH in your .env file and ensure the file exists."
        )

    with open(key_path, "rb") as fh:
        private_key = load_pem_private_key(fh.read(), password=None)

    now = datetime.now(tz=UTC)
    not_before = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    not_after = (now + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assertion_id = f"_{uuid.uuid4()}"

    # XML namespaces
    SAML = "urn:oasis:names:tc:SAML:2.0:assertion"  # noqa: N806
    nsmap = {"saml": SAML}

    assertion = etree.Element(f"{{{SAML}}}Assertion", nsmap=nsmap)
    assertion.set("ID", assertion_id)
    assertion.set("Version", "2.0")
    assertion.set("IssueInstant", not_before)

    issuer = etree.SubElement(assertion, f"{{{SAML}}}Issuer")
    issuer.text = vars["SF_CLIENT_ID"]

    subject = etree.SubElement(assertion, f"{{{SAML}}}Subject")
    name_id = etree.SubElement(subject, f"{{{SAML}}}NameID")
    name_id.set("Format", "urn:oasis:names:tc:SAML:1.1:nameid-format:unspecified")
    name_id.text = vars["SF_USER_ID"]

    subj_conf = etree.SubElement(subject, f"{{{SAML}}}SubjectConfirmation")
    subj_conf.set("Method", "urn:oasis:names:tc:SAML:2.0:cm:bearer")

    conditions = etree.SubElement(assertion, f"{{{SAML}}}Conditions")
    conditions.set("NotBefore", not_before)
    conditions.set("NotOnOrAfter", not_after)

    audience_restriction = etree.SubElement(conditions, f"{{{SAML}}}AudienceRestriction")
    audience = etree.SubElement(audience_restriction, f"{{{SAML}}}Audience")
    audience.text = vars["SF_TOKEN_URL"]

    authn_stmt = etree.SubElement(assertion, f"{{{SAML}}}AuthnStatement")
    authn_stmt.set("AuthnInstant", not_before)
    authn_context = etree.SubElement(authn_stmt, f"{{{SAML}}}AuthnContext")
    authn_context_class = etree.SubElement(authn_context, f"{{{SAML}}}AuthnContextClassRef")
    authn_context_class.text = "urn:oasis:names:tc:SAML:2.0:ac:classes:PasswordProtectedTransport"

    signer = XMLSigner(
        method=XMLSigner.excl_c14n,
        signature_algorithm="rsa-sha256",
        digest_algorithm="sha256",
    )
    signed_root = signer.sign(
        assertion,
        key=private_key,
        reference_uri=f"#{assertion_id}",
    )
    return etree.tostring(signed_root, xml_declaration=False, encoding="unicode").encode("utf-8")


def _fetch_token(vars: dict) -> tuple:
    """
    Exchange a SAML assertion for a Bearer token.
    Returns (access_token, expires_in_seconds).
    """
    signed_xml = _build_saml_assertion(vars)
    # base64url encoding (no padding) as required by the OAuth2 SAML bearer spec
    assertion_b64 = base64.urlsafe_b64encode(signed_xml).decode("utf-8")

    data = {
        "grant_type": "urn:ietf:params:oauth:grant-type:saml2-bearer",
        "client_id": vars["SF_CLIENT_ID"],
        "company_id": vars["SF_COMPANY_ID"],
        "assertion": assertion_b64,
    }

    resp = requests.post(vars["SF_TOKEN_URL"], data=data, timeout=30)
    if resp.status_code != 200:
        body_snippet = resp.text[:500]
        raise RuntimeError(
            f"OAuth2 token request failed - HTTP {resp.status_code}\nResponse: {body_snippet}"
        )

    payload = resp.json()
    access_token = payload.get("access_token")
    expires_in = int(payload.get("expires_in", 86400))

    if not access_token:
        raise RuntimeError(
            f"OAuth2 token response did not contain 'access_token'.\nResponse: {str(payload)[:500]}"
        )

    return access_token, expires_in


def get_oauth2_headers() -> dict:
    """
    Return Authorization header dict with a valid Bearer token.
    Handles token caching - reuses a valid token until it expires.
    On expiry, automatically re-fetches a new token.
    """
    global _cached_token, _token_expiry

    now = datetime.now(tz=UTC)
    if _cached_token and _token_expiry and now < _token_expiry:
        return {"Authorization": f"Bearer {_cached_token}"}

    vars = _load_required_vars()
    access_token, expires_in = _fetch_token(vars)

    # Cache with a 60-second safety buffer
    _cached_token = access_token
    _token_expiry = now + timedelta(seconds=expires_in - 60)

    return {"Authorization": f"Bearer {_cached_token}"}
