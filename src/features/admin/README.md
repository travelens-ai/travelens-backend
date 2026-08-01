# TraveLens Admin API

Admin panel backend, built **inside the main Flask app** as blueprints — not a
separate service. It operates on the app's **existing** Azure SQL tables; the
only new table is `admin_users` (admin auth, isolated from app users).

- Auth blueprint: `src/auth/admin/` (mounted `/admin/auth/*`)
- Resource CRUD blueprint: `src/features/admin/` (mounted `/admin/<resource>`)
- Registered in `src/app.py`; served by the same gunicorn process.

## Setup

Run the one-time migration (needs `az login`), then start the app normally:

```bash
venv/bin/python migrations/create_admin_users_table.py   # creates admin_users + seeds admin
PYTHONPATH=src venv/bin/python -m src.app                 # or the /run skill
```

Seeded admin (override via `ADMIN_SEED_EMAIL` / `ADMIN_SEED_NAME`) — **OTP login, no password**:

```
email: travelens.ai@gmail.com
```

To add more admins, insert a row into `admin_users` (name, email, role='admin');
no password is needed.

## Auth contract — email OTP (no password)

1. `POST /admin/auth/request-otp` `{email}` → emails a 6-digit code (10-min TTL).
   Registered active admin → `{"message":"An OTP send successfully","status":true}`;
   unknown/inactive email → `{"message":"invalid email id","status":false}` (both 200).
   An OTP is only generated/sent for a real active admin.
2. `POST /admin/auth/verify-otp` `{email, otp}` → `{ "token": "<jwt>", "admin": {id,name,email,role,status} }`.
   The OTP is single-use (burned on success). Wrong/expired → **400 `{message}`**.
3. `GET /admin/auth/me` → the admin profile. Requires `Authorization: Bearer <token>`.

- Reuses the app's existing `otp_verifications` table + `auth.email_utils`, with
  a dedicated `admin_login` purpose so admin codes never collide with app-user
  signup/reset codes.
- Every other `/admin/*` route requires the admin Bearer token;
  missing/invalid/expired → **401 `{message}`**.
- Admin tokens carry `scope:"admin"` + `admin_id` + `status` (tier), so an
  app-user JWT (same secret) cannot be replayed against admin routes, and vice
  versa.
- `/admin/*` is exempt from the global app-user/device guard (`auth/guard.py`);
  admin auth is enforced by `auth.admin.admin_required` instead.

## Admin management (super-admin only for writes)

Admins have a **tier** in `admin_users.status` (migration
`add_status_to_admin_users.py`): `admin` (default) or `super admin`. The seeded
`travelens.ai@gmail.com` is `super admin`. Only super admins may add/delete
admins; any authenticated admin may list them. The tier rides in the JWT
(`status` claim) and is also re-checked against the live DB on write routes, so a
demoted admin loses write access immediately.

```
GET    /admin/admins                    # any admin
→ { "data": [ {id, name, email, role, status} ], "status": true }

POST   /admin/admins                    # super admin only
       { "name": "New Admin", "email": "new@x.com", "status": "admin" }  # status optional, default 'admin'
→ { "data": {id, name, email, role, status}, "status": true }   # 201

DELETE /admin/admins/{admin_id}         # super admin only
→ { "message": "Deleted", "status": true }
```

- New admins log in via the same email-OTP flow — no password.
- Guards: can't delete your own account; can't delete the last super admin;
  duplicate email / bad `status` / missing fields → 400; non-super admin → 403.

## Resource contract

**Every** admin JSON response carries a top-level boolean `status` (`true` on
2xx, `false` otherwise) so the client can branch on one field.

List endpoints:

```
GET /admin/<resource>?page=1&limit=20&search=<q>
→ { "data": [...], "total": <int>, "page": <int>, "limit": <int>, "status": true }
```

- `search` is case-insensitive across each resource's main text columns.
- `page` is 1-based; `limit` clamped to 1..100 (default 20).
- CRUD: `GET /<r>/{id}` → `{data:{...},status:true}` (404 if missing),
  `POST /<r>` → `{data:{...},status:true}` (201), `PUT /<r>/{id}` →
  `{data:{...},status:true}` (404 if missing), `DELETE /<r>/{id}` →
  `{message:"Deleted",status:true}` (**200**).
- A single row is nested under `data`, so a resource's own `status` column
  (e.g. `itineraries.status`) is never shadowed by the top-level flag.
- Errors are always `{ "message": "...", "status": false }` with 400/401/404.
- **Responses expose the real DB columns as-is** (snake_case) — no invented
  fields. `users` never returns `password_hash`/`reset_token`.

## Resources (map to existing tables)

