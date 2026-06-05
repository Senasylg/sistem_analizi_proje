Hat bazinda ozet (test split, ayni model):
- Model: stable_pruned / feature_set=pruned_safe
- Not: n_test < 10 olan hatlarda cikarim zayif

- M2: n=39, gercek_ort=25.26, gercek_aralik=[20.49,29.96], MAE=2.34, p90|hata|=4.08, tahmin_ort=25.30
- M13: n=37, gercek_ort=25.44, gercek_aralik=[20.36,29.90], MAE=2.56, p90|hata|=4.58, tahmin_ort=25.25
- M7: n=36, gercek_ort=24.92, gercek_aralik=[20.06,29.66], MAE=2.49, p90|hata|=4.45, tahmin_ort=25.24
- M12: n=26, gercek_ort=25.67, gercek_aralik=[20.86,29.55], MAE=2.27, p90|hata|=3.98, tahmin_ort=25.39

Az örnekli hatlar (yorum dikkat):
M17 (n=7), M6 (n=6), M16 (n=6), M14 (n=5), M5 (n=5), M4 (n=5), M11 (n=5), M1 (n=5), M8 (n=4), M9 (n=4), M10 (n=4), M3 (n=3), M15 (n=3)
