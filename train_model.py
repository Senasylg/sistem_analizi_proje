import os
import json
from datetime import datetime
import warnings

import joblib
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')  # GUI olmayan ortamlarda (server/CI) grafik üretebilmek için
import matplotlib.pyplot as plt
import seaborn as sns
from pandas.api.types import is_numeric_dtype
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyRegressor
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import RandomizedSearchCV, train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

warnings.filterwarnings('ignore')


def _backup_if_exists(path: str) -> None:
    if os.getenv('BACKUP_ARTEFACTS', '1') in {'0', 'false', 'False', 'no', 'NO'}:
        return
    if not os.path.exists(path):
        return
    bak_path = f"{path}.bak"
    try:
        if os.path.exists(bak_path):
            os.remove(bak_path)
        os.replace(path, bak_path)
    except Exception:
        # Yedekleme başarısız olsa bile eğitime devam et.
        pass

# Ortam değişkenleriyle (env) davranış kontrolü
def _should_make_plots() -> bool:
    v = os.getenv('MAKE_PLOTS', '1')
    return v not in {'0', 'false', 'False', 'no', 'NO'}

# Feature engineering açık/kapalı
def _use_feature_engineering() -> bool:
    v = os.getenv('FEATURE_ENGINEERING', '0')
    return v not in {'0', 'false', 'False', 'no', 'NO'}

# FEATURE_SET: auto | full | pruned_safe
def _feature_set() -> str:
    v = os.getenv('FEATURE_SET', 'auto').strip().lower()
    if v in {'auto', 'choose', 'best'}:
        return 'auto'
    if v in {'full', 'all', 'raw'}:
        return 'full'
    return 'pruned_safe'

# Sayısal feature listesi (full / pruned_safe)
def _get_numeric_feature_cols(feature_set: str) -> list[str]:
    full_feature_cols = [
        'max_ust_yogunluk', 'min_orta_yogunluk', 'max_alt_yogunluk',
        'ure_jel_suresi', 'melamin_jel_suresi',
        'kagit_sag_recine', 'kagit_sag_nem',
        'kagit_orta_recine', 'kagit_orta_nem',
        'kagit_sol_recine', 'kagit_sol_nem',
        'plaka_sicakliklari_min', 'plaka_sicakliklari_max',
        'ozgul_basinc_min', 'ozgul_basinc_max',
        'olu_zaman_min', 'olu_zaman_max',
    ]

    # ÖNEMLİ: Optimizasyon değişkenleri modelde kalmalı.
    pruned_safe_drop = {
        'kagit_orta_nem',
        'kagit_orta_recine',
        'melamin_jel_suresi',
        'olu_zaman_min',
        'min_orta_yogunluk',
    }

    fs = (feature_set or '').strip().lower()
    if fs in {'full', 'all', 'raw'}:
        return full_feature_cols
    if fs in {'pruned_safe', 'pruned', 'safe'}:
        return [c for c in full_feature_cols if c not in pruned_safe_drop]
    raise ValueError(f"Unknown feature_set: {feature_set}")

# TRAINING_STRATEGY: stable_pruned | search
def _training_strategy() -> str:
        v = os.getenv('TRAINING_STRATEGY', 'stable_pruned').strip().lower()
        if v in {'search', 'tune', 'random_search', 'randomizedsearchcv'}:
                return 'search'
        return 'stable_pruned'

# Dizin yoksa oluştur
def _ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path

# Grafiği kaydet ve figürü kapat
def _savefig(path: str) -> None:
    plt.tight_layout()
    plt.savefig(path, dpi=180, bbox_inches='tight')
    plt.close()

# Basit özet feature'lar (mean/range/std) üretir
class FeatureEngineer(BaseEstimator, TransformerMixin):
    engineered_cols: list[str] = [
        'plaka_sicakliklari_mean', 'plaka_sicakliklari_range',
        'ozgul_basinc_mean', 'ozgul_basinc_range',
        'olu_zaman_mean', 'olu_zaman_range',
        'kagit_recine_mean', 'kagit_recine_std',
        'kagit_nem_mean', 'kagit_nem_std',
        'yogunluk_mean', 'yogunluk_range',
    ]

    def fit(self, X, y=None):
        return self

    def transform(self, X):
        if not isinstance(X, pd.DataFrame):
            X = pd.DataFrame(X)
        out = X.copy()

        def _mean(a, b):
            if a in out.columns and b in out.columns:
                return (out[a] + out[b]) / 2.0
            return np.nan

        def _range(a, b):
            if a in out.columns and b in out.columns:
                return out[b] - out[a]
            return np.nan

        # Plaka sıcaklığı
        out['plaka_sicakliklari_mean'] = _mean('plaka_sicakliklari_min', 'plaka_sicakliklari_max')
        out['plaka_sicakliklari_range'] = _range('plaka_sicakliklari_min', 'plaka_sicakliklari_max')

        # Basınç
        out['ozgul_basinc_mean'] = _mean('ozgul_basinc_min', 'ozgul_basinc_max')
        out['ozgul_basinc_range'] = _range('ozgul_basinc_min', 'ozgul_basinc_max')

        # Ölü zaman
        out['olu_zaman_mean'] = _mean('olu_zaman_min', 'olu_zaman_max')
        out['olu_zaman_range'] = _range('olu_zaman_min', 'olu_zaman_max')

        # Kağıt reçine/nem özetleri
        rec_cols = [c for c in ['kagit_sag_recine', 'kagit_orta_recine', 'kagit_sol_recine'] if c in out.columns]
        nem_cols = [c for c in ['kagit_sag_nem', 'kagit_orta_nem', 'kagit_sol_nem'] if c in out.columns]
        if rec_cols:
            out['kagit_recine_mean'] = out[rec_cols].mean(axis=1)
            out['kagit_recine_std'] = out[rec_cols].std(axis=1)
        else:
            out['kagit_recine_mean'] = np.nan
            out['kagit_recine_std'] = np.nan

        if nem_cols:
            out['kagit_nem_mean'] = out[nem_cols].mean(axis=1)
            out['kagit_nem_std'] = out[nem_cols].std(axis=1)
        else:
            out['kagit_nem_mean'] = np.nan
            out['kagit_nem_std'] = np.nan

        # Yoğunluk özetleri
        den_cols = [c for c in ['max_ust_yogunluk', 'min_orta_yogunluk', 'max_alt_yogunluk'] if c in out.columns]
        if den_cols:
            out['yogunluk_mean'] = out[den_cols].mean(axis=1)
            out['yogunluk_range'] = out[den_cols].max(axis=1) - out[den_cols].min(axis=1)
        else:
            out['yogunluk_mean'] = np.nan
            out['yogunluk_range'] = np.nan

        return out

