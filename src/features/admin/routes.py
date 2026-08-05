"""Admin resource CRUD routes.

Registers a uniform set of endpoints per entry in the resource registry:
    GET    /admin/<slug>          list  (?page & ?limit & ?search)  -> {data:[...],total,page,limit}
    GET    /admin/<slug>/<id>     get   -> {data:{...}} | 404
    POST   /admin/<slug>          create-> {data:{...}} (201) | 400
    PUT    /admin/<slug>/<id>     update-> {data:{...}} | 404
    DELETE /admin/<slug>/<id>     delete-> {message} (200) | 404

Only the verbs listed in each resource's `methods` are registered (e.g. feedback
gets list/get/delete, no create/update). Every route is protected by
`admin_required`. Errors use the panel's uniform {"message": ...} shape.

Every JSON response carries a top-level boolean `status` (true on 2xx, false
otherwise), injected by `add_status` (see below). Single rows are nested under
`data` so a resource's own `status` column (e.g. itineraries.status) is never
shadowed by the injected top-level flag.
"""
from flask import Blueprint, request, jsonify

from core.db import is_db_ready
from auth.admin.jwt_utils import admin_required
from features.admin import service
from features.admin.resources import RESOURCES
from features.messaging import service as messaging_service
import features.places.service as places_service

admin_bp = Blueprint("admin", __name__)

_MAX_LIMIT = 100
_DEFAULT_LIMIT = 20


@admin_bp.before_request
def check_db_ready():
    if not is_db_ready():
        return jsonify({"message": "Database is connecting, please try again shortly"}), 503


@admin_bp.after_request
def add_status(response):
    """Inject a top-level boolean `status` (true on 2xx) into every JSON dict
    response. Mirrors the app-wide server_ip injector in app.py. Leaves the
    nested `data` payload untouched, so a row's own `status` column survives."""
    if response.content_type and "application/json" in response.content_type:
        data = response.get_json(silent=True)
        if isinstance(data, dict) and "status" not in data:
            data["status"] = 200 <= response.status_code < 300
            response.data = jsonify(data).data
    return response


def _parse_paging():
    """page (>=1) and limit (1.._MAX_LIMIT) from the query string, clamped."""
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    try:
        limit = int(request.args.get("limit", _DEFAULT_LIMIT))
    except (TypeError, ValueError):
        limit = _DEFAULT_LIMIT
    limit = max(1, min(limit, _MAX_LIMIT))
    return page, limit


def _parse_bool(value):
    """Tri-state parse of a query param: None when absent/unrecognized, else the
    bool. 'true'/'1'/'yes' → True; 'false'/'0'/'no' → False (case-insensitive)."""
    if value is None:
        return None
    v = value.strip().lower()
    if v in ("true", "1", "yes"):
        return True
    if v in ("false", "0", "no"):
        return False
    return None


# --- Swagger specs -----------------------------------------------------------
# Flasgger only documents a view whose __doc__ has a YAML `---` block. The
# registry-driven views below are generated in factories, so we build each one's
# spec from the slug and attach it as __doc__ (see _register). Without this, only
# the hand-written auth routes would appear in /docs.
def _tag(slug):
    return "Admin: " + slug.replace("-", " ").title()


def _spec_list(slug, config):
    body_param = ""
    return (
        f"List {slug}\n---\n"
        f"tags:\n  - {_tag(slug)}\n"
        f"security:\n  - Bearer: []\n"
        f"parameters:\n"
        f"  - in: query\n    name: page\n    type: integer\n    default: 1\n"
        f"  - in: query\n    name: limit\n    type: integer\n    default: 20\n    description: 1..100\n"
        f"  - in: query\n    name: search\n    type: string\n    description: case-insensitive match on {', '.join(config['search']) or 'n/a'}\n"
        f"responses:\n"
        f"  200:\n    description: 'Paged list: {{data:[...], total, page, limit, status}}'\n"
        f"  401:\n    description: Missing or invalid admin token\n"
    ) + body_param


def _spec_get(slug):
    return (
        f"Get one {slug} by id\n---\n"
        f"tags:\n  - {_tag(slug)}\n"
        f"security:\n  - Bearer: []\n"
        f"parameters:\n  - in: path\n    name: row_id\n    type: integer\n    required: true\n"
        f"responses:\n  200:\n    description: '{{data:{{...}}, status:true}}'\n"
        f"  404:\n    description: Not found\n  401:\n    description: Missing or invalid admin token\n"
    )


def _spec_body(slug, config):
    props = "\n".join(f"            {c}:\n              type: string" for c in config["writable"])
    return (
        f"parameters:\n"
        f"  - in: body\n    name: body\n    required: true\n    schema:\n"
        f"        type: object\n        properties:\n{props}\n"
    )


