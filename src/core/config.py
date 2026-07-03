import os
from dotenv import load_dotenv

load_dotenv()

# Auth
JWT_SECRET = os.getenv("JWT_SECRET_KEY", "travelens-jwt-secret-key-2024")
JWT_EXPIRY = int(os.getenv("JWT_ACCESS_TOKEN_EXPIRES", 86400))
# Shared secret the client uses to sign a device JWT for unauthenticated
# (not-logged-in) requests. Overridable via .env; keep out of source in prod.
DEVICE_JWT_SECRET = os.getenv(
    "DEVICE_JWT_SECRET", "OQ2Igc1oi3iAHUdUSjRE4h3UadqfNnC2iVZm0i7uLQHsEQpZ05oEaApZ_0_Jw-0a"
)
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
SMTP_EMAIL = os.getenv("SMTP_EMAIL", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")

# Azure OpenAI
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")


def _normalize_azure_endpoint(raw):
    """The AzureOpenAI client expects the bare resource URL; it appends
    `/openai/deployments/...` itself. A trailing `/openai` or `/openai/v1`
    (the v1-API form) produces a doubled, malformed path -> 404. Strip it so
    either form in .env works."""
    if not raw:
        return raw
    endpoint = raw.rstrip("/")
    for suffix in ("/openai/v1", "/openai"):
        if endpoint.endswith(suffix):
            endpoint = endpoint[: -len(suffix)]
            break
    return endpoint


AZURE_OPENAI_ENDPOINT = _normalize_azure_endpoint(os.getenv("AZURE_OPENAI_ENDPOINT"))
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
AZURE_OPENAI_CHAT_DEPLOYMENT = os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT")
AZURE_OPENAI_EMBEDDING_DEPLOYMENT = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT")

# Server
PORT = int(os.environ.get("PORT", 4000))
