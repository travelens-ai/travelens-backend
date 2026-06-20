"""Ad configuration and helpers for injecting ad slots into list responses.

One place defines every ad unit, its type and dimensions, which pages show a
sticky (anchor) banner, and which pages show an interstitial. The client reads
`ads` from /configs to know the page-level behavior, and renders the inline
`{"type": "ad", ...}` items that the APIs interleave into their lists.

Ad types:
  - Google network ads: `banner`, `native`, `sticky_banner`, `interstitial`.
    These carry `refreshInterval` (seconds) and `refreshCount` (max reloads).
  - `direct`: a self-served creative — carries `imageUrl` or `videoUrl` and a
    `ctaUrl` to open on tap, and does NOT refresh.
"""

# Default refresh behavior for Google network ads.
DEFAULT_REFRESH_INTERVAL = 30   # seconds between refreshes
DEFAULT_REFRESH_COUNT = 10      # max number of refreshes before stopping

# Ad types that are served by Google's network (and therefore refresh). The
# `direct` type is self-served and never refreshes.
GOOGLE_ADTYPES = {"banner", "native", "sticky_banner", "interstitial"}

# ---------------------------------------------------------------------------
# Ad unit definitions — change ad unit IDs / sizes here only.
# ---------------------------------------------------------------------------

# Inline (in-list) ad slots, keyed by placement. Each placement controls how
# often an ad is injected into a list via `every` (insert one ad after every N
# real items).
INLINE_ADS = {
    "popular_states": {
        "adunit": "ca-app-pub-3940256099942544/6300978111",
        "adtype": "banner",
        "width": 320,
        "height": 100,
        "every": 6,
    },
    "favorites": {
        "adunit": "ca-app-pub-3940256099942544/6300978111",
        "adtype": "native",
        "width": 320,
        "height": 250,
        "every": 2,
    },
    "history": {
        "adunit": "ca-app-pub-3940256099942544/6300978111",
        "adtype": "native",
        "width": 320,
        "height": 250,
        "every": 4,
    },
    # Itinerary detail page — between the places / accommodations / restaurants
    # sections within each day.
    "itinerary_section": {
        "adunit": "ca-app-pub-3940256099942544/6300978111",
        "adtype": "banner",
        "width": 320,
        "height": 100,
        "every": 1,
    },
    # Example self-served direct ad — image creative with a click-through URL.
    "sponsored": {
        "adtype": "direct",
        "width": 320,
        "height": 250,
        "imageUrl": "https://travelens.in/app/ads/sponsored-default.jpg",
        "videoUrl": "",
        "ctaUrl": "https://travelens.in",
        "every": 5,
    },
}

# Page-level ad behavior surfaced through /configs.
#   sticky        — pages that show a persistent anchor/banner ad
#   interstitial  — pages that show a full-screen interstitial on entry/exit
STICKY_ADS = {
    "ITINERARY": {
        "adunit": "ca-app-pub-3940256099942544/2934735716",
        "adtype": "sticky_banner",
        "width": 320,
        "height": 50,
    },
}

INTERSTITIAL_ADS = {
    "ITINERARY": {
        "adunit": "ca-app-pub-3940256099942544/1033173712",
        "adtype": "interstitial",
        "width": 0,
        "height": 0,
    },
}


def _ad_fields(cfg):
    """Build the common ad fields from a config dict, adding type-specific keys:
      - Google network ads -> refreshInterval / refreshCount
      - direct ads         -> imageUrl / videoUrl / ctaUrl (no refresh)
    """
    adtype = cfg.get("adtype")
    fields = {
        "adtype": adtype,
        "width": cfg.get("width", 0),
        "height": cfg.get("height", 0),
    }
    if adtype == "direct":
        # Self-served creative — image or video plus a click-through URL.
        fields["imageUrl"] = cfg.get("imageUrl", "")
        fields["videoUrl"] = cfg.get("videoUrl", "")
        fields["ctaUrl"] = cfg.get("ctaUrl", "")
    else:
        # Google network ad — carries the ad unit and refresh behavior.
        fields["adunit"] = cfg.get("adunit", "")
        if adtype in GOOGLE_ADTYPES:
            fields["refreshInterval"] = cfg.get("refreshInterval", DEFAULT_REFRESH_INTERVAL)
            fields["refreshCount"] = cfg.get("refreshCount", DEFAULT_REFRESH_COUNT)
    return fields


def _ad_item(placement):
    """Return a single inline ad slot dict for the given placement (without the
    `every` control key), or None if the placement is unknown."""
    cfg = INLINE_ADS.get(placement)
    if not cfg:
        return None
    return {"type": "ad", "placement": placement, **_ad_fields(cfg)}


def section_ad(placement="itinerary_section"):
    """Return a single inline ad slot for use between sections (e.g. between the
    places / accommodations / restaurants blocks on the itinerary page)."""
    return _ad_item(placement)


def interleave_ads(items, placement):
    """Return a new list with ad slots inserted after every N real items, where
    N is the placement's `every`. Ads are not placed after the final item.
    Falls back to the original list when the placement is unknown."""
    cfg = INLINE_ADS.get(placement)
    if not cfg or not items:
        return list(items)
    every = max(1, int(cfg.get("every", 4)))

    out = []
    for i, item in enumerate(items):
        out.append(item)
        # Insert an ad after every `every` items, but not trailing the last one.
        if (i + 1) % every == 0 and (i + 1) < len(items):
            out.append(_ad_item(placement))
    return out


def get_ads_config():
    """The `ads` block served via /configs: only page-level sticky/interstitial
    behavior. Inline (in-list) slot configs are NOT included here — they travel
    with the content responses that actually carry the ads (see
    `get_inline_ads_config`)."""
    return {
        "sticky": {
            page: _ad_fields(cfg) for page, cfg in STICKY_ADS.items()
        },
        "interstitial": {
            page: _ad_fields(cfg) for page, cfg in INTERSTITIAL_ADS.items()
        },
    }


def get_inline_ads_config(*placements):
    """Return the inline ad slot definitions for the given placement(s), keyed
    by placement. Attach this to a content API response (generate-itinerary,
    favorites, history, ...) so the client gets the slot config alongside the
    ads that were interleaved into the content. Unknown placements are skipped.
    With no placements, returns all inline configs."""
    keys = placements or tuple(INLINE_ADS.keys())
    out = {}
    for placement in keys:
        cfg = INLINE_ADS.get(placement)
        if cfg:
            out[placement] = {**_ad_fields(cfg), "every": cfg.get("every", 4)}
    return out
