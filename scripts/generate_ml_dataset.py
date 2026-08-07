import os
import numpy as np
import pandas as pd

def generate_dataset(n_samples=10000, seed=42):
    np.random.seed(seed)
    
    # 1. distance_km: Float, 5-350 km range (Kerala logistics corridor distances)
    distance_km = np.round(5.0 + 345.0 * np.random.beta(2, 3, n_samples), 2)
    
    # 2. traffic_ratio: Float, 1.0-3.5 (congestion_ratio = duration/static_duration)
    traffic_ratio = np.round(1.0 + 2.5 * np.random.beta(1.5, 3.5, n_samples), 2)
    
    # 3. weather_severity: Float, 0.0-1.0 (normalized weather index)
    weather_severity = np.round(np.random.beta(1.5, 3.0, n_samples), 2)
    
    # 4. vehicle_load: Float, 0.1-1.0 (capacity utilization ratio)
    vehicle_load = np.round(np.random.uniform(0.1, 1.0, n_samples), 2)
    
    # 5. driver_fatigue: Float, 0.0-1.0 (HOS fatigue index)
    driver_fatigue = np.round(np.random.beta(2, 4, n_samples), 2)
    
    # 6. historical_delay: Float, 0-30 minutes (historical delay on the edge)
    historical_delay = np.round(np.random.exponential(scale=5.0, size=n_samples), 2)
    historical_delay = np.clip(historical_delay, 0.0, 30.0)
    
    # 7. hub_congestion: Float, 0-25 minutes (hub congestion delay)
    hub_congestion = np.round(np.random.exponential(scale=4.0, size=n_samples), 2)
    hub_congestion = np.clip(hub_congestion, 0.0, 25.0)
    
    # 8. product_type: Categorical - medicine, perishable, furniture, luxury, hazmat, general
    product_types = ['medicine', 'perishable', 'furniture', 'luxury', 'hazmat', 'general']
    product_p = [0.15, 0.25, 0.15, 0.10, 0.10, 0.25]
    product_type = np.random.choice(product_types, size=n_samples, p=product_p)
    
    # 9. priority: Categorical - critical, high, standard, low
    priorities = ['critical', 'high', 'standard', 'low']
    priority_p = [0.15, 0.30, 0.40, 0.15]
    priority = np.random.choice(priorities, size=n_samples, p=priority_p)
    
    # 10. road_quality: Float, 0.5-1.0 (road condition score)
    road_quality = np.round(0.5 + 0.5 * np.random.beta(3, 2, n_samples), 2)
    
    # 11. temperature_celsius: Float, 22-38 (Kerala ambient temp)
    temperature_celsius = np.round(22.0 + 16.0 * np.random.beta(3, 3, n_samples), 1)
    
    # 12. precipitation_mm: Float, 0-80 (tropical rainfall, correlated with weather_severity)
    precipitation_mm = np.round(weather_severity * 70.0 * np.random.uniform(0.7, 1.15, n_samples), 1)
    precipitation_mm = np.clip(precipitation_mm, 0.0, 80.0)
    
    # 13. wind_speed_kmh: Float, 0-60 (correlated with weather_severity)
    wind_speed_kmh = np.round(weather_severity * 50.0 * np.random.uniform(0.7, 1.2, n_samples), 1)
    wind_speed_kmh = np.clip(wind_speed_kmh, 0.0, 60.0)
    
    # 14. is_peak_hour: Boolean (0/1)
    is_peak_hour = np.random.choice([0, 1], size=n_samples, p=[0.65, 0.35])
    
    # 15. active_incidents: Integer, 0-5
    active_incidents = np.random.poisson(lam=0.5, size=n_samples)
    active_incidents = np.clip(active_incidents, 0, 5)
    
    # 16. vehicle_type_risk: Float, 1.0/1.2/1.4 (standard/reefer/hazmat)
    vehicle_type_risk = np.zeros(n_samples, dtype=float)
    for i in range(n_samples):
        pt = product_type[i]
        if pt in ['medicine', 'perishable']:
            vehicle_type_risk[i] = 1.2  # reefer risk
        elif pt == 'hazmat':
            vehicle_type_risk[i] = 1.4  # hazmat risk
        else:
            vehicle_type_risk[i] = 1.0  # standard risk
            
    # TARGET COMPUTATION: actual_delay_minutes
    # Importance weights:
    # 1. Congestion (35%)
    # 2. Weather severity (25%)
    # 3. Active incidents (20%)
    # 4. Driver fatigue (10%)
    # 5. Vehicle utilization (10%)
    
    congestion_component = (traffic_ratio - 1.0) * (distance_km / 30.0) * 10.0 + hub_congestion * 0.6 + historical_delay * 0.35
    weather_component = weather_severity * 20.0 + (precipitation_mm / 80.0) * 12.0 + (wind_speed_kmh / 60.0) * 8.0
    incident_component = active_incidents * 12.0 + np.maximum(0, active_incidents - 2) * 6.0
    fatigue_component = (driver_fatigue ** 1.5) * 18.0
    load_component = (vehicle_load ** 2) * 12.0
    
    # Weighted base delay
    base_delay = (
        0.35 * congestion_component +
        0.25 * weather_component +
        0.20 * incident_component +
        0.10 * fatigue_component +
        0.10 * load_component
    )
    
    # Interaction effects
    rain_peak_interaction = (precipitation_mm > 25.0) * is_peak_hour * 10.0
    road_traffic_interaction = (1.0 - road_quality) * (traffic_ratio - 1.0) * 12.0
    weather_risk_interaction = weather_severity * (vehicle_type_risk - 1.0) * 15.0
    
    # Priority adjustment multiplier
    priority_factor = np.where(priority == 'critical', 0.85,
                      np.where(priority == 'high', 0.95,
                      np.where(priority == 'standard', 1.0, 1.15)))
    
    raw_delay = (base_delay + rain_peak_interaction + road_traffic_interaction + weather_risk_interaction) * priority_factor * vehicle_type_risk
    
    # Realistic Gaussian noise with heteroscedastic scaling
    noise_std = 1.5 + 0.10 * raw_delay
    noise = np.random.normal(0, noise_std)
    
    actual_delay_minutes = raw_delay + noise
    
    # Ensure realistic non-negative continuous values
    actual_delay_minutes = np.maximum(0.0, actual_delay_minutes)
    actual_delay_minutes = np.round(actual_delay_minutes, 2)
    
    df = pd.DataFrame({
        'distance_km': distance_km,
        'traffic_ratio': traffic_ratio,
        'weather_severity': weather_severity,
        'vehicle_load': vehicle_load,
        'driver_fatigue': driver_fatigue,
        'historical_delay': historical_delay,
        'hub_congestion': hub_congestion,
        'product_type': product_type,
        'priority': priority,
        'road_quality': road_quality,
        'temperature_celsius': temperature_celsius,
        'precipitation_mm': precipitation_mm,
        'wind_speed_kmh': wind_speed_kmh,
        'is_peak_hour': is_peak_hour,
        'active_incidents': active_incidents,
        'vehicle_type_risk': vehicle_type_risk,
        'actual_delay_minutes': actual_delay_minutes
    })
    
    return df

