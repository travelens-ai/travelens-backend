import re

from core.db import fetch_dicts


def get_images_for_places(system, names):
    """Return {lowercased place name -> [image_name, ...]} for the given place names."""
    names = [str(n).strip().lower() for n in names if str(n).strip()]
    if not names:
        return {}
    try:
        placeholders = ",".join(["?"] * len(names))
        rows = fetch_dicts(
            f"""SELECT LOWER(COALESCE(p.display_name, p.name)) AS display, LOWER(p.name) AS canonical, i.image_name AS image
                FROM places p
                JOIN place_image_map pim ON pim.place_id = p.id
                JOIN images i ON pim.image_id = i.id
                WHERE LOWER(p.display_name) IN ({placeholders})
                   OR (p.display_name IS NULL AND LOWER(p.name) IN ({placeholders}))
                ORDER BY CASE WHEN i.image_name LIKE '%\\_0.webp' ESCAPE '\\' THEN 0 ELSE 1 END""",
            tuple(names) + tuple(names),
        )
    except Exception as e:
        print(f"  _get_images_for_places failed ({e})")
        return {}

    result = {}
    for row in rows:
        for key in {row["display"], row["canonical"]}:
            result.setdefault(key, [])
            if row["image"] and row["image"] not in result[key]:
                result[key].append(row["image"])
    return result


def search_images_by_keywords(system, keywords, limit=5):
    """Fallback image lookup by keyword against image_name column."""
    found = []
    for raw in keywords:
        if len(found) >= limit:
            break
        kw = str(raw).strip() if raw is not None else ""
        if not kw:
            continue
        token = kw.lower().replace(" ", "_")
        escaped = token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        try:
            rows = fetch_dicts(
                "SELECT TOP (?) image_name FROM images WHERE LOWER(image_name) LIKE ? "
                "ORDER BY CASE WHEN image_name LIKE '%\\_0.webp' ESCAPE '\\' THEN 0 ELSE 1 END, id",
                (limit, "%" + escaped + "%"),
            )
        except Exception as e:
            print(f"  _search_images_by_keywords failed for {kw!r} ({e})")
            continue
        for row in rows:
            name = row["image_name"]
            if name and name not in found:
                found.append(name)
                if len(found) >= limit:
                    break
    return found


def search_image_by_keywords(system, keywords):
    """Single-image variant — returns first match or None."""
    images = search_images_by_keywords(system, keywords, limit=1)
    return images[0] if images else None


def _candidate_city_keys(placename: str) -> list:
    """Return a list of city name strings to try, most specific first."""
    name = placename.strip()
    candidates = [name, name.lower()]
    # "Leh-Ladakh" -> "leh"
    for sep in (" and ", "-", ","):
        if sep in name.lower():
            parts = re.split(re.escape(sep), name, flags=re.IGNORECASE)
            for p in parts:
                p = p.strip()
                if p:
                    candidates.extend([p, p.lower()])
    # deduplicate preserving order
    seen = set()
    result = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            result.append(c)
    return result


def get_city_image(system, placename: str, state: str = "") -> str:
    """Return CDN filename for the top-rated place in the given city, or ''.

    Checks _CITY_IMAGE_OVERRIDE first (curated fixes for known bad DB images),
    then queries the DB, then falls back to a keyword LIKE search on image_name.
    """
    # Check curated override first — avoids wrong-city images in DB
    try:
        from features.places.service import _CITY_IMAGE_OVERRIDE
        for key in _candidate_city_keys(placename):
            override = _CITY_IMAGE_OVERRIDE.get(key.lower())
            if override:
                return override
    except Exception:
        pass

    _SQL = (
        "SELECT TOP 1 i.image_name "
        "FROM images i "
        "JOIN place_image_map pim ON pim.image_id = i.id "
        "JOIN places p            ON pim.place_id  = p.id "
        "JOIN cities c            ON p.city_id     = c.id "
        "WHERE LOWER(c.name) = LOWER(?) "
        "ORDER BY "
        "  CASE WHEN i.image_name LIKE '%\\_0.webp' ESCAPE '\\' THEN 0 ELSE 1 END, "
        "  COALESCE(p.rating, 0) DESC, "
        "  COALESCE(p.num_ratings, 0) DESC"
    )
    for key in _candidate_city_keys(placename):
        try:
            rows = fetch_dicts(_SQL, (key,))
            if rows:
                return rows[0]["image_name"]
        except Exception as e:
            print(f"[get_city_image] DB error for '{key}': {e}")

    # Fallback: LIKE search on image filenames using placename + state
    keywords = [placename]
    if state:
        keywords.append(state)
    img = search_image_by_keywords(system, keywords)
    return img or ""
