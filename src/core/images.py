import copy

# Public base for serving stored image files. Bare image names (e.g.
# "Amer_Fort_...webp", "default3.webp") are turned into absolute URLs at the
# API response boundary; the DB keeps storing bare names.
IMAGE_BASE_URL = "https://travelens.in/app/generated_images/"


def _to_url(name):
    """Prepend the base URL to a bare image name. Values that are already
    absolute URLs (e.g. Pexels/Unsplash results, or already-prefixed) are
    returned unchanged so we never double-prepend."""
    if not isinstance(name, str) or not name:
        return name
    if name.startswith("http://") or name.startswith("https://"):
        return name
    return IMAGE_BASE_URL + name


def _walk(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "image" and isinstance(value, str):
                node[key] = _to_url(value)
            elif key == "images" and isinstance(value, list):
                node[key] = [_to_url(v) if isinstance(v, str) else v for v in value]
            else:
                _walk(value)
    elif isinstance(node, list):
        for item in node:
            _walk(item)


def with_image_urls(obj):
    """Return a deep copy of `obj` with every `image` string and `images` list
    entry rewritten to a full https://travelens.in/app/generated_images/<name>
    URL. The input is not mutated, so cached/stored payloads keep bare names."""
    cloned = copy.deepcopy(obj)
    _walk(cloned)
    return cloned
