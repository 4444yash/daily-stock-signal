import os
import sys
import json
import datetime
import yfinance as yf
import pandas as pd
import numpy as np
import xgboost as xgb
import requests

def send_ntfy_message(topic, title, text):
    """Send plain-text notification via ntfy.sh."""
    url = f"https://ntfy.sh/{topic}"
    headers = {
        "Title": title,
        "Tags": "chart_with_upwards_trend,bell"
    }
    try:
        r = requests.post(url, data=text.encode('utf-8'), headers=headers, timeout=10)
        r.raise_for_status()
        print("ntfy notification sent successfully.")
    except Exception as e:
        print(f"Error sending ntfy notification: {e}")

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
    
    df['sma50'] = df['close'].rolling(window=50).mean()
    df['sma20'] = df['close'].rolling(window=20).mean()
    
    sma20 = df['close'].rolling(window=20).mean()
    std20 = df['close'].rolling(window=20).std(ddof=0)
    df['bbw'] = (3.0 * std20 / sma20) * 100
    df['bbw_under_10'] = (df['bbw'] < 10.0).astype(int)
    df['bbw_win'] = df['bbw_under_10'].rolling(window=5).max()
    
    df['volume_avg20'] = df['volume'].rolling(window=20).mean()
    df['volume_spike'] = ((df['volume'] > 1.5 * df['volume_avg20']) & (df['volume_avg20'] > 0)).astype(int)
    
    df['rsi_14'] = calculate_rsi(df)
    df['rsi_prev'] = df['rsi_14'].shift(1)
    df['rsi_diff'] = df['rsi_14'] - df['rsi_prev']
    df['rsi_event'] = ((df['rsi_14'] >= 55.0) & (df['rsi_14'] <= 70.0) & (df['rsi_diff'] > 8.0)).astype(int)
    
    df['high20'] = df['high'].shift(1).rolling(window=20).max()
    df['breakout_20'] = ((df['high'] > df['high20']) & (df['high20'] > 0)).astype(int)
    
    tr = np.zeros(len(df))
    for i in range(len(df)):
        if i == 0:
            tr[i] = high[i] - low[i]
        else:
            tr[i] = max(high[i] - low[i], abs(high[i] - close[i-1]), abs(low[i] - close[i-1]))
    df['tr'] = tr
    df['atr14'] = df['tr'].rolling(window=14).mean()
    df['atr10'] = df['tr'].rolling(window=10).mean()
    
    df['triple_signal'] = ((df['bbw_win'] == 1) & (df['volume_spike'] == 1) & (df['rsi_event'] == 1) & (df['breakout_20'] == 1)).astype(int)
    
    df['s'] = df['bbw_under_10'].cumsum()
    df['s_zero'] = np.where(df['bbw_under_10'] == 0, df['s'], 0)
    df['last_zero_s'] = df['s_zero'].cummax()
    df['days_in_squeeze'] = np.where(df['bbw_under_10'] == 1, df['s'] - df['last_zero_s'], 0)
    
    return df

def clean_multiindex(df):
    """Clean multi-index columns from yfinance response if present."""
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.reset_index()
    df.columns = [col.lower() for col in df.columns]
    return df