| Slug | Table | Verbs |
|---|---|---|
| `food-preferences` | food_preferences | full CRUD |
| `group-types` | group_types | full CRUD |
| `states` | states | full CRUD |
| `activities` | activities | full CRUD |
| `users` | users | full CRUD (secrets hidden) |
| `places` | places | full CRUD (Google-synced cols read-only) |
| `itineraries` | itineraries | full CRUD |
| `feedback` | feedback | list / get / **delete only** (no create/update) |

`feedback` list is enriched: each row joins in the author (`user`: id, name,
email, phone) and referenced `itinerary` (id, status, request_json,
response_json, created_at) as nested objects, `null` when absent. The join casts
`feedback.user_id` (nvarchar) to int to match `users.id`, and both sides are LEFT
joins so feedback with no user/itinerary still appears. `search` matches the
feedback columns only.

Writable columns per resource are whitelisted in `resources.py`; any other key
in a request body is ignored (also the SQL-injection guard — table/column names
come only from that trusted registry, all values are parameterized).

Not yet included: `tabs`, `pages`, `ads`, `notifications` — no backing table
exists. Add a table + a `resources.py` entry to enable them.

## Place images (explicit routes, not registry-driven)

Images live in the `images` table, linked to places via `place_image_map`, with
the file stored under `generated_images/` (also served from the CDN at
`https://travelens.in/app/generated_images/<name>`).

This is a **moderation review queue**: `GET` returns only images whose
`moderated` flag is `0` (unreviewed). Marking an image moderated removes it from
the queue.

```
GET    /admin/place-images?search=<q>   # top 100 moderated=0 images
→ { "data": [ {
      "image_id": 36080, "image_name": "...webp",
      "image_url": "https://travelens.in/app/generated_images/...webp",
      "source": "pexels", "created_at": "...", "moderated": false,
      "place": { "id": 7644, "name": "..." }   // null if the place is missing
    } ], "total": <int>, "page": <int>, "limit": <int>, "status": true }

GET    /admin/place-images/all?page=1&limit=20&search=<q>   # ALL images (moderated + not)
→ same row shape as above; paged (limit 1..100). `moderated` is true/false per row.

POST   /admin/place-images/moderate   { "image_ids": [36080, 36081], "moderated": true }
→ { "data": { "updated": [36080, 36081], "not_found": [], "moderated": true, "count": 2 },
    "status": true }        // moderated defaults true; send false to requeue. 400 if ids bad

DELETE /admin/place-images/{image_id}
→ { "data": { "image_id": 36080, "image_name": "...webp", "file_removed": true },
    "status": true }        // 404 {message,status:false} if the id is unknown

POST   /admin/place-images/bulk-delete   { "image_ids": [36080, 36081, 36082] }
→ { "data": {
      "deleted":   [ { "image_id": 36080, "image_name": "...webp", "file_removed": true }, ... ],
      "not_found": [ 36082 ],   // ids that didn't exist — reported, not fatal
      "count": 2
    }, "status": true }         // 400 if image_ids is missing/empty/non-integer
```

- Returns at most **100** rows (newest first); `total` still reports the full
  queue size. There's no pagination — as images are moderated/deleted they leave
  the queue and the next batch surfaces. `search` matches the image name or the
  place name (case-insensitive).
- The `moderated` BIT column on `images` (migration
  `add_moderated_to_images.py`, default 0) drives the queue. `POST .../moderate`
  flips it for one or many ids; unknown ids are returned in `not_found` without
  failing the batch.
- DELETE removes the image everywhere: its `place_image_map` links, the `images`
  row, and the file on disk. `file_removed` is `false` when the file was already
  gone (served only from the CDN) — the DB row is the source of truth, so this is
  not an error. Mirrors `scripts/delete_image.py`.
- Bulk delete runs the same removal per id in one transaction; ids are de-duped,
  and unknown ids are returned in `not_found` instead of failing the batch.

## Endpoint count

`3 auth (request-otp, verify-otp, me) + 3 admin mgmt (list/add/delete admins)
+ (7 resources × 5 CRUD) + (feedback: list/get/delete = 3)
+ 5 place-images (list top-100, list all, moderate, bulk-delete, delete) = 49`.

## Quick smoke test

```bash
# 1. request an OTP (check the admin's inbox, or server logs if SMTP is unset)
curl -s localhost:8000/admin/auth/request-otp \
  -H 'Content-Type: application/json' \
  -d '{"email":"travelens.ai@gmail.com"}'

# 2. verify it to get a token
TOKEN=$(curl -s localhost:8000/admin/auth/verify-otp \
  -H 'Content-Type: application/json' \
  -d '{"email":"travelens.ai@gmail.com","otp":"123456"}' \
  | python -c 'import sys,json;print(json.load(sys.stdin)["token"])')

curl -s localhost:8000/admin/auth/me -H "Authorization: Bearer $TOKEN"
curl -s "localhost:8000/admin/users?page=1&limit=20&search=a" -H "Authorization: Bearer $TOKEN"
curl -s -o /dev/null -w '%{http_code}\n' localhost:8000/admin/users   # 401
```
