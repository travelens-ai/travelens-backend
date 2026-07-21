import logging
# Langfuse tracing is best-effort; suppress OTLP batch-export warnings to reduce noise.
logging.getLogger("opentelemetry.sdk.trace.export").setLevel(logging.ERROR)

try:
    from langfuse import get_client as _get_client
    from core.config import LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY
    if LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY:
        _langfuse = _get_client()
    else:
        _langfuse = None
except ImportError:
    _langfuse = None


def get_langfuse():
    return _langfuse
