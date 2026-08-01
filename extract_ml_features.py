import os
import pandas as pd
import numpy as np
import glob

workspace = r"c:\Users\ASAD\Desktop\v42"
results_dir = os.path.join(workspace, "results")
os.makedirs(results_dir, exist_ok=True)

# Folders to process
folders = [
    {"path": os.path.join(workspace, "data_new"), "name": "Batch 1"},
    {"path": os.path.join(workspace, "data_batch2"), "name": "Batch 2"},
    {"path": os.path.join(workspace, "data_batch3"), "name": "Batch 3"},
    {"path": os.path.join(workspace, "data_batch4"), "name": "Batch 4"},
    {"path": os.path.join(workspace, "data"), "name": "Nifty 50"}
]

def calculate_rsi(df, period=14):
    delta = df['close'].diff()
    gain = delta.clip(lower=0).values
    loss = -delta.clip(upper=0).values
    
    avg_gain = np.zeros(len(df))
    avg_loss = np.zeros(len(df))
    
    if len(df) > period:
        avg_gain[period] = np.mean(gain[1:period+1])
        avg_loss[period] = np.mean(loss[1:period+1])
        for i in range(period+1, len(df)):
            avg_gain[i] = (avg_gain[i-1] * (period - 1) + gain[i]) / period
            avg_loss[i] = (avg_loss[i-1] * (period - 1) + loss[i]) / period
            
    rsi = np.zeros(len(df))
    rsi[:period] = np.nan
    for i in range(period, len(df)):
        if avg_loss[i] == 0:
            rsi[i] = 100 if avg_gain[i] > 0 else 50
        else:
            rs = avg_gain[i] / avg_loss[i]
            rsi[i] = 100 - (100 / (1 + rs))
            
    return rsi

def calculate_indicators(df):
    high = df['high'].values
    low = df['low'].values
    close = df['close'].values
    volume = df['volume'].values
    
    # 50 SMA
    df['sma50'] = df['close'].rolling(window=50).mean()
    
    # BBW Squeeze (1.5 SD, 20-period SMA)
    sma20 = df['close'].rolling(window=20).mean()
    std20 = df['close'].rolling(window=20).std(ddof=0)
    df['bbw'] = (3.0 * std20 / sma20) * 100
    df['bbw_under_10'] = (df['bbw'] < 10.0).astype(int)
    
    # BBW window of 5 days
    df['bbw_win'] = df['bbw_under_10'].rolling(window=5).max()
    
    # Volume spike
    df['volume_avg20'] = df['volume'].rolling(window=20).mean()
    df['volume_spike'] = ((df['volume'] > 1.5 * df['volume_avg20']) & (df['volume_avg20'] > 0)).astype(int)
    
    # RSI Wilder
    df['rsi_14'] = calculate_rsi(df)
    df['rsi_prev'] = df['rsi_14'].shift(1)
    df['rsi_diff'] = df['rsi_14'] - df['rsi_prev']
    df['rsi_event'] = ((df['rsi_14'] >= 55.0) & 
                       (df['rsi_14'] <= 70.0) & 
                       (df['rsi_diff'] > 8.0)).astype(int)
                       
    # 20-day High Breakout
    df['high20'] = df['high'].shift(1).rolling(window=20).max()
    df['breakout_20'] = ((df['high'] > df['high20']) & (df['high20'] > 0)).astype(int)
    
    # ATR14 and ATR10
    tr = np.zeros(len(df))
    for i in range(len(df)):
        if i == 0:
            tr[i] = high[i] - low[i]
        else:
            tr[i] = max(high[i] - low[i], abs(high[i] - close[i-1]), abs(low[i] - close[i-1]))
    df['tr'] = tr
    df['atr14'] = df['tr'].rolling(window=14).mean()
    df['atr10'] = df['tr'].rolling(window=10).mean()
    
    # Quadruple Signal
    df['triple_signal'] = ((df['bbw_win'] == 1) & 
                           (df['volume_spike'] == 1) & 
                           (df['rsi_event'] == 1) & 
                           (df['breakout_20'] == 1)).astype(int)
                           
    return df