def _spec_create(slug, config):
    return (
        f"Create a {slug}\n---\n"
        f"tags:\n  - {_tag(slug)}\n"
        f"security:\n  - Bearer: []\n"
        f"{_spec_body(slug, config)}"
        f"responses:\n  201:\n    description: '{{data:{{...}}, status:true}}'\n"
        f"  400:\n    description: No writable fields provided\n  401:\n    description: Missing or invalid admin token\n"
    )


def _spec_update(slug, config):
    return (
        f"Update a {slug} by id\n---\n"
        f"tags:\n  - {_tag(slug)}\n"
        f"security:\n  - Bearer: []\n"
        f"parameters:\n  - in: path\n    name: row_id\n    type: integer\n    required: true\n"
        f"  - in: body\n    name: body\n    required: true\n    schema:\n"
        f"        type: object\n        properties:\n"
        + "\n".join(f"            {c}:\n              type: string" for c in config["writable"]) + "\n"
        f"responses:\n  200:\n    description: '{{data:{{...}}, status:true}}'\n"
        f"  404:\n    description: Not found\n  401:\n    description: Missing or invalid admin token\n"
    )


def _spec_delete(slug):
    return (
        f"Delete a {slug} by id\n---\n"
        f"tags:\n  - {_tag(slug)}\n"
        f"security:\n  - Bearer: []\n"
        f"parameters:\n  - in: path\n    name: row_id\n    type: integer\n    required: true\n"
        f"responses:\n  200:\n    description: '{{message:\"Deleted\", status:true}}'\n"
        f"  404:\n    description: Not found\n  401:\n    description: Missing or invalid admin token\n"
    )


def _make_list(config, slug):
    # A resource may override the default list query (e.g. feedback joins in
    # user + itinerary details) by naming a function on the service module.
    list_fn = getattr(service, config["list_fn"]) if config.get("list_fn") else service.list_rows

    @admin_required
    def _list():
        page, limit = _parse_paging()
        search = request.args.get("search")
        result, (_status, msg, code) = list_fn(
            config, page=page, limit=limit, search=search
        )
        if result is not None:
            return jsonify(result), code
        return jsonify({"message": msg}), code
    _list.__doc__ = _spec_list(slug, config)
    return _list


def _make_get(config, slug):
    @admin_required
    def _get(row_id):
        result, (_status, msg, code) = service.get_row(config, row_id)
        if result is not None:
            return jsonify({"data": result}), code
        return jsonify({"message": msg}), code
    _get.__doc__ = _spec_get(slug)
    return _get


def _make_create(config, slug):
    @admin_required
    def _create():
        result, (_status, msg, code) = service.create_row(config, request.json or {})
        if result is not None:
            return jsonify({"data": result}), code
        return jsonify({"message": msg}), code
    _create.__doc__ = _spec_create(slug, config)
    return _create


def _make_update(config, slug):
    @admin_required
    def _update(row_id):
        result, (_status, msg, code) = service.update_row(config, row_id, request.json or {})
        if result is not None:
            return jsonify({"data": result}), code
        return jsonify({"message": msg}), code
    _update.__doc__ = _spec_update(slug, config)
    return _update


def _make_delete(config, slug):
    @admin_required
    def _delete(row_id):
        ok, (_status, msg, code) = service.delete_row(config, row_id)
        if ok:
            # 200 (not 204) so the response can carry {message, status}.
            return jsonify({"message": "Deleted"}), 200
        return jsonify({"message": msg}), code
    _delete.__doc__ = _spec_delete(slug)
    return _delete


# --- place images (explicit routes, not registry-driven) --------------------
@admin_bp.route("/admin/place-images", methods=["GET"])
@admin_required
def place_images_list():
    """List the top 100 un-moderated place images (joined to their place)
    ---
    tags:
      - Admin: Place Images
    security:
      - Bearer: []
    parameters:
      - in: query
        name: search
        type: string
        description: case-insensitive match on image name or place name
    responses:
      200:
        description: >
          At most 100 rows: {data:[{image_id,image_name,image_url,source,created_at,moderated,place}], total, page, limit, status}
      401:
        description: Missing or invalid admin token
    """
    # Fixed top-100 window (no pagination) — the review queue only needs the
    # most recent batch to work through at a time.
    search = request.args.get("search")
    result, (_status, msg, code) = service.list_place_images(
        page=1, limit=100, search=search, moderated=False
    )
    if result is not None:
        return jsonify(result), code
    return jsonify({"message": msg}), code


