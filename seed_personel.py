from __future__ import annotations
import os
from dataclasses import dataclass

# Personel tablosunu seed verisiyle doldurmak içindir.
from lookup_db import (
    DEFAULT_DB_PATH,
    create_personel,
    ensure_lookup_db,
    set_personel_password,
    update_personel_profile,
)

# Kullanıcı adı benzersizdir. Aynı kullanıcı adıyla kayıt varsa güncelleme yapılır.
@dataclass(frozen=True)
class SeedPersonel:
    ad_soyad: str
    birim: str
    kullanici_adi: str
    email: str

# Script olarak çalıştırıldığında, personel tablosunu seed verisiyle doldurur.
def main() -> None:
    default_password = os.getenv("PERSONEL_DEFAULT_PASSWORD", "1234")

    people: list[SeedPersonel] = [
        SeedPersonel(
            ad_soyad="Sena Şaylıg",
            birim="Üretim",
            kullanici_adi="sena.saylig",
            email="sena.saylig@starwood.com.tr",
        ),
        SeedPersonel(
            ad_soyad="Kübra Aydın",
            birim="Kalite",
            kullanici_adi="kubra.aydin",
            email="kubra.aydin@starwood.com.tr",
        ),
        SeedPersonel(
            ad_soyad="Evin Demir",
            birim="Planlama",
            kullanici_adi="evin.demir",
            email="evin.demir@starwood.com.tr",
        ),
        SeedPersonel(
            ad_soyad="Barış Kaya",
            birim="Bakım",
            kullanici_adi="baris.kaya",
            email="baris.kaya@starwood.com.tr",
        ),
    ]

    ensure_lookup_db(db_path=DEFAULT_DB_PATH)

# Kayıt ekle, varsa güncelle. Parola sıfırlanır.
    inserted = 0
    updated = 0
    for p in people:
        try:
            create_personel(
                ad_soyad=p.ad_soyad,
                birim=p.birim,
                kullanici_adi=p.kullanici_adi,
                email=p.email,
                parola=default_password,
                db_path=DEFAULT_DB_PATH,
            )
            inserted += 1
        except Exception:
            # Kayıt varsa profil+şifre güncelle
            ok = update_personel_profile(
                kullanici_adi=p.kullanici_adi,
                ad_soyad=p.ad_soyad,
                birim=p.birim,
                email=p.email,
                aktif=True,
                db_path=DEFAULT_DB_PATH,
            )
            # Kayıt yoksa hata var demektir. Parola sıfırlanır.
            if ok:
                set_personel_password(
                    kullanici_adi=p.kullanici_adi,
                    new_password=default_password,
                    db_path=DEFAULT_DB_PATH,
                )
                updated += 1

    print(f"Seed complete. Inserted={inserted}, updated={updated}")
    print("Default password used for seeded users:", default_password)


if __name__ == "__main__":
    main()
