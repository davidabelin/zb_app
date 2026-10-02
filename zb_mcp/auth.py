"""Google identity, encrypted distributed OAuth state, and account authorization."""

from typing import Any

from cryptography.fernet import Fernet
from fastmcp.server.auth.providers.google import GoogleProvider
from key_value.aio.stores.firestore import (
    FirestoreStore,
    FirestoreV1CollectionSanitizationStrategy,
    FirestoreV1KeySanitizationStrategy,
)
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper
from key_value.aio.wrappers.prefix_collections import PrefixCollectionsWrapper

from zb_services import ServiceError
from .settings import Settings


def principal(token: Any, allowed_emails: frozenset[str]) -> str:
    """Require a verified, allowlisted identity and return its stable Google subject."""
    claims = getattr(token, "claims", {}) or {}
    verified = claims.get("email_verified")
    if (verified is not True and verified != "true") or not claims.get("sub"):
        raise ServiceError("forbidden", "A verified Google identity is required.", 403)
    if str(claims.get("email", "")).strip().lower() not in allowed_emails:
        raise ServiceError(
            "forbidden", "This Google account is not authorized for Zenbot.", 403
        )
    return str(claims["sub"])


def provider(settings: Settings) -> GoogleProvider:
    """Construct maintained OAuth with separately namespaced, encrypted Firestore storage."""
    store = FirestoreStore(
        project=settings.project,
        key_sanitization_strategy=FirestoreV1KeySanitizationStrategy(),
        collection_sanitization_strategy=FirestoreV1CollectionSanitizationStrategy(),
    )
    storage = FernetEncryptionWrapper(
        key_value=PrefixCollectionsWrapper(
            store, prefix=f"{settings.namespace}_oauth_"
        ),
        fernet=Fernet(settings.encryption_key.encode("ascii")),
    )
    return GoogleProvider(
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        base_url=settings.base_url,
        required_scopes=["openid", "https://www.googleapis.com/auth/userinfo.email"],
        jwt_signing_key=settings.signing_key,
        client_storage=storage,
        require_authorization_consent=True,
    )