@admin_bp.route("/admin/place-images/all", methods=["GET"])
@admin_required
def place_images_list_all():
    """List ALL place images (moderated and un-moderated), paged
    ---
    tags:
      - Admin: Place Images
    security:
      - Bearer: []
    parameters:
      - in: query
        name: page
        type: integer
        default: 1
      - in: query
        name: limit
        type: integer
        default: 20
        description: 1..100
      - in: query
        name: moderated
        type: boolean
        description: >
          true → only moderated images; false → only un-moderated;
          omit → all images
      - in: query
        name: search
        type: string
        description: case-insensitive match on image name or place name
    responses:
      200:
        description: >
          {data:[{image_id,image_name,image_url,source,created_at,moderated,place}], total, page, limit, status}
      401:
        description: Missing or invalid admin token
    """
    page, limit = _parse_paging()
    search = request.args.get("search")
    moderated = _parse_bool(request.args.get("moderated"))
    result, (_status, msg, code) = service.list_place_images(
        page=page, limit=limit, search=search, moderated=moderated
    )
    if result is not None:
        return jsonify(result), code
    return jsonify({"message": msg}), code


@admin_bp.route("/admin/place-images/moderate", methods=["POST"])
@admin_required
def place_images_moderate():
    """Mark place images moderated (removes them from the review queue)
    ---
    tags:
      - Admin: Place Images
    security:
      - Bearer: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [image_ids]
          properties:
            image_ids:
              type: array
              items:
                type: integer
              example: [36080, 36081]
            moderated:
              type: boolean
              default: true
              description: set false to move images back into the queue
    responses:
      200:
        description: >
          {data:{updated:[ids], not_found:[ids], moderated:bool, count}, status:true}
      400:
        description: image_ids must be a non-empty list of integers
      401:
        description: Missing or invalid admin token
    """
    data = request.json or {}
    moderated = data.get("moderated", True)
    result, (_status, msg, code) = service.set_images_moderated(data.get("image_ids"), moderated)
    if result is not None:
        return jsonify({"data": result}), code
    return jsonify({"message": msg}), code


@admin_bp.route("/admin/place-images/bulk-delete", methods=["POST"])
@admin_required
def place_images_bulk_delete():
    """Bulk-delete place images by id (DB + place_image_map + disk)
    ---
    tags:
      - Admin: Place Images
    security:
      - Bearer: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [image_ids]
          properties:
            image_ids:
              type: array
              items:
                type: integer
              example: [36080, 36081, 36082]
    responses:
      200:
        description: >
          {data:{deleted:[{image_id,image_name,file_removed}], not_found:[ids], count}, status:true}
      400:
        description: image_ids must be a non-empty list of integers
      401:
        description: Missing or invalid admin token
    """
    data = request.json or {}
    result, (_status, msg, code) = service.bulk_delete_place_images(data.get("image_ids"))
    if result is not None:
        return jsonify({"data": result}), code
    return jsonify({"message": msg}), code


@admin_bp.route("/admin/place-images/<int:image_id>", methods=["DELETE"])
@admin_required
def place_images_delete(image_id):
    """Delete one image from the DB (images + place_image_map) and disk
    ---
    tags:
      - Admin: Place Images
    security:
      - Bearer: []
    parameters:
      - in: path
        name: image_id
        type: integer
        required: true
    responses:
      200:
        description: '{data:{image_id,image_name,file_removed}, status:true}'
      404:
        description: Not found
      401:
        description: Missing or invalid admin token
    """
    result, (_status, msg, code) = service.delete_place_image(image_id)
    if result is not False:
        return jsonify({"data": result}), code
    return jsonify({"message": msg}), code


@admin_bp.route("/admin/generate-notification-copy", methods=["POST"])
@admin_required
def generate_notification_copy():
    """Generate a push-notification title and body from a prompt using AI

    Admin helper: given a short campaign description, returns AI-written
    `title` and `body` plus a target `type`, a https://travelens.in/ deep-link
    `link`, and a banner `image` URL — all for the admin to review/edit before
    sending via /send-notification. Does not send anything.
    ---
    tags:
      - Admin: Notifications
    security:
      - Bearer: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [prompt]
          properties:
            prompt:
              type: string
              description: What the notification is about (campaign/intent)
              example: "Weekend getaway deals near Goa, 30% off hotels"
            tone:
              type: string
              description: Optional style hint (e.g. exciting, urgent, friendly)
              example: "exciting"
    responses:
      200:
        description: >
          {data:{title, body, type, link, image}, status:true} — type is the
          target screen, link is a https://travelens.in/... deep link, image is
          a banner URL.
      400:
        description: Missing prompt
      502:
        description: Model returned empty/malformed copy
      401:
        description: Missing or invalid admin token
    """
    data = request.json or {}
    result, (_status, msg, code) = messaging_service.generate_notification_copy(
        prompt=data.get("prompt"),
        tone=data.get("tone"),
    )
    if result is not None:
        return jsonify({"data": result}), code
    return jsonify({"message": msg}), code


