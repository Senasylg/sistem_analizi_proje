import joblib
import sklearn
import pickle

print(f"Yüklü scikit-learn sürümü: {sklearn.__version__}")

# Modeli yüklemeyi dene
try:
    model = joblib.load('models/pres_suresi_pipeline.pkl')
    print("✅ Model joblib ile yüklendi")
    
    # Modelin tipini ve parametrelerini göster
    print(f"Model tipi: {type(model)}")
    
    # Eğer pipeline ise, son adımın (regressor) tipini göster
    if hasattr(model, 'named_steps'):
        print(f"Regresör: {type(model.named_steps.get('regressor', 'Bilinmiyor'))}")
    
except Exception as e:
    print(f"❌ Model yüklenemedi: {e}")
    
    # pickle ile dene
    try:
        with open('models/pres_suresi_pipeline.pkl', 'rb') as f:
            model = pickle.load(f)
        print("✅ Model pickle ile yüklendi")
    except Exception as e2:
        print(f"❌ Pickle da başarısız: {e2}")