# EDA grafikleri (korelasyon/dağılım)
def generate_eda_plots(df: pd.DataFrame, feature_cols: list[str], target_col: str, out_dir: str) -> None:
    out_dir = _ensure_dir(out_dir)

    work_cols = [c for c in (feature_cols + [target_col]) if c in df.columns]
    work = df[work_cols].copy()
    work = work.dropna(subset=[target_col])

    if len(work) < 20:
        print("   [UYARI] Plot üretmek için yeterli satır yok.")
        return

    # Korelasyon (Spearman; monotonik / doğrusal olmayan ilişkilerde daha dayanıklıdır)
    corr = work.corr(method='spearman', numeric_only=True)
    if target_col in corr.columns:
        corr_to_target = corr[target_col].drop(labels=[target_col]).sort_values(key=lambda s: s.abs(), ascending=False)
    else:
        corr_to_target = pd.Series(dtype=float)

    # Isı haritası
    plt.figure(figsize=(12, 10))
    sns.heatmap(corr, cmap='RdBu_r', center=0, annot=False, square=False)
    plt.title(f"Spearman Korelasyon Isı Haritası (n={len(work)})")
    _savefig(os.path.join(out_dir, 'corr_spearman_heatmap.png'))

    # Çubuk grafik: hedef ile korelasyonlar
    if not corr_to_target.empty:
        plt.figure(figsize=(10, 6))
        sns.barplot(x=corr_to_target.values, y=corr_to_target.index, orient='h')
        plt.axvline(0, color='black', linewidth=1)
        plt.title(f"{target_col} ile Korelasyonlar (Spearman)")
        plt.xlabel('Korelasyon')
        plt.ylabel('Feature')
        _savefig(os.path.join(out_dir, 'corr_to_target_spearman.png'))

        # En yüksek korelasyonlu feature'lar için scatter ızgarası
        top_features = list(corr_to_target.index[:6])
        if top_features:
            fig, axes = plt.subplots(2, 3, figsize=(14, 8))
            axes = axes.flatten()
            for ax, feat in zip(axes, top_features, strict=False):
                ax.scatter(work[feat], work[target_col], s=12, alpha=0.35)
                ax.set_xlabel(feat)
                ax.set_ylabel(target_col)
                ax.set_title(f"{feat} vs {target_col}")
            for ax in axes[len(top_features):]:
                ax.axis('off')
            fig.suptitle('En Yüksek Korelasyonlu Feature Scatter Grafikleri', y=1.02)
            plt.tight_layout()
            fig.savefig(os.path.join(out_dir, 'scatter_top_corr.png'), dpi=180, bbox_inches='tight')
            plt.close(fig)

    print(f"   [OK] EDA grafikleri kaydedildi: {out_dir}")

# Model diagnostik grafikleri (pred-true / residual)
def generate_model_diagnostic_plots(y_train: pd.Series, y_test: pd.Series, train_pred: np.ndarray, test_pred: np.ndarray, out_dir: str) -> None:
    out_dir = _ensure_dir(out_dir)

    # Tahmin vs Gerçek
    plt.figure(figsize=(6.5, 6.5))
    plt.scatter(y_test, test_pred, s=14, alpha=0.45)
    lo = float(min(np.min(y_test), np.min(test_pred)))
    hi = float(max(np.max(y_test), np.max(test_pred)))
    plt.plot([lo, hi], [lo, hi], color='black', linewidth=1)
    plt.title('Test: Gerçek vs Tahmin')
    plt.xlabel('Gerçek')
    plt.ylabel('Tahmin')
    _savefig(os.path.join(out_dir, 'pred_vs_true_test.png'))

    # Residual (Gerçek - Tahmin)
    resid = (y_test.to_numpy() - np.asarray(test_pred))
    plt.figure(figsize=(8, 4.5))
    sns.histplot(resid, bins=30, kde=True)
    plt.axvline(0, color='black', linewidth=1)
    plt.title('Test Residual Dağılımı (Gerçek - Tahmin)')
    plt.xlabel('Residual')
    _savefig(os.path.join(out_dir, 'residuals_test.png'))

    # Train vs Test residual ölçek karşılaştırması
    train_resid = (y_train.to_numpy() - np.asarray(train_pred))
    plt.figure(figsize=(8, 4.5))
    sns.kdeplot(train_resid, label='Train', fill=True, alpha=0.25)
    sns.kdeplot(resid, label='Test', fill=True, alpha=0.25)
    plt.axvline(0, color='black', linewidth=1)
    plt.title('Residual KDE: Train vs Test')
    plt.xlabel('Residual')
    plt.legend()
    _savefig(os.path.join(out_dir, 'residuals_train_vs_test.png'))

    print(f"   [OK] Model diagnostik grafikleri kaydedildi: {out_dir}")

