"""Read-only audit: print places with < 5 images, grouped by count."""
import struct, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

import pyodbc
from azure.identity import DefaultAzureCredential

_SQL_COPT_SS_ACCESS_TOKEN = 1256


def connect():
    credential = DefaultAzureCredential()
    token = credential.get_token("https://database.windows.net//.default")
    token_bytes = token.token.encode("utf-16-le")
    token_struct = struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)
    conn_str = (
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER={os.getenv('AZURE_SQL_SERVER')};"
        f"DATABASE={os.getenv('AZURE_SQL_DATABASE')};"
        "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=60;"
    )
    return pyodbc.connect(conn_str, attrs_before={_SQL_COPT_SS_ACCESS_TOKEN: token_struct})


conn = connect()
cur = conn.cursor()

# --- Distribution ---
cur.execute("""
    SELECT img_count, COUNT(*) AS num_places
    FROM (
        SELECT p.id, COUNT(pim.image_id) AS img_count
        FROM places p
        LEFT JOIN place_image_map pim ON pim.place_id = p.id
        GROUP BY p.id
    ) t
    GROUP BY img_count
    ORDER BY img_count
""")
rows = cur.fetchall()
print("=== Places by image count ===")
print(f"{'Images':>8}  {'# Places':>10}")
total_missing = 0
for img_count, num_places in rows:
    flag = "  <-- needs fill" if img_count < 5 else ""
    print(f"{img_count:>8}  {num_places:>10}{flag}")
    if img_count < 5:
        total_missing += num_places
print(f"\nTotal with < 5 images: {total_missing}")

# --- Per-place detail for those with < 5 ---
cur.execute("""
    SELECT p.id, p.name, c.name AS city, s.name AS state, p.type,
           COUNT(pim.image_id) AS img_count
    FROM places p
    LEFT JOIN cities c ON p.city_id = c.id
    LEFT JOIN states s ON c.state_id = s.id
    LEFT JOIN place_image_map pim ON pim.place_id = p.id
    GROUP BY p.id, p.name, c.name, s.name, p.type
    HAVING COUNT(pim.image_id) < 5
    ORDER BY COUNT(pim.image_id) ASC, p.id DESC
""")
detail_rows = cur.fetchall()
print(f"\n=== Per-place detail (< 5 images) ===")
print(f"{'id':>6}  {'imgs':>4}  {'name':<40}  {'city':<22}  {'state':<22}  type")
print("-" * 120)
for r in detail_rows:
    pid, name, city, state, ptype, ic = r
    print(f"{pid:>6}  {ic:>4}  {(name or ''):.<40}  {(city or ''):.<22}  {(state or ''):.<22}  {ptype or ''}")

conn.close()
