import numpy as np
import pandas as pd
import joblib
import train_model as tm
from pathlib import Path

# Dosya yolları
out_dir = Path('reports')
out_dir.mkdir(exist_ok=True)
csv_file = 'Pres_parametre_Master_dosya.csv'
model_file = 'models/pres_suresi_pipeline.pkl'
fi_file = 'models/feature_info.pkl'

# Model ve feature bilgilerini yükle
model = joblib.load(model_file)
fi = joblib.load(fi_file)
num_cols = list(fi['feature_cols'])
cat = fi['categorical_feature']

# Veriyi yükle ve ön işle
_df = tm.preprocess_dataframe(tm.load_csv(csv_file))
X, y, _, _, _ = tm.build_train_data(_df, 'presleme_suresi_min', feature_set='full')
X = X[num_cols + [cat]].copy()

# Baz satır (medyan) oluştur
row = {c: float(X[c].median()) for c in num_cols}
row[cat] = X[cat].mode().iloc[0]
base = float(model.predict(pd.DataFrame([row]))[0])

# What-if analizi için incelenecek feature'ları tanımla
key_pref = ['kagit_sag_recine', 'kagit_sol_recine', 'kagit_sag_nem', 'kagit_sol_nem', 'ure_jel_suresi', 'olu_zaman_max', 'ozgul_basinc_max', 'ozgul_basinc_min', 'plaka_sicakliklari_max', 'plaka_sicakliklari_min', 'max_ust_yogunluk', 'max_alt_yogunluk']
keys = [c for c in key_pref if c in num_cols]
qs = [0.1, 0.5, 0.9]

rows = []
# Tek değişkenli what-if
for c in keys:
    for q in qs:
        v = float(X[c].quantile(q))
        r = row.copy()
        r[c] = v
        p = float(model.predict(pd.DataFrame([r]))[0])
        rows.append({
            'kind': 'single',
            'scenario': f'{c}_p{int(q*100)}',
            'feature': c,
            'quantile': q,
            'value': v,
            'pred': p,
            'delta': p - base,
            'base_pred': base
        })

# Birleşik senaryolar
scenarios = []
if ('kagit_sag_recine' in num_cols) and ('kagit_sag_nem' in num_cols):
    scenarios.append(('SAG_recine_high_nem_low', {
        'kagit_sag_recine': float(X['kagit_sag_recine'].quantile(0.9)),
        'kagit_sag_nem': float(X['kagit_sag_nem'].quantile(0.1))
    }))
    scenarios.append(('SAG_recine_low_nem_high', {
        'kagit_sag_recine': float(X['kagit_sag_recine'].quantile(0.1)),
        'kagit_sag_nem': float(X['kagit_sag_nem'].quantile(0.9))
    }))
if ('ozgul_basinc_max' in num_cols) and ('plaka_sicakliklari_max' in num_cols):
    scenarios.append(('BasincMax_high_PlakaMax_high', {
        'ozgul_basinc_max': float(X['ozgul_basinc_max'].quantile(0.9)),
        'plaka_sicakliklari_max': float(X['plaka_sicakliklari_max'].quantile(0.9))
    }))
    scenarios.append(('BasincMax_low_PlakaMax_low', {
        'ozgul_basinc_max': float(X['ozgul_basinc_max'].quantile(0.1)),
        'plaka_sicakliklari_max': float(X['plaka_sicakliklari_max'].quantile(0.1))
    }))

for name, kw in scenarios:
    r = row.copy()
    r.update(kw)
    p = float(model.predict(pd.DataFrame([r]))[0])
    rows.append({
        'kind': 'combined',
        'scenario': name,
        'feature': '+'.join(kw.keys()),
        'quantile': None,
        'value': None,
        'pred': p,
        'delta': p - base,
        'base_pred': base
    })

res = pd.DataFrame(rows)
res.to_csv(out_dir / 'whatif_scenarios.csv', index=False, encoding='utf-8')

# Rapor metni oluştur
lines = []
lines.append(f"Base (median inputs, {cat}={row[cat]}): {base:.2f} min")
lines.append("")
lines.append("Single-feature what-if (p10 vs p90; others fixed at median):")
for c in keys:
    sub = res[(res.kind == 'single') & (res.feature == c)]
    p10 = float(sub[sub['quantile'] == 0.1].pred.iloc[0])
    p90 = float(sub[sub['quantile'] == 0.9].pred.iloc[0])
    v10 = float(sub[sub['quantile'] == 0.1].value.iloc[0])
    v90 = float(sub[sub['quantile'] == 0.9].value.iloc[0])
    lines.append(f"- {c}: p10={v10:.4g} -> {p10:.2f} (Delta {p10-base:+.2f}), p90={v90:.4g} -> {p90:.2f} (Delta {p90-base:+.2f})")

lines.append("")
lines.append("Combined scenarios:")
for name, kw in scenarios:
    p = float(res[(res.scenario == name)].pred.iloc[0])
    ktxt = ", ".join([f"{k}={v:.4g}" for k,v in kw.items()])
    lines.append(f"- {name}: {ktxt} -> {p:.2f} (Delta {p-base:+.2f})")

(out_dir / 'whatif_scenarios.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
print(f"DONE, WROTE reports/whatif_scenarios.csv and .md")