def main():
    # Reconfigure stdout to handle UTF-8 printing of emojis on Windows
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass
    print(f"Starting daily scan: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # 1. Paths & Keys setup
    workspace = os.path.dirname(os.path.abspath(__file__))
    watchlist_path = os.path.join(workspace, "watchlist.json")
    active_path = os.path.join(workspace, "active_trades.json")
    
    # Try looking in results directory first, then fallback to current folder
    model_path = os.path.join(workspace, "results", "xgboost_live_model_asymmetric.json")
    if not os.path.exists(model_path):
        model_path = os.path.join(workspace, "xgboost_live_model_asymmetric.json")
        
    ntfy_topic = os.environ.get("NTFY_TOPIC")
    
    # Check if local run or missing keys
    is_local_run = False
    if not ntfy_topic:
        print("WARNING: NTFY_TOPIC environment variable is missing! Running in console-only mode.")
        is_local_run = True
        
    if not os.path.exists(watchlist_path):
        print(f"Error: watchlist.json not found at {watchlist_path}!")
        sys.exit(1)
        
    if not os.path.exists(model_path):
        print(f"Error: model file not found at {model_path}!")
        sys.exit(1)
        
    # 2. Load Watchlist
    with open(watchlist_path, 'r') as f:
        watchlist_data = json.load(f)
    stocks = watchlist_data.get("stocks", [])
    print(f"Loaded {len(stocks)} stocks from watchlist.")
    
    # 3. Load Active Trades
    if os.path.exists(active_path):
        with open(active_path, 'r') as f:
            active_data = json.load(f)
    else:
        active_data = {"last_updated": "", "trades": []}
    active_trades = active_data.get("trades", [])
    print(f"Loaded {len(active_trades)} active trades.")
    
    # 4. Load XGBoost Model
    model = xgb.XGBClassifier()
    model.load_model(model_path)
    feature_cols = [
        "bbw_width_pct", "days_in_squeeze", "volume_multiple", "close_high_ratio",
        "rsi_absolute", "rsi_delta", "atr_pct", "distance_from_50sma",
        "nifty_trend", "nifty_distance_from_50sma", "relative_strength_125", "prior_runup_90"
    ]
    
    # 5. Fetch Nifty 50 data (1 year history)
    print("Downloading Nifty 50 (^NSEI) data...")
    try:
        nifty_df = yf.download("^NSEI", period="1y", progress=False)
        nifty_df = clean_multiindex(nifty_df)
        nifty_df['date_parsed'] = pd.to_datetime(nifty_df['date']).dt.date
        nifty_df = nifty_df.sort_values('date_parsed').reset_index(drop=True)
        nifty_df['sma50'] = nifty_df['close'].rolling(window=50).mean()
    except Exception as e:
        print(f"Critical Error: Failed to download Nifty 50 data: {e}")
        sys.exit(1)
        
    date_to_nifty_idx = {row['date_parsed']: idx for idx, row in nifty_df.iterrows()}
    nifty_closes = nifty_df['close'].values
    nifty_sma50 = nifty_df['sma50'].values
    
    latest_nifty_date = nifty_df.iloc[-1]['date_parsed']
    print(f"Latest Nifty 50 price date: {latest_nifty_date}")
    
    # We will fetch data for all stocks
    new_signals = []
    exited_trades_today = []
    active_positions_status = []
    updated_active_trades = []
    
    # Helper to check if a trade is already active
    active_symbols = {t["symbol"] for t in active_trades}
    
    # 6. Process each stock
    for stock in stocks:
        symbol = stock["symbol"]
        ticker = stock["ticker"]
        batch = stock["batch"]
        
        print(f"Scanning {symbol} ({ticker})...")
        try:
            df = yf.download(ticker, period="1y", progress=False)
            if df.empty or len(df) < 50:
                print(f"  Skipping {symbol}: insufficient data.")
                continue
                
            df = clean_multiindex(df)
            df['date_parsed'] = pd.to_datetime(df['date']).dt.date
            df = df.sort_values('date_parsed').reset_index(drop=True)
            
            # Check if stock data matches the latest Nifty date (today's close available)
            latest_stock_date = df.iloc[-1]['date_parsed']
            if latest_stock_date < latest_nifty_date:
                print(f"  Warning: Ticker {ticker} latest date ({latest_stock_date}) is older than Nifty ({latest_nifty_date})")
            
            # Compute technical indicators
            df = calculate_indicators(df)
            
            # Check active position update if we hold this stock
            if symbol in active_symbols:
                # Find matching active trade
                matching_trade = next(t for t in active_trades if t["symbol"] == symbol)
                entry_date = matching_trade["entry_date"] if "entry_date" in matching_trade else matching_trade["signal_date"]
                entry_price = matching_trade["entry_price"]
                current_stop = matching_trade["current_stop"]
                
                # We update stop and check exit using the latest day's bar
                latest_row = df.iloc[-1]
                latest_date_str = latest_row['date_parsed'].strftime('%Y-%m-%d')
                o_j = float(latest_row['open'])
                h_j = float(latest_row['high'])
                l_j = float(latest_row['low'])
                c_j = float(latest_row['close'])
                atr10_j = float(latest_row['atr10']) if not pd.isna(latest_row['atr10']) else float(latest_row.get('tr', 0))
                
                is_exited = False
                exit_reason = ""
                exit_price = 0.0
                
                # Check gap stop
                if o_j <= current_stop:
                    is_exited = True
                    exit_price = o_j
                    exit_reason = "SL Hit (Open Gap)"
                else:
                    # Update trailing stop
                    tsl_level = (h_j + l_j)/2 - (3.0 * atr10_j)
                    new_stop = max(current_stop, tsl_level)
                    
                    # Check low SL breach
                    if l_j <= new_stop:
                        is_exited = True
                        exit_price = new_stop
                        exit_reason = "SL/TSL Hit"
                    else:
                        current_stop = new_stop
                        
                if is_exited:
                    pnl_pct = (exit_price - entry_price) / entry_price * 100
                    exited_trades_today.append({
                        "symbol": symbol,
                        "entry_price": entry_price,
                        "exit_price": exit_price,
                        "pnl_pct": pnl_pct,
                        "reason": exit_reason,
                        "exit_date": latest_date_str
                    })
                else:
                    pnl_pct = (c_j - entry_price) / entry_price * 100
                    matching_trade["current_stop"] = current_stop
                    matching_trade["latest_price"] = c_j
                    matching_trade["latest_date"] = latest_date_str
                    updated_active_trades.append(matching_trade)
                    active_positions_status.append({
                        "symbol": symbol,
                        "price": c_j,
                        "stop": current_stop,
                        "pnl_pct": pnl_pct
                    })
            
            # Check for new signal today
            latest_row = df.iloc[-1]
            if latest_row['triple_signal'] == 1 and symbol not in active_symbols:
                idx = len(df) - 1
                sig_date = latest_row['date_parsed']
                sig_date_str = sig_date.strftime('%Y-%m-%d')
                
                sig_nifty_idx = date_to_nifty_idx.get(sig_date)
                if sig_nifty_idx is not None:
                    # Extract features
                    bbw_width_pct = latest_row['bbw']
                    days_in_squeeze = int(latest_row['days_in_squeeze'])
                    vol_avg = latest_row['volume_avg20']
                    volume_multiple = latest_row['volume'] / vol_avg if vol_avg > 0 else 1.0
                    denom = latest_row['high'] - latest_row['low']
                    close_high_ratio = (latest_row['close'] - latest_row['low']) / denom if denom > 0 else 1.0
                    rsi_absolute = latest_row['rsi_14']
                    rsi_delta = latest_row['rsi_diff']
                    atr_pct = (latest_row['atr14'] / latest_row['close']) * 100 if latest_row['close'] > 0 else 0
                    s_sma50 = latest_row['sma50']
                    distance_from_50sma = (latest_row['close'] - s_sma50) / s_sma50 if s_sma50 > 0 else 0.0
                    
                    n_close = nifty_closes[sig_nifty_idx]
                    n_sma50 = nifty_sma50[sig_nifty_idx]
                    nifty_trend = 1 if n_close > n_sma50 else 0
                    nifty_distance_from_50sma = (n_close - n_sma50) / n_sma50 if n_sma50 > 0 else 0.0
                    
                    if idx >= 125 and sig_nifty_idx >= 125:
                        stock_ret = latest_row['close'] / df.iloc[idx - 125]['close']
                        nifty_ret = nifty_closes[sig_nifty_idx] / nifty_closes[sig_nifty_idx - 125]
                        rs = stock_ret / nifty_ret
                    else:
                        rs = 1.0
                        
                    if idx >= 90:
                        runup = (latest_row['close'] - df.iloc[idx - 90]['close']) / df.iloc[idx - 90]['close'] * 100
                    else:
                        runup = 0.0
                        
                    features = {
                        "bbw_width_pct": bbw_width_pct,
                        "days_in_squeeze": days_in_squeeze,
                        "volume_multiple": volume_multiple,
                        "close_high_ratio": close_high_ratio,
                        "rsi_absolute": rsi_absolute,
                        "rsi_delta": rsi_delta,
                        "atr_pct": atr_pct,
                        "distance_from_50sma": distance_from_50sma,
                        "nifty_trend": nifty_trend,
                        "nifty_distance_from_50sma": nifty_distance_from_50sma,
                        "relative_strength_125": rs,
                        "prior_runup_90": runup
                    }
                    
                    feat_df = pd.DataFrame([features])[feature_cols]
                    prob = float(model.predict_proba(feat_df)[:, 1][0])
                    
                    if prob >= 0.65:
                        atr10_0 = latest_row['atr10'] if not pd.isna(latest_row['atr10']) else latest_row['close'] * 0.03
                        initial_stop = (latest_row['high'] + latest_row['low'])/2 - (3.0 * atr10_0)
                        
                        new_signals.append({
                            "symbol": symbol,
                            "batch": batch,
                            "signal_date": sig_date_str,
                            "prob": prob,
                            "close_price": float(latest_row['close']),
                            "initial_stop": float(initial_stop)
                        })
                        
        except Exception as e:
            print(f"  Error processing {symbol}: {e}")
            
    # Add new signals to active positions if they exist (simulate entering on next open bar)
    # Since we can't fetch tomorrow's open yet, we queue them to enter tomorrow's open or add them directly to active trades.
    # The standard way to automate is:
    # 1. Send entry alert.
    # 2. Add to active trades using today's close as estimated entry price, or we can update active trades on the next day's run.
    # Let's add them to active trades now so they are monitored on tomorrow's run.
    for sig in new_signals:
        updated_active_trades.append({
            "symbol": sig["symbol"],
            "batch": sig["batch"],
            "entry_date": (latest_nifty_date + datetime.timedelta(days=1)).strftime('%Y-%m-%d'), # tomorrow's date
            "signal_date": sig["signal_date"],
            "prob": sig["prob"],
            "entry_price": sig["close_price"], # estimated entry price
            "current_stop": sig["initial_stop"],
            "latest_price": sig["close_price"],
            "latest_date": sig["signal_date"]
        })
        
    # 7. Save updated active trades
    active_data["last_updated"] = latest_nifty_date.strftime('%Y-%m-%d')
    active_data["trades"] = updated_active_trades
    with open(active_path, 'w') as f:
        json.dump(active_data, f, indent=2)
    print("Saved active_trades.json.")
    
    # 8. Construct clean plain-text ntfy Alert
    title = f"Daily Stock Signal Report - {active_data['last_updated']}"
    alert_text = ""
    
    if new_signals:
        alert_text += "🚀 NEW ENTRY SIGNALS:\n"
        for sig in new_signals:
            alert_text += (
                f"- {sig['symbol']} (Prob: {sig['prob']:.2%})\n"
                f"  Est. Entry: {sig['close_price']:.2f}\n"
                f"  Initial Stop: {sig['initial_stop']:.2f}\n"
            )
        alert_text += "\n"
    else:
        alert_text += "🚀 NEW ENTRY SIGNALS: None today.\n\n"
        
    if exited_trades_today:
        alert_text += "🛑 EXIT SIGNALS TRIGGERED:\n"
        for ex in exited_trades_today:
            icon = "🟢" if ex['pnl_pct'] >= 0 else "🔴"
            alert_text += (
                f"- {icon} {ex['symbol']}\n"
                f"  Exit Price: {ex['exit_price']:.2f}\n"
                f"  P&L: {ex['pnl_pct']:.2f}%\n"
                f"  Reason: {ex['reason']}\n"
            )
        alert_text += "\n"
        
    if active_positions_status:
        alert_text += "📊 ACTIVE POSITIONS STATUS:\n"
        for act in active_positions_status:
            icon = "🟢" if act['pnl_pct'] >= 0 else "🔴"
            alert_text += (
                f"- {act['symbol']}: {act['price']:.2f} (Stop: {act['stop']:.2f} | P&L: {icon} {act['pnl_pct']:.2f}%)\n"
            )
    else:
        alert_text += "📊 ACTIVE POSITIONS STATUS: None active.\n"
        
    print("\n--- NTFY NOTIFICATION PREVIEW ---")
    print(f"Title: {title}")
    print(alert_text)
    
    if not is_local_run:
        send_ntfy_message(ntfy_topic, title, alert_text)

if __name__ == "__main__":
    main()
