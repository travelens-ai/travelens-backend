import logging
logging.getLogger("opentelemetry.sdk.trace.export").setLevel(logging.ERROR)

try:
    from langfuse import Langfuse
    from core.config import LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST, APP_ENV
    if LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY:
        _langfuse = Langfuse(
            public_key=LANGFUSE_PUBLIC_KEY,
            secret_key=LANGFUSE_SECRET_KEY,
            host=LANGFUSE_HOST,
            environment=APP_ENV,
        )
    else:
        _langfuse = None
except ImportError:
    _langfuse = None


def get_langfuse():
    return _langfuse
