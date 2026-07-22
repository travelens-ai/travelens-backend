from contextlib import contextmanager

try:
    from langfuse import get_client as _lf_get_client
    _LF_AVAILABLE = True
except ImportError:
    _LF_AVAILABLE = False
    def _lf_get_client():
        return None


@contextmanager
def lf_span(name, **kwargs):
    if _LF_AVAILABLE:
        with _lf_get_client().start_as_current_observation(name=name, **kwargs):
            yield
    else:
        yield


def lf_update_span(**kwargs):
    if _LF_AVAILABLE:
        _lf_get_client().update_current_span(**kwargs)