# Hat bazında what-if raporu üret
def _should_make_line_whatif() -> bool:
    v = os.getenv('MAKE_LINE_WHATIF', '1')
    return v not in {'0', 'false', 'False', 'no', 'NO'}

# Hat bazında what-if (p10/p90 oynaklığı)
def generate_line_whatif_reports(
    pipeline: Pipeline,
    X_raw: pd.DataFrame,
    numeric_cols: list[str],
    categorical_col: str,
    out_dir: str,
    *,
    top_lines: int = 4,
    min_rows_per_line: int = 50,
    top_features_per_line: int = 6,
) -> None:
    out_dir = _ensure_dir(out_dir)

    if X_raw.empty:
        return
    missing = [c for c in numeric_cols + [categorical_col] if c not in X_raw.columns]
    if missing:
        print(f"   [UYARI] line_whatif üretilemedi (eksik kolonlar): {missing}")
        return

    Xw = X_raw[numeric_cols + [categorical_col]].copy()
    Xw[categorical_col] = Xw[categorical_col].astype(str)
    counts = Xw[categorical_col].value_counts()
    lines = [ln for ln in list(counts.head(top_lines).index) if int(counts[ln]) >= min_rows_per_line]

    if not lines:
        print("   [UYARI] line_whatif: yeterli satır yok (hat bazında).")
        return

    preferred_features = [
        'ozgul_basinc_max', 'ozgul_basinc_min',
        'olu_zaman_max',
        'ure_jel_suresi',
        'kagit_sag_recine', 'kagit_sol_recine',
        'kagit_sag_nem', 'kagit_sol_nem',
        'plaka_sicakliklari_max', 'plaka_sicakliklari_min',
        'max_ust_yogunluk', 'max_alt_yogunluk',
    ]
    key_cols = [c for c in preferred_features if c in numeric_cols]
    if not key_cols:
        key_cols = list(numeric_cols)

    rows: list[dict] = []
    for line in lines:
        sub = Xw[Xw[categorical_col] == line]
        base_row = {c: float(sub[c].median()) for c in numeric_cols}
        base_row[categorical_col] = line
        base_pred = float(pipeline.predict(pd.DataFrame([base_row]))[0])

        for feat in key_cols:
            v10 = float(sub[feat].quantile(0.1))
            v90 = float(sub[feat].quantile(0.9))

            r10 = base_row.copy()
            r10[feat] = v10
            p10 = float(pipeline.predict(pd.DataFrame([r10]))[0])

            r90 = base_row.copy()
            r90[feat] = v90
            p90 = float(pipeline.predict(pd.DataFrame([r90]))[0])

            rows.append({
                'melamin_hatti': line,
                'n_line': int(len(sub)),
                'feature': feat,
                'base_pred': base_pred,
                'p10_value': v10,
                'p10_pred': p10,
                'p10_delta': p10 - base_pred,
                'p90_value': v90,
                'p90_pred': p90,
                'p90_delta': p90 - base_pred,
                'swing_p90_minus_p10': p90 - p10,
            })

    rep = pd.DataFrame(rows)
    if rep.empty:
        print("   [UYARI] line_whatif: tablo üretilemedi.")
        return

    csv_path = os.path.join(out_dir, 'line_whatif.csv')
    md_path = os.path.join(out_dir, 'line_whatif.md')
    rep.to_csv(csv_path, index=False, encoding='utf-8')

    lines_md: list[str] = []
    lines_md.append("Hat-özel what-if (model tabanlı; diğer feature'lar hat medyanında sabit)")
    lines_md.append(f"- Hatlar: {', '.join(lines)}")
    lines_md.append("- Not: Bu bir kural değil; modelin öğrendiği istatistiksel ilişki.")
    lines_md.append("")

    rep['abs_swing'] = rep['swing_p90_minus_p10'].abs()
    for line in lines:
        sub = rep[rep['melamin_hatti'] == line].copy()
        base = float(sub['base_pred'].iloc[0])
        n_line = int(sub['n_line'].iloc[0])
        lines_md.append(f"{line} (n={n_line}) | Base≈{base:.2f} dk")
        top = sub.sort_values('abs_swing', ascending=False).head(top_features_per_line)
        for _, r in top.iterrows():
            lines_md.append(
                f"- {r['feature']}: p10={r['p10_value']:.4g} → {r['p10_pred']:.2f}, p90={r['p90_value']:.4g} → {r['p90_pred']:.2f} | swing={r['swing_p90_minus_p10']:+.2f} dk"
            )
        lines_md.append("")

    with open(md_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(lines_md) + "\n")

    print(f"   [OK] Hat-özel what-if raporu kaydedildi: {csv_path} / {md_path}")

