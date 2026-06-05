from __future__ import annotations
import base64
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
import pandas as pd

DEFAULT_DB_PATH = Path(os.getenv("LOOKUP_DB_PATH", "lookups.sqlite3"))
DEFAULT_CSV_PATH = Path(os.getenv("LOOKUP_SEED_CSV", "Pres_parametre_Master_dosya.csv"))
DEFAULT_COLOR_CATALOG_PATH = Path(os.getenv("LOOKUP_COLOR_CATALOG", "data/renk_katalog_seed.txt"))

# Varsayılan kalınlıklar (mm)
DEFAULT_THICKNESS_MM: list[float] = [
    2.7,
    4.0,
    5.0,
    6.0,
    7.7,
    8.0,
    10.0,
    12.0,
    14.0,
    15.0,
    15.5,
    16.0,
    16.5,
    17.0,
    18.0,
    22.0,
    25.0,
    30.0,
]

# Varsayılan pres plaka yüzeyleri
DEFAULT_PRES_PLAKA_YUZEY: list[str] = [
    "AHŞAP",
    "AHŞAP/DERİN",
    "AHŞAP/NATUREL",
    "BUTE",
    "BUTE/DERİN",
    "BUTE/PARLAK",
    "DERİN",
    "DERİN/BUTE",
    "DERİN/FREZE",
    "DERİN/NATUREL",
    "DERİN/PARLAK",
    "DİNAMİK",
    "DÜZ",
    "DÜZ/PARLAK",
    "FREZE",
    "FREZE/DERİN",
    "FREZE/TEKSTİL",
    "GÖLGE",
    "KEPİ",
    "KEPİ/AHŞAP",
    "KEPİ/BUTE",
    "KEPİ/DÜZ",
    "KEPİ/NATUREL",
    "NATUREL",
    "NATUREL/DÜZ",
    "NATUREL/PARLAK",
    "PARLAK",
    "PARLAK/DERİN",
    "PARLAK/FREZE",
    "PARLAK/SÜPER MAT",
    "SENKRON",
    "SÜPER MAT",
    "SÜPER MAT/BUTE",
    "SÜPER MAT/FREZE",
    "SÜPER MAT/PARLAK",
    "TEKSTİL",
    "TEKSTİL/NATUREL",
    "TEKSTİL/SÜPER MAT",
    "TİMBERLAND",
    "WOOD",
    "WOOD/AHŞAP",
    "WOOD/NATUREL",
    "WOOD/SÜPER MAT",
]

@dataclass(frozen=True)
class LookupSeedResult:
    created_db: bool
    seeded_from_csv: bool
    rows_inserted: int

# DB bağlantısı
def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn

# Şemayı oluştur
def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA journal_mode=WAL;
        PRAGMA foreign_keys=ON;

        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        -- NOT: Kalınlık ondalıklı olabilir (örn. 2.7mm)
        CREATE TABLE IF NOT EXISTS ham_levha_kalinlik (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kalinlik_mm REAL NOT NULL UNIQUE,
            aktif INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS renk_deger (
            renk_deger TEXT PRIMARY KEY,
            aktif INTEGER NOT NULL DEFAULT 1
        );

        -- Eşleme tablosu: kod -> ad 
        CREATE TABLE IF NOT EXISTS renk_katalog (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            renk_deger TEXT NOT NULL,
            renk_adi TEXT NOT NULL,
            aktif INTEGER NOT NULL DEFAULT 1,
            UNIQUE(renk_deger, renk_adi)
        );

        CREATE TABLE IF NOT EXISTS kagit_renk (
            kagit_renk TEXT PRIMARY KEY,
            aktif INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS pres_plaka_yuzey (
            pres_plaka_yuzey TEXT PRIMARY KEY,
            aktif INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS melamin_hatti (
            melamin_hatti TEXT PRIMARY KEY,
            sira INTEGER,
            aktif INTEGER NOT NULL DEFAULT 1
        );

        -- Personel / kullanıcı tablosu (giriş ekranı için)
        CREATE TABLE IF NOT EXISTS personel (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ad_soyad TEXT NOT NULL,
            birim TEXT NOT NULL,
            kullanici_adi TEXT NOT NULL UNIQUE,
            email TEXT,
            parola_hash TEXT NOT NULL,
            parola_salt TEXT NOT NULL,
            parola_iter INTEGER NOT NULL,
            aktif INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS ix_personel_kullanici_adi ON personel(kullanici_adi);
        """
    )
    conn.commit()

# Eski şema varsa yeni şemaya taşı (hata uygulamayı durdurmasın)
def _migrate_schema(conn: sqlite3.Connection) -> None:
    try:
        info = conn.execute("PRAGMA table_info('ham_levha_kalinlik')").fetchall()
        cols = {r[1]: (r[2] or "").lower() for r in info}  # name -> type
        has_id = any(r[1] == "id" for r in info)
        if info and (not has_id) and ("kalinlik_mm" in cols):
            # Eski tablo yapısı tespit edildi.
            conn.executescript(
                """
                ALTER TABLE ham_levha_kalinlik RENAME TO ham_levha_kalinlik_old;
                CREATE TABLE ham_levha_kalinlik (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kalinlik_mm REAL NOT NULL UNIQUE,
                    aktif INTEGER NOT NULL DEFAULT 1
                );
                INSERT OR IGNORE INTO ham_levha_kalinlik(kalinlik_mm, aktif)
                SELECT CAST(kalinlik_mm AS REAL), COALESCE(aktif, 1)
                FROM ham_levha_kalinlik_old;
                DROP TABLE ham_levha_kalinlik_old;
                """
            )
            conn.commit()
    except Exception:
        pass

    # personel.email alanı migration (mevcut DB'ler için)
    try:
        info = conn.execute("PRAGMA table_info('personel')").fetchall()
        if info:
            cols = {r[1] for r in info}
            if "email" not in cols:
                conn.execute("ALTER TABLE personel ADD COLUMN email TEXT")
                conn.commit()
        # Unique index (email doluysa benzersiz olsun)
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_personel_email ON personel(email) WHERE email IS NOT NULL")
        conn.commit()
    except Exception:
        pass

    # renk_deger: INTEGER PRIMARY KEY -> TEXT primary key geçişi.
    try:
        info = conn.execute("PRAGMA table_info('renk_deger')").fetchall()
        if info:
            # Eski tabloyu tespit et: INTEGER PK.
            # PRAGMA kolonları: cid, name, type, notnull, dflt_value, pk
            name_to = {r[1]: (r[2] or "").lower() for r in info}
            pk_cols = [r for r in info if r[5] == 1]
            is_old_int_pk = (
                (len(pk_cols) == 1)
                and (pk_cols[0][1] == "renk_deger")
                and ("integer" in (name_to.get("renk_deger", "")))
            )
            if is_old_int_pk:
                conn.executescript(
                    """
                    ALTER TABLE renk_deger RENAME TO renk_deger_old;
                    CREATE TABLE renk_deger (
                        renk_deger TEXT PRIMARY KEY,
                        aktif INTEGER NOT NULL DEFAULT 1
                    );
                    INSERT OR IGNORE INTO renk_deger(renk_deger, aktif)
                    SELECT CAST(renk_deger AS TEXT), COALESCE(aktif, 1)
                    FROM renk_deger_old;
                    DROP TABLE renk_deger_old;
                    """
                )
                conn.commit()
    except Exception:
        pass

# Renk katalog seed metnini (kod + ad) ayrıştır
def _parse_color_catalog(text: str) -> list[tuple[str, str]]:
    if not text:
        return []

    # Ayraçları normalize et
    t = text.replace("\r", "\n")
    t = t.replace("\t", " ")
    parts: list[str] = []
    for line in t.split("\n"):
        parts.extend([p.strip() for p in line.split(",") if p.strip()])

    out: list[tuple[str, str]] = []
    for p in parts:
        p = re.sub(r"\s+", " ", p).strip()
        if not p:
            continue
        # İlk boşluğa göre kod + ad ayrıştır
        if " " not in p:
            continue
        code, name = p.split(" ", 1)
        code = code.strip()
        name = name.strip()
        if not name:
            continue
        if not re.match(r"^[A-Za-z]?\d+$", code):
            continue
        out.append((code.upper(), name))
    return out

# Renk değerlerini sıralamak için anahtar (örn. "M10" < "M2" < "A5" < "B3" < "X1" < "Z9" < "ZZ100" < "ABC")
def _renk_deger_sort_key(code: str) -> tuple:
    s = (code or "").strip().upper()
    m = re.match(r"^([A-Z]?)(\d+)$", s)
    if not m:
        return (2, s)
    prefix = m.group(1) or ""
    num = int(m.group(2))
    return (0 if prefix == "" else 1, prefix, num)

# Seed CSV'yi en uygun encoding ile okumayı dene
def _try_read_seed_csv(csv_path: Path) -> pd.DataFrame | None:
    if not csv_path.exists():
        return None

    # Türkçe başlıklar için en iyi çaba ile encoding denemesi.
    for enc in ("utf-8-sig", "utf-8", "cp1254", "latin1"):
        try:
            return pd.read_csv(csv_path, sep=";", encoding=enc)
        except Exception:
            continue
    return None

# Yardımcı dönüşümler
def _to_int(value) -> int | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() == "nan":
        return None

    # "898,5" veya "898.5" kabul et; ilk sayıyı ayıkla.
    s = s.replace(",", ".")
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    if not m:
        return None
    try:
        return int(float(m.group(0)))
    except Exception:
        return None

# "2.7mm" gibi değerlerden sayıyı ayıkla
def _to_float(value) -> float | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() == "nan":
        return None
    s = s.replace(",", ".")
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    if not m:
        return None
    try:
        return float(m.group(0))
    except Exception:
        return None

# "M1..M17" sırası için sıralama anahtarı
def _melamin_sort_key(line: str) -> int:
    if not line:
        return 10**9
    m = re.match(r"^\s*M\s*(\d+)\s*$", str(line), flags=re.IGNORECASE)
    if not m:
        return 10**9
    return int(m.group(1))

# Lookup DB'yi oluşturur/seed eder (gerekirse migrate)
def ensure_lookup_db(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    seed_csv_path: Path = DEFAULT_CSV_PATH,
) -> LookupSeedResult:
    created_db = not db_path.exists()
    conn = _connect(db_path)
    try:
        _init_schema(conn)
        _migrate_schema(conn)

        seeded_from_csv = False
        rows_inserted = 0

        df = _try_read_seed_csv(seed_csv_path)
        if df is not None and len(df) > 0:
            seeded_from_csv = True

            # Kalınlıkları seed et
            if "Kalınlık" in df.columns:
                vals = sorted({v for v in df["Kalınlık"].dropna().unique().tolist()})
                ins = [(f,) for f in [_to_float(v) for v in vals] if f is not None]
                before = conn.total_changes
                conn.executemany(
                    "INSERT OR IGNORE INTO ham_levha_kalinlik(kalinlik_mm) VALUES (?)",
                    ins,
                )
                rows_inserted += conn.total_changes - before

            # Renk değerlerini seed et
            if "Renk Değer" in df.columns:
                vals = sorted({v for v in df["Renk Değer"].dropna().unique().tolist()})
                ins = [(i,) for i in [_to_int(v) for v in vals] if i is not None]
                before = conn.total_changes
                conn.executemany(
                    "INSERT OR IGNORE INTO renk_deger(renk_deger) VALUES (?)",
                    ins,
                )
                rows_inserted += conn.total_changes - before

            # Kağıt renklerini seed et
            if "Kağıt Renk" in df.columns:
                vals = sorted(
                    {
                        str(v).strip()
                        for v in df["Kağıt Renk"].dropna().unique().tolist()
                        if str(v).strip()
                    }
                )
                before = conn.total_changes
                conn.executemany(
                    "INSERT OR IGNORE INTO kagit_renk(kagit_renk) VALUES (?)",
                    [(v,) for v in vals],
                )
                rows_inserted += conn.total_changes - before

            # Pres plaka yüzey değerlerini seed et
            if "Pres Plaka Yüzey" in df.columns:
                vals = sorted(
                    {
                        str(v).strip()
                        for v in df["Pres Plaka Yüzey"].dropna().unique().tolist()
                        if str(v).strip()
                    }
                )
                before = conn.total_changes
                conn.executemany(
                    "INSERT OR IGNORE INTO pres_plaka_yuzey(pres_plaka_yuzey) VALUES (?)",
                    [(v,) for v in vals],
                )
                rows_inserted += conn.total_changes - before

            # Melamin hatlarını seed et
            if "Melamin Pres Hatları" in df.columns:
                vals = sorted(
                    {
                        str(v).strip()
                        for v in df["Melamin Pres Hatları"].dropna().unique().tolist()
                        if str(v).strip()
                    },
                    key=_melamin_sort_key,
                )
                before = conn.total_changes
                conn.executemany(
                    "INSERT OR IGNORE INTO melamin_hatti(melamin_hatti, sira) VALUES (?, ?)",
                    [(v, _melamin_sort_key(v)) for v in vals],
                )
                rows_inserted += conn.total_changes - before

        # M1..M17 her zaman mevcut olsun.
        default_lines = [f"M{i}" for i in range(1, 18)]
        conn.executemany(
            "INSERT OR IGNORE INTO melamin_hatti(melamin_hatti, sira) VALUES (?, ?)",
            [(v, _melamin_sort_key(v)) for v in default_lines],
        )

        # Varsayılan yüzey listesi her zaman mevcut olsun.
        before = conn.total_changes
        surface_vals = sorted({str(v).strip() for v in DEFAULT_PRES_PLAKA_YUZEY if str(v).strip()})
        conn.executemany(
            "INSERT OR IGNORE INTO pres_plaka_yuzey(pres_plaka_yuzey) VALUES (?)",
            [(v,) for v in surface_vals],
        )
        rows_inserted += conn.total_changes - before

        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', '2')")

        # Varsayılan kalınlık listesi her zaman mevcut olsun.
        before = conn.total_changes
        conn.executemany(
            "INSERT OR IGNORE INTO ham_levha_kalinlik(kalinlik_mm) VALUES (?)",
            [(float(v),) for v in sorted(set(DEFAULT_THICKNESS_MM))],
        )
        rows_inserted += conn.total_changes - before

        conn.commit()

        # Seed dosyası varsa renk kataloğunu (renk_deger + kagit_renk + eşleme) seed et.
        try:
            if DEFAULT_COLOR_CATALOG_PATH.exists():
                text = DEFAULT_COLOR_CATALOG_PATH.read_text(encoding="utf-8")
                pairs = _parse_color_catalog(text)

                if pairs:
                    # Eşlemeyi ekle (tekrar olabilir)
                    before = conn.total_changes
                    conn.executemany(
                        "INSERT OR IGNORE INTO renk_katalog(renk_deger, renk_adi) VALUES (?, ?)",
                        [(c, n) for (c, n) in pairs],
                    )
                    rows_inserted += conn.total_changes - before

                    # Kod listesini ekle
                    before = conn.total_changes
                    conn.executemany(
                        "INSERT OR IGNORE INTO renk_deger(renk_deger) VALUES (?)",
                        [(c,) for c in sorted({c for (c, _) in pairs}, key=_renk_deger_sort_key)],
                    )
                    rows_inserted += conn.total_changes - before

                    # Ad listesini kagit_renk tablosuna ekle (mevcut tablo yeniden kullanılır)
                    before = conn.total_changes
                    conn.executemany(
                        "INSERT OR IGNORE INTO kagit_renk(kagit_renk) VALUES (?)",
                        [(n,) for n in sorted({n for (_, n) in pairs})],
                    )
                    rows_inserted += conn.total_changes - before

                    conn.commit()
        except Exception:
            pass

        return LookupSeedResult(
            created_db=created_db,
            seeded_from_csv=seeded_from_csv,
            rows_inserted=int(rows_inserted),
        )
    finally:
        conn.close()

# Lookup yardımcıları
def _fetch_list(conn: sqlite3.Connection, sql: str, params: Iterable = ()) -> list[str]:
    cur = conn.execute(sql, params)
    return [str(r[0]) for r in cur.fetchall()]

# Aktif melamin hatlarını sırayla döndür
def get_melamin_hatlari(*, db_path: Path = DEFAULT_DB_PATH) -> list[str]:
    try:
        ensure_lookup_db(db_path=db_path)
        conn = _connect(db_path)
        try:
            rows = _fetch_list(
                conn,
                "SELECT melamin_hatti FROM melamin_hatti WHERE aktif=1 ORDER BY COALESCE(sira, 999999), melamin_hatti",
            )
        finally:
            conn.close()

        if rows:
            return rows
    except Exception:
        pass

    return [f"M{i}" for i in range(1, 18)]

# Renk katalog kod-ad çiftlerini döndür
def get_renk_katalog(*, db_path: Path = DEFAULT_DB_PATH) -> list[tuple[str, str]]:
    ensure_lookup_db(db_path=db_path)
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "SELECT renk_deger, renk_adi FROM renk_katalog WHERE aktif=1"
        )
        rows = [(str(r[0]).strip(), str(r[1]).strip()) for r in cur.fetchall()]
    finally:
        conn.close()

    rows = [(c.upper(), n) for (c, n) in rows if c and n]
    rows = sorted(set(rows), key=lambda t: (_renk_deger_sort_key(t[0]), t[1]))
    return rows

# İstenen lookup tablosunun aktif değerlerini döndür
def get_lookup_values(*, table: str, db_path: Path = DEFAULT_DB_PATH) -> list[str]:
    ensure_lookup_db(db_path=db_path)
    conn = _connect(db_path)
    try:
        if table == "ham_levha_kalinlik":
            return _fetch_list(
                conn,
                "SELECT kalinlik_mm FROM ham_levha_kalinlik WHERE aktif=1 ORDER BY CAST(kalinlik_mm AS REAL)",
            )
        if table == "renk_deger":
            rows = _fetch_list(conn, "SELECT renk_deger FROM renk_deger WHERE aktif=1")
            return sorted(rows, key=_renk_deger_sort_key)
        if table == "kagit_renk":
            return _fetch_list(conn, "SELECT kagit_renk FROM kagit_renk WHERE aktif=1 ORDER BY kagit_renk")
        if table == "pres_plaka_yuzey":
            return _fetch_list(
                conn,
                "SELECT pres_plaka_yuzey FROM pres_plaka_yuzey WHERE aktif=1 ORDER BY pres_plaka_yuzey",
            )
        if table == "melamin_hatti":
            return get_melamin_hatlari(db_path=db_path)
        raise ValueError(f"Unsupported lookup table: {table}")
    finally:
        conn.close()


# -------------------------
# Personel (Auth) yardımcıları
# -------------------------

_DEFAULT_PBKDF2_ITERATIONS = int(os.getenv("AUTH_PBKDF2_ITER", "260000"))


def _normalize_username(username: str) -> str:
    return (username or "").strip().lower()


def _pbkdf2_hash_password(*, password: str, salt: bytes, iterations: int) -> str:
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        (password or "").encode("utf-8"),
        salt,
        int(iterations),
    )
    return dk.hex()


def _new_salt_bytes() -> bytes:
    return secrets.token_bytes(16)


def _salt_to_text(salt: bytes) -> str:
    return base64.b64encode(salt).decode("ascii")


def _salt_from_text(s: str) -> bytes:
    return base64.b64decode((s or "").encode("ascii"))


def count_personel(*, db_path: Path = DEFAULT_DB_PATH) -> int:
    ensure_lookup_db(db_path=db_path)
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT COUNT(1) FROM personel").fetchone()
        return int(row[0] if row else 0)
    finally:
        conn.close()


def create_personel(
    *,
    ad_soyad: str,
    birim: str,
    kullanici_adi: str,
    email: str | None = None,
    parola: str,
    db_path: Path = DEFAULT_DB_PATH,
    aktif: bool = True,
    iterations: int | None = None,
) -> int:
    """Yeni personel kaydı oluşturur. Parolayı PBKDF2 ile hashleyerek saklar."""

    ad_soyad = (ad_soyad or "").strip()
    birim = (birim or "").strip()
    kullanici_adi_n = _normalize_username(kullanici_adi)
    if not ad_soyad:
        raise ValueError("ad_soyad boş olamaz")
    if not birim:
        raise ValueError("birim boş olamaz")
    if not kullanici_adi_n:
        raise ValueError("kullanici_adi boş olamaz")
    if not (parola or "").strip():
        raise ValueError("parola boş olamaz")

    email_n: str | None = None
    if email is not None:
        e = str(email).strip().lower()
        email_n = e if e else None

    ensure_lookup_db(db_path=db_path)
    conn = _connect(db_path)
    try:
        salt = _new_salt_bytes()
        it = int(iterations or _DEFAULT_PBKDF2_ITERATIONS)
        pwd_hash = _pbkdf2_hash_password(password=parola, salt=salt, iterations=it)
        cur = conn.execute(
            """
            INSERT INTO personel(ad_soyad, birim, kullanici_adi, email, parola_hash, parola_salt, parola_iter, aktif)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ad_soyad,
                birim,
                kullanici_adi_n,
                email_n,
                pwd_hash,
                _salt_to_text(salt),
                it,
                1 if aktif else 0,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def authenticate_personel(
    *,
    kullanici_adi: str,
    parola: str,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, str] | None:
    """Kullanıcı adı veya e-posta/şifre doğruysa personel bilgilerini döndürür, değilse None."""

    raw = (kullanici_adi or "").strip()
    if not raw:
        return None

    # Kullanıcı adı normalizasyonu; e-posta girilmişse boş olabilir.
    username = _normalize_username(raw)
    email = raw.strip().lower() if ("@" in raw) else ""

    ensure_lookup_db(db_path=db_path)
    conn = _connect(db_path)
    try:
        row = None

        # 1) E-posta ile dene (kullanıcı e-posta girdiyse)
        if email:
            row = conn.execute(
                """
                SELECT id, ad_soyad, birim, kullanici_adi, email, parola_hash, parola_salt, parola_iter
                FROM personel
                WHERE lower(email) = ? AND aktif = 1
                LIMIT 1
                """,
                (email,),
            ).fetchone()

        # 2) Kullanıcı adı ile dene
        if (row is None) and username:
            row = conn.execute(
                """
                SELECT id, ad_soyad, birim, kullanici_adi, email, parola_hash, parola_salt, parola_iter
                FROM personel
                WHERE kullanici_adi = ? AND aktif = 1
                LIMIT 1
                """,
                (username,),
            ).fetchone()

        # 3) Eğer kullanıcı adı ile girildi ama aslında e-posta yazıldıysa (örn. normalize bozuldu), son kez e-posta dene
        if (row is None) and (not email):
            maybe_email = raw.strip().lower()
            if "@" in maybe_email:
                row = conn.execute(
                    """
                    SELECT id, ad_soyad, birim, kullanici_adi, email, parola_hash, parola_salt, parola_iter
                    FROM personel
                    WHERE lower(email) = ? AND aktif = 1
                    LIMIT 1
                    """,
                    (maybe_email,),
                ).fetchone()

        if not row:
            return None

        try:
            salt = _salt_from_text(str(row[6]))
            it = int(row[7])
        except Exception:
            return None

        candidate = _pbkdf2_hash_password(password=parola or "", salt=salt, iterations=it)
        if not hmac.compare_digest(candidate, str(row[5])):
            return None

        return {
            "id": str(row[0]),
            "ad_soyad": str(row[1]),
            "birim": str(row[2]),
            "kullanici_adi": str(row[3]),
            "email": str(row[4] or ""),
        }
    finally:
        conn.close()


def update_personel_profile(
    *,
    kullanici_adi: str,
    ad_soyad: str | None = None,
    birim: str | None = None,
    email: str | None = None,
    aktif: bool | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> bool:
    """Mevcut personelin profil alanlarını günceller. Kayıt yoksa False döner."""

    username = _normalize_username(kullanici_adi)
    if not username:
        return False

    ensure_lookup_db(db_path=db_path)
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT id FROM personel WHERE kullanici_adi = ? LIMIT 1",
            (username,),
        ).fetchone()
        if not row:
            return False

        fields: list[str] = []
        params: list[object] = []

        if ad_soyad is not None:
            fields.append("ad_soyad = ?")
            params.append((ad_soyad or "").strip())
        if birim is not None:
            fields.append("birim = ?")
            params.append((birim or "").strip())
        if email is not None:
            e = str(email).strip().lower()
            fields.append("email = ?")
            params.append(e if e else None)
        if aktif is not None:
            fields.append("aktif = ?")
            params.append(1 if aktif else 0)

        if not fields:
            return True

        params.append(username)
        conn.execute(
            f"UPDATE personel SET {', '.join(fields)} WHERE kullanici_adi = ?",
            tuple(params),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def set_personel_password(
    *,
    kullanici_adi: str,
    new_password: str,
    db_path: Path = DEFAULT_DB_PATH,
    iterations: int | None = None,
) -> bool:
    """Mevcut personelin şifresini günceller (hash+salt). Kayıt yoksa False döner."""

    username = _normalize_username(kullanici_adi)
    if not username:
        return False
    if not (new_password or "").strip():
        raise ValueError("new_password boş olamaz")

    ensure_lookup_db(db_path=db_path)
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT id FROM personel WHERE kullanici_adi = ? LIMIT 1",
            (username,),
        ).fetchone()
        if not row:
            return False

        salt = _new_salt_bytes()
        it = int(iterations or _DEFAULT_PBKDF2_ITERATIONS)
        pwd_hash = _pbkdf2_hash_password(password=new_password, salt=salt, iterations=it)

        conn.execute(
            """
            UPDATE personel
            SET parola_hash = ?, parola_salt = ?, parola_iter = ?
            WHERE kullanici_adi = ?
            """,
            (pwd_hash, _salt_to_text(salt), it, username),
        )
        conn.commit()
        return True
    finally:
        conn.close()