def extract_features_at_signal(df, idx):
    row = df.iloc[idx]
    
    bbw_width_pct = row['bbw']
    
    # days_in_squeeze (count backwards from idx or idx - 1 depending on whether idx BBW is under 10)
    bbw_series = df['bbw'].values
    days_in_squeeze = 0
    start_idx = idx if bbw_series[idx] < 10.0 else idx - 1
    while start_idx >= 0 and bbw_series[start_idx] < 10.0:
        days_in_squeeze += 1
        start_idx -= 1
        
    volume_multiple = row['volume'] / row['volume_avg20'] if row['volume_avg20'] > 0 else 0
    
    denom = row['high'] - row['low']
    close_high_ratio = (row['close'] - row['low']) / denom if denom > 0 else 1.0
    
    rsi_absolute = row['rsi_14']
    rsi_delta = row['rsi_diff']
    
    atr_pct = (row['atr14'] / row['close']) * 100 if row['close'] > 0 else 0
    distance_from_50sma = (row['close'] - row['sma50']) / row['sma50'] if row['sma50'] > 0 else 0
    
    return {
        "bbw_width_pct": round(bbw_width_pct, 4),
        "days_in_squeeze": days_in_squeeze,
        "volume_multiple": round(volume_multiple, 4),
        "close_high_ratio": round(close_high_ratio, 4),
        "rsi_absolute": round(rsi_absolute, 4),
        "rsi_delta": round(rsi_delta, 4),
        "atr_pct": round(atr_pct, 4),
        "distance_from_50sma": round(distance_from_50sma, 4)
    }

def simulate_full_trade_pnl(df, entry_idx):
    if entry_idx + 1 >= len(df):
        return None, False
        
    entry_row = df.iloc[entry_idx]
    trade_df = df.iloc[entry_idx + 1:].copy().reset_index(drop=True)
    entry_price = trade_df.iloc[0]['open']
    
    atr10_0 = entry_row['atr10'] if not pd.isna(entry_row['atr10']) else (entry_price * 0.03)
    low0 = entry_row['low']
    high0 = entry_row['high']
    
    sl_price = (high0 + low0)/2 - (3.0 * atr10_0)
    exit_price = None
    hold_limit = 60
    is_resolved = False
    
    for i in range(len(trade_df)):
        row = trade_df.iloc[i]
        day_open = row['open']
        day_high = row['high']
        day_low = row['low']
        day_close = row['close']
        day_atr10 = row['atr10'] if not pd.isna(row['atr10']) else atr10_0
        
        # Check SL on Open gap
        if day_open <= sl_price:
            exit_price = day_open
            is_resolved = True
            break
            
        # Update trailing stop
        tsl_level = (day_high + day_low)/2 - (3.0 * day_atr10)
        sl_price = max(sl_price, tsl_level)
        
        # Check low SL breach
        if day_low <= sl_price:
            exit_price = sl_price
            is_resolved = True
            break
            
        # Check hold limit
        if i >= hold_limit:
            exit_price = day_open
            is_resolved = True
            break
            
    if exit_price is None:
        last_row = trade_df.iloc[-1]
        exit_price = last_row['close']
        if len(trade_df) >= hold_limit:
            is_resolved = True
        else:
            is_resolved = False
            
    pnl_pct = (exit_price - entry_price) / entry_price * 100
    return pnl_pct, is_resolved