# Metrikleri JSON olarak kaydet (latest + opsiyonel geçmiş)
def save_metrics(metrics: dict, out_dir: str) -> None:
    out_dir = _ensure_dir(out_dir)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    latest_path = os.path.join(out_dir, 'metrics_latest.json')
    stamped_path = os.path.join(out_dir, f'metrics_{ts}.json')
    try:
        with open(latest_path, 'w', encoding='utf-8') as f:
            json.dump(metrics, f, ensure_ascii=False, indent=2)
        keep_history = os.getenv('SAVE_METRICS_HISTORY', '0') not in {'0', 'false', 'False', 'no', 'NO'}
        if keep_history:
            with open(stamped_path, 'w', encoding='utf-8') as f:
                json.dump(metrics, f, ensure_ascii=False, indent=2)
        print(f"   [OK] Metrikler kaydedildi: {latest_path}")
    except Exception as e:
        print(f"   [UYARI] Metrikler kaydedilemedi: {e}")

def _print_header():
    print("=" * 60)
    print("MELAMINLI LEVHA PRESİ MODELİ EĞİTİMİ BAŞLANIYIYOR")
    print("=" * 60)

# Makine öğrenmesi modelini eğitmek için temel fonksiyonlar
# CSV oku, boş satır/sütunları temizle
def load_csv(csv_path: str) -> pd.DataFrame:
    print("\n1. Veri yükleniyor...")
    try:
        df = pd.read_csv(csv_path, sep=';', encoding='utf-8-sig', low_memory=False)
        print(f"   [OK] Veri başarıyla yüklendi ({len(df)} satır)")
        print(f"   Sütunlar: {df.shape[1]}")
    except Exception as e:
        print(f"   [HATA] Hata: {e}")
        print("   CSV dosyası bulunamadı veya okunamadı")
        raise

    df.columns = df.columns.str.strip()
    df = df.dropna(how='all')
    df = df.dropna(axis=1, how='all')
    return df

# Kolonları standardize et ve sayısalları dönüştür
def preprocess_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    print("\n2. Veri ön işleme yapılıyor...")

    column_mapping = {
        'Kalınlık': 'kalinlik',
        'Renk Değer': 'renk_deger',
        'Kağıt Renk': 'kagit_renk',
        'Pres Plaka Yüzey': 'pres_plaka_yuzey',
        'Melamin Pres Hatları': 'melamin_hatti',
        'Max üst yoğ.kg/m3': 'max_ust_yogunluk',
        'Min. Orta. Kg/m3': 'min_orta_yogunluk',
        'Max alt yoğ.kg/m3': 'max_alt_yogunluk',
        'Üre Jel Süresi (Emprenye)': 'ure_jel_suresi',
        'Melamin Jel Süresi (Ceketleme)': 'melamin_jel_suresi',
        'Sağ (%Reçine)': 'kagit_sag_recine',
        'Sağ (%Nem)': 'kagit_sag_nem',
        'Orta (%Reçine)': 'kagit_orta_recine',
        'Orta (%Nem)': 'kagit_orta_nem',
        'Sol (%Reçine)': 'kagit_sol_recine',
        'Sol (%Nem)': 'kagit_sol_nem',
        'Plaka Sıcaklıkları Min': 'plaka_sicakliklari_min',
        'Plaka Sıcaklıkları Max': 'plaka_sicakliklari_max',
        'Özgül Basınç Min': 'ozgul_basinc_min',
        'Özgül Basınç Max': 'ozgul_basinc_max',
        'Anlık Duruş': 'anlik_durus',
        'Ölü Zaman Minimum': 'olu_zaman_min',
        'Ölü Zaman Maximum': 'olu_zaman_max',
        'Presleme Süresi Min': 'presleme_suresi_min',
        'Presleme Süresi Max': 'presleme_suresi_max',
    }

    renamed_cols = {k: v for k, v in column_mapping.items() if k in df.columns}
    df = df.rename(columns=renamed_cols)

    # Türkçe CSV: ondalık ayıracı ',' olabilir.
    numeric_cols = [
        'kalinlik', 'renk_deger',
        'max_ust_yogunluk', 'min_orta_yogunluk', 'max_alt_yogunluk',
        'ure_jel_suresi', 'melamin_jel_suresi',
        'kagit_sag_recine', 'kagit_sag_nem',
        'kagit_orta_recine', 'kagit_orta_nem',
        'kagit_sol_recine', 'kagit_sol_nem',
        'plaka_sicakliklari_min', 'plaka_sicakliklari_max',
        'ozgul_basinc_min', 'ozgul_basinc_max',
        'anlik_durus',
        'olu_zaman_min', 'olu_zaman_max',
        'presleme_suresi_min', 'presleme_suresi_max',
    ]

    for col in numeric_cols:
        if col not in df.columns:
            continue
        if is_numeric_dtype(df[col]):
            continue
        df[col] = pd.to_numeric(
            df[col].astype(str).str.strip().str.replace(' ', '').str.replace(',', '.'),
            errors='coerce',
        )

    print(f"   [OK] Veri şekli: {df.shape}")
    print(f"   [OK] Örnek sütunlar: {list(df.columns[:10])}")
    return df

