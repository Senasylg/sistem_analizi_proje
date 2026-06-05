# Bu script, lookup DB'yi başlatır ve melamin hatlarını listeler. Genellikle uygulama başlatılmadan önce çalıştırılır.
from __future__ import annotations
from lookup_db import DEFAULT_CSV_PATH, DEFAULT_DB_PATH, ensure_lookup_db, get_melamin_hatlari

# Bu script, lookup DB'yi başlatır ve melamin hatlarını listeler. Genellikle uygulama başlatılmadan önce veya yeni bir ortamda çalıştırılır.
def main() -> None:
    res = ensure_lookup_db(db_path=DEFAULT_DB_PATH, seed_csv_path=DEFAULT_CSV_PATH)
    lines = get_melamin_hatlari(db_path=DEFAULT_DB_PATH)

    print(f"DB: {DEFAULT_DB_PATH.resolve()}")
    print(f"Seed CSV: {DEFAULT_CSV_PATH.resolve() if DEFAULT_CSV_PATH.exists() else DEFAULT_CSV_PATH}")
    print(f"Created DB: {res.created_db}")
    print(f"Seeded from CSV: {res.seeded_from_csv}")
    print(f"Rows inserted (best-effort): {res.rows_inserted}")
    print(f"Melamin hatlari: {', '.join(lines)}")


if __name__ == "__main__":
    main()