# --- destination moderation (popular / trending) -----------------------------
def _moderation_cities(data):
    """Pull the city list from a remove request body, accepting either a `cities`
    list or a single `city` string."""
    cities = data.get("cities")
    if cities is None and data.get("city"):
        cities = [data.get("city")]
    if isinstance(cities, str):
        cities = [cities]
    return cities or []


@admin_bp.route("/admin/moderate-destinations", methods=["GET"])
@admin_required
def moderate_destinations_get():
    """Review the draft popular/trending destinations before publishing

    Returns the draft set for the given `type` (seeding it from the currently
    published set, or the curated defaults, on first open). This is the working
    copy the admin edits with /remove and then /submit — it is NOT what the
    client sees until submitted.
    ---
    tags:
      - Admin: Destinations
    security:
      - Bearer: []
    parameters:
      - in: query
        name: type
        type: string
        required: true
        enum: [popular, trending]
    responses:
      200:
        description: '{data:{type, destinations:[...]}, status:true}'
      400:
        description: Invalid or missing type
      401:
        description: Missing or invalid admin token
    """
    dtype = (request.args.get("type") or "").strip().lower()
    try:
        result = places_service.get_moderation_draft(dtype)
    except ValueError as e:
        return jsonify({"message": str(e)}), 400
    except Exception as e:
        return jsonify({"message": str(e)}), 500
    return jsonify({"data": result}), 200


@admin_bp.route("/admin/moderate-destinations/remove", methods=["POST"])
@admin_required
def moderate_destinations_remove():
    """Remove one or more destinations from a draft; each freed slot is auto-filled

    Removing a city from the draft immediately backfills the slot with the next
    unused city from the curated pool, so the set stays full. Edits stay in the
    draft until /submit. Accepts `cities` (list) or a single `city`.
    ---
    tags:
      - Admin: Destinations
    security:
      - Bearer: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [type, cities]
          properties:
            type:
              type: string
              enum: [popular, trending]
            cities:
              type: array
              items:
                type: string
              example: ["Panaji", "Jaipur"]
            city:
              type: string
              description: Single city alternative to `cities`
    responses:
      200:
        description: '{data:{type, destinations:[...]}, status:true} — refreshed draft'
      400:
        description: Invalid type or empty cities
      404:
        description: None of the given cities are in the draft
      401:
        description: Missing or invalid admin token
    """
    data = request.json or {}
    dtype = (data.get("type") or "").strip().lower()
    try:
        result, (_status, msg, code) = places_service.remove_from_draft(
            dtype, _moderation_cities(data)
        )
    except ValueError as e:
        return jsonify({"message": str(e)}), 400
    except Exception as e:
        return jsonify({"message": str(e)}), 500
    if result is not None:
        return jsonify({"data": result}), code
    return jsonify({"message": msg}), code


@admin_bp.route("/admin/moderate-destinations/submit", methods=["POST"])
@admin_required
def moderate_destinations_submit():
    """Publish the draft so the client starts serving it

    Copies the current draft for `type` to the published set. After this,
    GET /places?type=<type> returns exactly this set, in this order.
    ---
    tags:
      - Admin: Destinations
    security:
      - Bearer: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [type]
          properties:
            type:
              type: string
              enum: [popular, trending]
    responses:
      200:
        description: '{data:{type, destinations:[...]}, status:true} — now live'
      400:
        description: Invalid type or no draft to publish
      401:
        description: Missing or invalid admin token
    """
    data = request.json or {}
    dtype = (data.get("type") or "").strip().lower()
    try:
        result, (_status, msg, code) = places_service.publish_moderation(dtype)
    except ValueError as e:
        return jsonify({"message": str(e)}), 400
    except Exception as e:
        return jsonify({"message": str(e)}), 500
    if result is not None:
        return jsonify({"data": result}), code
    return jsonify({"message": msg}), code


def _register(slug, config):
    """Wire the enabled verbs for one resource onto the blueprint. Endpoint
    names are namespaced by slug so Flask sees no collisions."""
    methods = config["methods"]
    base = f"/admin/{slug}"
    key = slug.replace("-", "_")

    if "list" in methods:
        admin_bp.add_url_rule(base, f"{key}_list", _make_list(config, slug), methods=["GET"])
    if "create" in methods:
        admin_bp.add_url_rule(base, f"{key}_create", _make_create(config, slug), methods=["POST"])
    if "get" in methods:
        admin_bp.add_url_rule(f"{base}/<int:row_id>", f"{key}_get", _make_get(config, slug), methods=["GET"])
    if "update" in methods:
        admin_bp.add_url_rule(f"{base}/<int:row_id>", f"{key}_update", _make_update(config, slug), methods=["PUT"])
    if "delete" in methods:
        admin_bp.add_url_rule(f"{base}/<int:row_id>", f"{key}_delete", _make_delete(config, slug), methods=["DELETE"])


for _slug, _config in RESOURCES.items():
    _register(_slug, _config)