# X/y hazırla (eksik kolon kontrolü + tip düzeltmeleri)
def build_train_data(df: pd.DataFrame, target_col: str, feature_set: str | None = None):
    if target_col not in df.columns:
        raise ValueError(f"Hedef sütun '{target_col}' bulunamadı!")

    fs = feature_set if feature_set is not None else _feature_set()
    if fs == 'auto':
        feature_cols = _get_numeric_feature_cols('full')
    else:
        feature_cols = _get_numeric_feature_cols(fs)

    categorical_feature = 'melamin_hatti'
    engineered_cols = FeatureEngineer.engineered_cols if _use_feature_engineering() else []

    missing = [c for c in feature_cols + [categorical_feature] if c not in df.columns]
    if missing:
        raise ValueError(f"Eksik sütunlar: {missing}")

    df = df.dropna(subset=[target_col])
    X = df[feature_cols + [categorical_feature]].copy()
    y = pd.to_numeric(df[target_col], errors='coerce')
    X = X.loc[y.notna()].copy()
    y = y.loc[y.notna()].astype(float)

    # Kategorikler: None/NaN olsa bile stringe çevir (OneHot için)
    X[categorical_feature] = X[categorical_feature].astype(str)

    print(f"   [OK] Veri hazırlandı: {X.shape[0]} örnek, {X.shape[1]} sütun")
    print(f"   Hedef değişken aralığı: {y.min():.2f} - {y.max():.2f}")
    return X, y, feature_cols, engineered_cols, categorical_feature

# Regresyonda stratify için hedefi dilimlere ayır
def stratify_bins(y: pd.Series, q: int = 10) -> pd.Series:
    ranks = y.rank(method='first')
    return pd.qcut(ranks, q=q, labels=False, duplicates='drop')

# Temel regresyon metriklerini yazdır
def evaluate(y_true, y_pred, label: str):
    mse = mean_squared_error(y_true, y_pred)
    rmse = np.sqrt(mse)
    mae = mean_absolute_error(y_true, y_pred)
    r2 = r2_score(y_true, y_pred)
    print(f"   {label} RMSE: {rmse:.4f}")
    print(f"   {label} MAE : {mae:.4f}")
    print(f"   {label} R²  : {r2:.4f}")
    return rmse, mae, r2

