try:
    from langfuse import get_client as _get_client
    _langfuse = _get_client()
except ImportError:
    _langfuse = None


def get_langfuse():
    return _langfuse