def main():
    processed_signals = []
    seen_signals = set()
    
    print("Starting ML data extraction with new multibagger target (PnL > +25%)...")
    
    # Load Nifty 50 index data
    nifty_path = os.path.join(workspace, "data", "NIFTY_50_day.csv")
    nifty_lookup = {}
    nifty_dates = []
    if os.path.exists(nifty_path):
        try:
            nifty_df = pd.read_csv(nifty_path)
            nifty_df['date_parsed'] = pd.to_datetime(nifty_df['date'], utc=True)
            nifty_df = nifty_df.sort_values('date_parsed').reset_index(drop=True)
            nifty_df['sma50'] = nifty_df['close'].rolling(window=50).mean()
            
            date_to_nifty_idx = {pd.to_datetime(row['date']).strftime('%Y-%m-%d'): idx for idx, row in nifty_df.iterrows()}
            nifty_closes = nifty_df['close'].values
            
            for _, row in nifty_df.dropna(subset=['sma50']).iterrows():
                d_str = pd.to_datetime(row['date']).strftime('%Y-%m-%d')
                n_close = row['close']
                n_sma = row['sma50']
                
                trend = 1 if n_close > n_sma else 0
                dist = (n_close - n_sma) / n_sma
                
                nifty_lookup[d_str] = {
                    "nifty_trend": trend,
                    "nifty_distance_from_50sma": round(dist, 4)
                }
            nifty_dates = sorted(nifty_lookup.keys())
            print(f"Loaded Nifty 50 index data ({len(nifty_lookup)} trading days).")
        except Exception as e:
            print(f"Error loading Nifty 50 data: {e}")
    else:
        print("Warning: NIFTY_50_day.csv not found.")
        
    def get_nifty_features(date_str):
        if date_str in nifty_lookup:
            return nifty_lookup[date_str]
        for d in reversed(nifty_dates):
            if d < date_str:
                return nifty_lookup[d]
        return {"nifty_trend": 0, "nifty_distance_from_50sma": 0.0}
    
    for folder in folders:
        path = folder["path"]
        name = folder["name"]
        if not os.path.exists(path):
            print(f"Directory {path} does not exist. Skipping.")
            continue
            
        files = glob.glob(os.path.join(path, "*_day.csv"))
        print(f"Processing {len(files)} files in {name}...")
        
        for f in files:
            symbol = os.path.basename(f).replace("_day.csv", "").upper()
            if symbol == "NIFTY_50":
                continue
                
            try:
                df = pd.read_csv(f)
                if len(df) < 50:
                    continue
                    
                df['date_parsed'] = pd.to_datetime(df['date'], utc=True)
                df = df.sort_values('date_parsed').reset_index(drop=True)
                
                df = calculate_indicators(df)
                sig_df = df[df['triple_signal'] == 1]
                
                for idx, row in sig_df.iterrows():
                    sig_date_str = pd.to_datetime(row['date']).strftime('%Y-%m-%d')
                    sig_year = pd.to_datetime(row['date']).year
                    
                    if sig_year < 2020:
                        continue
                        
                    # Exclude the split-distorted SILVERTUC signal on 2025-10-29
                    if symbol == "SILVERTUC" and sig_date_str == "2025-10-29":
                        continue
                        
                    sig_key = (symbol, sig_date_str)
                    if sig_key in seen_signals:
                        continue
                        
                    # Extract features (up to close of Day 0)
                    features = extract_features_at_signal(df, idx)
                    
                    # Add Nifty features
                    nifty_feats = get_nifty_features(sig_date_str)
                    features["nifty_trend"] = nifty_feats["nifty_trend"]
                    features["nifty_distance_from_50sma"] = nifty_feats["nifty_distance_from_50sma"]
                    
                    # 125-day Stock Relative Strength (RS)
                    sig_nifty_idx = date_to_nifty_idx.get(sig_date_str)
                    if idx >= 125 and sig_nifty_idx is not None and sig_nifty_idx >= 125:
                        stock_ret = df.loc[idx, 'close'] / df.loc[idx - 125, 'close']
                        nifty_ret = nifty_closes[sig_nifty_idx] / nifty_closes[sig_nifty_idx - 125]
                        rs = stock_ret / nifty_ret
                    else:
                        rs = 1.0
                    features["relative_strength_125"] = round(rs, 4)
                    
                    # 90-day Prior Runup
                    if idx >= 90:
                        runup = (df.loc[idx, 'close'] - df.loc[idx - 90, 'close']) / df.loc[idx - 90, 'close'] * 100
                    else:
                        runup = 0.0
                    features["prior_runup_90"] = round(runup, 4)
                    
                    # Calculate actual trade PnL and check if resolved
                    trade_pnl, is_resolved = simulate_full_trade_pnl(df, idx)
                    if not is_resolved or trade_pnl is None:
                        continue
                        
                    # Target Label: 1 if PnL > +25% (multibagger), 0 otherwise
                    label = 1 if trade_pnl > 25.0 else 0
                    
                    # Sanity check for NaN/inf in features
                    has_nan_or_inf = False
                    for val in features.values():
                        if pd.isna(val) or np.isinf(val):
                            has_nan_or_inf = True
                            break
                            
                    if has_nan_or_inf:
                        continue
                        
                    features["symbol"] = symbol
                    features["signal_date"] = sig_date_str
                    features["target"] = label
                    features["trade_pnl"] = round(trade_pnl, 4)
                    features["source_batch"] = name
                    
                    processed_signals.append(features)
                    seen_signals.add(sig_key)
                    
            except Exception as e:
                print(f"Error processing {symbol} in {name}: {e}")
                
    ml_df = pd.DataFrame(processed_signals)
    
    if ml_df.empty:
        print("No trade signals found!")
        return
        
    print(f"\nTotal signals extracted: {len(ml_df)}")
    
    # Class Distribution Check
    dist = ml_df['target'].value_counts()
    win_rate = (dist.get(1, 0) / len(ml_df)) * 100 if len(ml_df) > 0 else 0
    print("\nClass Distribution (Target > +25%):")
    print(f"  Label 0 (Non-multibagger): {dist.get(0, 0)}")
    print(f"  Label 1 (Multibagger >25%): {dist.get(1, 0)}")
    print(f"  Win Rate (Class 1 Ratio): {win_rate:.2f}%")
    
    # Save to CSV
    output_path = os.path.join(results_dir, "xgboost_training_data.csv")
    ml_df.to_csv(output_path, index=False)
    print(f"\nSuccessfully saved ML training dataset to: {output_path}")

if __name__ == "__main__":
    main()