# Eğitim akışı (yükle → ön işle → böl → eğit → kaydet)
def main():
    os.makedirs('models', exist_ok=True)

    _print_header()
    csv_path = 'Pres_parametre_Master_dosya.csv'
    df = load_csv(csv_path)
    df = preprocess_dataframe(df)

    target_col = 'presleme_suresi_min'
    requested_fs = _feature_set()
    X, y, feature_cols_full, engineered_cols, categorical_feature = build_train_data(df, target_col, feature_set='full')

    # Aday feature setleri (sadece sayısal kolonlar). Kategorik kolon sabit kalır.
    feature_cols_pruned = _get_numeric_feature_cols('pruned_safe')
    X_full = X[feature_cols_full + [categorical_feature]].copy()
    X_pruned = X[feature_cols_pruned + [categorical_feature]].copy()

    if _should_make_plots():
        report_dir = os.getenv('REPORT_DIR', 'reports')
        print("\n2b. EDA (korelasyon grafikleri) üretiliyor...")
        if engineered_cols:
            # Korelasyonda engineered kolonları da göster
            try:
                eda_base = df[feature_cols_full + [target_col]].copy()
                eda_eng = FeatureEngineer().transform(eda_base)
                generate_eda_plots(eda_eng, feature_cols=feature_cols_full + engineered_cols, target_col=target_col, out_dir=report_dir)
            except Exception:
                generate_eda_plots(df, feature_cols=feature_cols_full, target_col=target_col, out_dir=report_dir)
        else:
            generate_eda_plots(df, feature_cols=feature_cols_full, target_col=target_col, out_dir=report_dir)

    print("\n3. Veri bölünüyor...")
    y_bins = stratify_bins(y, q=10)
    X_train_full, X_test_full, y_train, y_test = train_test_split(
        X_full, y, test_size=0.2, random_state=42, stratify=y_bins
    )
    X_train_pruned = X_pruned.loc[X_train_full.index]
    X_test_pruned = X_pruned.loc[X_test_full.index]
    print(f"   [OK] Eğitim: {X_train_full.shape[0]}, Test: {X_test_full.shape[0]}")

    print("\n4. Baseline (DummyRegressor) değerlendiriliyor...")
    dummy = DummyRegressor(strategy='mean')
    dummy.fit(X_train_full, y_train)
    dummy_pred = dummy.predict(X_test_full)
    baseline_rmse, baseline_mae, baseline_r2 = evaluate(y_test, dummy_pred, label="Baseline/Test")

    # Strateji: sabit preset (şu ana kadarki en stabil/bilinen)
    if _training_strategy() == 'stable_pruned':
        print("\n5. Eğitim stratejisi: stable_pruned (sabit preset, pruned_safe)...")
        chosen_feature_cols = feature_cols_pruned
        chosen_feature_set = 'pruned_safe'
        chosen_X_train = X_train_pruned
        chosen_X_test = X_test_pruned

        # Sabit hiperparametreler
        model_params = {
            'loss': 'squared_error',
            'learning_rate': 0.08,
            'max_depth': 6,
            'max_leaf_nodes': 63,
            'min_samples_leaf': 40,
            'l2_regularization': 1.0,
            'max_iter': 900,
        }

        preprocessor_final = ColumnTransformer(
            transformers=[
                ('num', SimpleImputer(strategy='median'), chosen_feature_cols + engineered_cols),
                ('cat', OneHotEncoder(handle_unknown='ignore'), [categorical_feature]),
            ],
            remainder='drop',
            sparse_threshold=0,
        )

        steps_final = []
        if engineered_cols:
            steps_final.append(('features', FeatureEngineer()))
        steps_final.extend([
            ('preprocess', preprocessor_final),
            ('model', HistGradientBoostingRegressor(random_state=42, early_stopping=True, **model_params)),
        ])
        best_model = Pipeline(steps=steps_final)
        best_model.fit(chosen_X_train, y_train)

        print("\n7. Holdout performansı (feature_set=pruned_safe, stable preset)...")
        train_pred = best_model.predict(chosen_X_train)
        test_pred = best_model.predict(chosen_X_test)
        train_rmse, train_mae, train_r2 = evaluate(y_train, train_pred, label="Eğitim")
        test_rmse, test_mae, test_r2 = evaluate(y_test, test_pred, label="Test")

        if _should_make_plots():
            report_dir = os.getenv('REPORT_DIR', 'reports')
            print("\n7b. Diagnostik grafikler üretiliyor...")
            generate_model_diagnostic_plots(y_train=y_train, y_test=y_test, train_pred=train_pred, test_pred=test_pred, out_dir=report_dir)

        if os.getenv('SAVE_METRICS', '1') not in {'0', 'false', 'False', 'no', 'NO'}:
            report_dir = os.getenv('REPORT_DIR', 'reports')
            metrics = {
                'timestamp': datetime.now().isoformat(timespec='seconds'),
                'target_col': target_col,
                'n_rows': int(X.shape[0]),
                'split': {'train': int(X_train_full.shape[0]), 'test': int(X_test_full.shape[0])},
                'baseline': {'rmse': float(baseline_rmse), 'mae': float(baseline_mae), 'r2': float(baseline_r2)},
                'cv_best': None,
                'holdout_train': {'rmse': float(train_rmse), 'mae': float(train_mae), 'r2': float(train_r2)},
                'holdout_test': {'rmse': float(test_rmse), 'mae': float(test_mae), 'r2': float(test_r2)},
                'best_params': {f"model__{k}": v for k, v in model_params.items()},
                'features': {
                    'raw': chosen_feature_cols,
                    'engineered': engineered_cols,
                    'categorical': categorical_feature,
                },
                'feature_set': chosen_feature_set,
                'tuning': {
                    'strategy': 'stable_pruned',
                },
            }
            save_metrics(metrics, out_dir=report_dir)

        if _should_make_line_whatif():
            report_dir = os.getenv('REPORT_DIR', 'reports')
            print("\n7c. Hat-özel what-if raporu üretiliyor...")
            try:
                generate_line_whatif_reports(
                    pipeline=best_model,
                    X_raw=X_pruned,
                    numeric_cols=chosen_feature_cols,
                    categorical_col=categorical_feature,
                    out_dir=report_dir,
                )
            except Exception as e:
                print(f"   [UYARI] line_whatif üretilemedi: {e}")

        print("\n8. Model kaydediliyor...")
        pipeline_path = 'models/pres_suresi_pipeline.pkl'
        _backup_if_exists(pipeline_path)
        joblib.dump(best_model, pipeline_path)
        print(f"   [OK] Pipeline kaydedildi: {pipeline_path}")

        feature_info = {
            'artifact': 'pipeline_v2',
            'target_col': target_col,
            'feature_cols': chosen_feature_cols,
            'engineered_cols': engineered_cols,
            'categorical_feature': categorical_feature,
            'feature_engineering': bool(engineered_cols),
            'feature_set': chosen_feature_set,
            'training_strategy': 'stable_pruned',
        }
        feature_info_path = 'models/feature_info.pkl'
        _backup_if_exists(feature_info_path)
        joblib.dump(feature_info, feature_info_path)
        print(f"   [OK] Feature bilgisi kaydedildi: {feature_info_path}")

        print("\n" + "=" * 60)
        print("[OK] MODEL EĞİTİMİ TAMAMLANDI")
        print("=" * 60)
        print("\nUygulamayı çalıştırmak için:")
        print("   streamlit run app.py")
        return

    print("\n5. Pipeline kuruluyor (Imputer + OneHot + Model)...")

    # Ön işleme: sayısal=median impute, kategorik=one-hot (bilinmeyen kategoriler yok sayılır)
    preprocessor_full = ColumnTransformer(
        transformers=[
            ('num', SimpleImputer(strategy='median'), feature_cols_full + engineered_cols),
            ('cat', OneHotEncoder(handle_unknown='ignore'), [categorical_feature]),
        ],
        remainder='drop',
        sparse_threshold=0,
    )

    print("\n6. Hiperparametre araması (HistGradientBoosting / RandomizedSearchCV)...")

    steps = []
    if engineered_cols:
        steps.append(('features', FeatureEngineer()))
    steps.extend([
        ('preprocess', preprocessor_full),
        ('model', HistGradientBoostingRegressor(random_state=42, early_stopping=True, loss='squared_error')),
    ])
    pipeline = Pipeline(steps=steps)

    param_distributions = {
        # NOT: Arama uzayı geniş ama sınırlı (underfit/overfit dengesi)
        'model__loss': ['squared_error', 'absolute_error'],
        'model__learning_rate': [0.01, 0.02, 0.03, 0.05, 0.08, 0.12, 0.16],
        'model__max_depth': [2, 3, 4, 5, 6, 8, None],
        'model__max_leaf_nodes': [15, 31, 63, 127, 255],
        'model__min_samples_leaf': [2, 5, 10, 20, 40],
        'model__l2_regularization': [0.0, 0.02, 0.05, 0.1, 0.3, 1.0],
        'model__max_iter': [300, 600, 900, 1200, 1800],
    }

    n_iter = int(os.getenv('N_ITER', '80'))
    cv_folds = int(os.getenv('CV_FOLDS', '5'))
    n_jobs = int(os.getenv('N_JOBS', '-1'))
    verbose = int(os.getenv('VERBOSE', '0'))
    search = RandomizedSearchCV(
        pipeline,
        param_distributions=param_distributions,
        n_iter=n_iter,
        scoring={
            'mae': 'neg_mean_absolute_error',
            'rmse': 'neg_root_mean_squared_error',
            'r2': 'r2',
        },
        refit='mae',
        cv=cv_folds,
        random_state=42,
        n_jobs=n_jobs,
        verbose=verbose,
    )

    print(f"   → Arama başlıyor (n_iter={n_iter}, cv={cv_folds}, n_jobs={n_jobs}, refit=MAE)...")
    search.fit(X_train_full, y_train)

    best_model = search.best_estimator_
    best_idx = search.best_index_
    best_cv_mae = -float(search.best_score_)
    best_cv_rmse = -float(search.cv_results_['mean_test_rmse'][best_idx])
    best_cv_r2 = float(search.cv_results_['mean_test_r2'][best_idx])
    print(f"   [OK] En iyi CV MAE: {best_cv_mae:.4f} | RMSE: {best_cv_rmse:.4f} | R²: {best_cv_r2:.4f}")
    print(f"   [OK] En iyi parametreler: {search.best_params_}")

    # Kaydedilecek final model için kullanılacak feature set'i seç.
    chosen_feature_cols = feature_cols_full
    chosen_feature_set = 'full'
    chosen_X_train = X_train_full
    chosen_X_test = X_test_full

    if requested_fs == 'auto':
        print("\n6b. Feature set seçimi (AUTO): full vs pruned_safe validation...")
        y_bins_train = stratify_bins(y_train, q=10)
        X_tn_full, X_val_full, y_tn, y_val = train_test_split(
            X_train_full, y_train, test_size=0.2, random_state=42, stratify=y_bins_train
        )
        X_tn_pr = X_train_pruned.loc[X_tn_full.index]
        X_val_pr = X_train_pruned.loc[X_val_full.index]

        best_params = search.best_params_
        model_params = {k.replace('model__', ''): v for k, v in best_params.items() if k.startswith('model__')}
        base_model = HistGradientBoostingRegressor(random_state=42, early_stopping=True)
        base_model.set_params(**model_params)

        def _fit_and_val(num_cols, X_tn, X_val):
            pre = ColumnTransformer(
                transformers=[
                    ('num', SimpleImputer(strategy='median'), num_cols + engineered_cols),
                    ('cat', OneHotEncoder(handle_unknown='ignore'), [categorical_feature]),
                ],
                remainder='drop',
                sparse_threshold=0,
            )
            steps_local = []
            if engineered_cols:
                steps_local.append(('features', FeatureEngineer()))
            steps_local.extend([
                ('preprocess', pre),
                ('model', base_model),
            ])
            pipe = Pipeline(steps=steps_local)
            pipe.fit(X_tn, y_tn)
            val_pred = pipe.predict(X_val)
            val_mae = mean_absolute_error(y_val, val_pred)
            return pipe, float(val_mae)

        _, val_mae_full = _fit_and_val(feature_cols_full, X_tn_full, X_val_full)
        _, val_mae_pr = _fit_and_val(feature_cols_pruned, X_tn_pr, X_val_pr)
        print(f"   [AUTO] Validation MAE | full={val_mae_full:.4f} | pruned_safe={val_mae_pr:.4f}")

        if val_mae_pr < val_mae_full:
            chosen_feature_cols = feature_cols_pruned
            chosen_feature_set = 'pruned_safe'
            chosen_X_train = X_train_pruned
            chosen_X_test = X_test_pruned
        else:
            chosen_feature_cols = feature_cols_full
            chosen_feature_set = 'full'
            chosen_X_train = X_train_full
            chosen_X_test = X_test_full

    elif requested_fs == 'pruned_safe':
        chosen_feature_cols = feature_cols_pruned
        chosen_feature_set = 'pruned_safe'
        chosen_X_train = X_train_pruned
        chosen_X_test = X_test_pruned

    print(f"\n7. Holdout performansı (feature_set={chosen_feature_set})...")

    # Seçilen sayısal kolonlarla en iyi pipeline'ı yeniden kur ve full train split üzerinde yeniden fit et.
    best_params = search.best_params_
    model_params = {k.replace('model__', ''): v for k, v in best_params.items() if k.startswith('model__')}
    final_model = HistGradientBoostingRegressor(random_state=42, early_stopping=True)
    final_model.set_params(**model_params)
    preprocessor_final = ColumnTransformer(
        transformers=[
            ('num', SimpleImputer(strategy='median'), chosen_feature_cols + engineered_cols),
            ('cat', OneHotEncoder(handle_unknown='ignore'), [categorical_feature]),
        ],
        remainder='drop',
        sparse_threshold=0,
    )
    steps_final = []
    if engineered_cols:
        steps_final.append(('features', FeatureEngineer()))
    steps_final.extend([
        ('preprocess', preprocessor_final),
        ('model', final_model),
    ])
    best_model = Pipeline(steps=steps_final)
    best_model.fit(chosen_X_train, y_train)

    train_pred = best_model.predict(chosen_X_train)
    test_pred = best_model.predict(chosen_X_test)
    train_rmse, train_mae, train_r2 = evaluate(y_train, train_pred, label="Eğitim")
    test_rmse, test_mae, test_r2 = evaluate(y_test, test_pred, label="Test")

    if _should_make_plots():
        report_dir = os.getenv('REPORT_DIR', 'reports')
        print("\n7b. Diagnostik grafikler üretiliyor...")
        generate_model_diagnostic_plots(y_train=y_train, y_test=y_test, train_pred=train_pred, test_pred=test_pred, out_dir=report_dir)

    if os.getenv('SAVE_METRICS', '1') not in {'0', 'false', 'False', 'no', 'NO'}:
        report_dir = os.getenv('REPORT_DIR', 'reports')
        metrics = {
            'timestamp': datetime.now().isoformat(timespec='seconds'),
            'target_col': target_col,
            'n_rows': int(X.shape[0]),
            'split': {'train': int(X_train_full.shape[0]), 'test': int(X_test_full.shape[0])},
            'baseline': {'rmse': float(baseline_rmse), 'mae': float(baseline_mae), 'r2': float(baseline_r2)},
            'cv_best': {'mae': float(best_cv_mae), 'rmse': float(best_cv_rmse), 'r2': float(best_cv_r2)},
            'holdout_train': {'rmse': float(train_rmse), 'mae': float(train_mae), 'r2': float(train_r2)},
            'holdout_test': {'rmse': float(test_rmse), 'mae': float(test_mae), 'r2': float(test_r2)},
            'best_params': {k: (v.item() if hasattr(v, 'item') else v) for k, v in search.best_params_.items()},
            'features': {
                'raw': chosen_feature_cols,
                'engineered': engineered_cols,
                'categorical': categorical_feature,
            },
            'feature_set': chosen_feature_set,
            'tuning': {
                'n_iter': int(os.getenv('N_ITER', '80')),
                'cv_folds': int(os.getenv('CV_FOLDS', '5')),
                'n_jobs': int(os.getenv('N_JOBS', '-1')),
            }
        }
        save_metrics(metrics, out_dir=report_dir)

    if _should_make_line_whatif():
        report_dir = os.getenv('REPORT_DIR', 'reports')
        print("\n7c. Hat-özel what-if raporu üretiliyor...")
        try:
            X_for_report = X_pruned if chosen_feature_set == 'pruned_safe' else X_full
            generate_line_whatif_reports(
                pipeline=best_model,
                X_raw=X_for_report,
                numeric_cols=chosen_feature_cols,
                categorical_col=categorical_feature,
                out_dir=report_dir,
            )
        except Exception as e:
            print(f"   [UYARI] line_whatif üretilemedi: {e}")

    print("\n8. Model kaydediliyor...")
    pipeline_path = 'models/pres_suresi_pipeline.pkl'
    _backup_if_exists(pipeline_path)
    joblib.dump(best_model, pipeline_path)
    print(f"   [OK] Pipeline kaydedildi: {pipeline_path}")

    feature_info = {
        'artifact': 'pipeline_v2',
        'target_col': target_col,
        'feature_cols': chosen_feature_cols,
        'engineered_cols': engineered_cols,
        'categorical_feature': categorical_feature,
        'feature_engineering': bool(engineered_cols),
        'feature_set': chosen_feature_set,
    }
    feature_info_path = 'models/feature_info.pkl'
    _backup_if_exists(feature_info_path)
    joblib.dump(feature_info, feature_info_path)
    print(f"   [OK] Feature bilgisi kaydedildi: {feature_info_path}")

    print("\n" + "=" * 60)
    print("[OK] MODEL EĞİTİMİ TAMAMLANDI")
    print("=" * 60)
    print("\nUygulamayı çalıştırmak için:")
    print("   streamlit run app.py")


if __name__ == '__main__':
    main()