def main():
    output_dir = r"c:\Users\GenAITVMSEZUSR56\Desktop\Regional Finals\data"
    os.makedirs(output_dir, exist_ok=True)
    
    csv_path = os.path.join(output_dir, "ml_training_dataset.csv")
    
    print("Generating 10,000 logistics delay prediction records...")
    df = generate_dataset(n_samples=10000, seed=42)
    
    df.to_csv(csv_path, index=False)
    print(f"Dataset successfully saved to: {csv_path}")
    print(f"Total rows: {len(df)}, Total columns: {len(df.columns)}")
    
    print("\n" + "="*60)
    print("SUMMARY STATISTICS FOR NUMERIC COLUMNS")
    print("="*60)
    
    numeric_cols = df.select_dtypes(include=[np.number]).columns
    stats_df = df[numeric_cols].describe(percentiles=[0.05, 0.25, 0.50, 0.75, 0.95, 0.99]).T
    stats_df['iqr'] = stats_df['75%'] - stats_df['25%']
    print(stats_df[['mean', 'std', 'min', '5%', '25%', '50%', '75%', '95%', '99%', 'max', 'iqr']].to_string())
    
    print("\n" + "="*60)
    print("PRODUCT TYPE DISTRIBUTION")
    print("="*60)
    print(df['product_type'].value_counts(normalize=True).to_string())
    
    print("\n" + "="*60)
    print("PRIORITY DISTRIBUTION")
    print("="*60)
    print(df['priority'].value_counts(normalize=True).to_string())

if __name__ == "__main__":
    main()
