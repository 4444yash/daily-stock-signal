import os
import pandas as pd
import numpy as np
import xgboost as xgb

workspace = r"c:\Users\ASAD\Desktop\v42"
results_dir = os.path.join(workspace, "results")
os.makedirs(results_dir, exist_ok=True)

def main():
    # 1. Load and Sort Dataset Chronologically
    data_path = os.path.join(results_dir, "xgboost_training_data.csv")
    if not os.path.exists(data_path):
        print(f"Error: {data_path} not found. Please run feature extraction first.")
        return
        
    df = pd.read_csv(data_path)
    df['signal_date'] = pd.to_datetime(df['signal_date'])
    df = df.sort_values('signal_date').reset_index(drop=True)
    df['year_month'] = df['signal_date'].dt.to_period('M')
    
    print(f"Loaded {len(df)} trades from {df['signal_date'].min().strftime('%Y-%m-%d')} to {df['signal_date'].max().strftime('%Y-%m-%d')}.")
    
    # 2. Define Features and Target
    feature_cols = [
        "bbw_width_pct", "days_in_squeeze", "volume_multiple", "close_high_ratio",
        "rsi_absolute", "rsi_delta", "atr_pct", "distance_from_50sma",
        "nifty_trend", "nifty_distance_from_50sma", "relative_strength_125", "prior_runup_90"
    ]
    target_col = "target"
    pnl_col = "trade_pnl"
    
    # 3. Setup Walk-Forward monthly refit protocol
    unique_months = sorted(df['year_month'].unique())
    window_size = 300
    
    # Find the first month where we have at least W trades resolved (at least 60 days old) prior to it
    first_test_month_idx = None
    for idx, month in enumerate(unique_months):
        test_start_date = pd.Period(month, freq='M').start_time
        prior_resolved_count = len(df[df['signal_date'] < (test_start_date - pd.Timedelta(days=60))])
        if prior_resolved_count >= window_size:
            first_test_month_idx = idx
            break
            
    if first_test_month_idx is None:
        print(f"Error: Not enough resolved data prior to any month to satisfy window size of {window_size} trades.")
        return
        
    test_months = unique_months[first_test_month_idx:]
    print(f"Starting asymmetric walk-forward monthly refit from {test_months[0]} to {test_months[-1]} ({len(test_months)} months).")
    
    all_test_predictions = []
    
    for month in test_months:
        # Get prior trades for training (Rolling Window with strict 60d resolution buffer)
        test_start_date = pd.Period(month, freq='M').start_time
        train_df_all = df[df['signal_date'] < (test_start_date - pd.Timedelta(days=60))]
        train_df = train_df_all.tail(window_size).copy()
        
        # Get current month trades for testing (Out-of-Sample)
        test_df = df[df['year_month'] == month].copy()
        
        if len(test_df) == 0:
            continue
            
        X_train = train_df[feature_cols]
        y_train = train_df[target_col]
        
        X_test = test_df[feature_cols]
        
        # Dynamic calculation of scale_pos_weight to handle class imbalance
        num_pos = sum(y_train == 1)
        num_neg = sum(y_train == 0)
        scale_weight = float(num_neg) / num_pos if num_pos > 0 else 1.0
        
        # Calculate Asymmetric weights:
        pnl = train_df[pnl_col].values
        train_weights = np.ones(len(train_df))
        
        # Multibaggers (pnl > 25.0) -> scaled up
        pos_mask = pnl > 25.0
        train_weights[pos_mask] = 1.0 + np.clip(pnl[pos_mask] / 15.0, 0, 3.0)
        
        # Losses (pnl < 0) -> scaled up
        neg_mask = pnl < 0
        train_weights[neg_mask] = 1.0 + np.clip(np.abs(pnl[neg_mask]) / 15.0, 0, 3.0)
        
        # Near-misses (0 <= pnl <= 25.0) stay at 1.0 (no penalty)
        
        # Train XGBoost Classifier
        model = xgb.XGBClassifier(
            max_depth=3,
            learning_rate=0.05,
            n_estimators=100,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            eval_metric="logloss",
            scale_pos_weight=scale_weight
        )
        model.fit(X_train, y_train, sample_weight=train_weights)
        
        # Predict probabilities of success (class 1)
        probs = model.predict_proba(X_test)[:, 1]
        test_df['predicted_prob'] = probs
        
        all_test_predictions.append(test_df)
        
    # Combine out-of-sample test results
    oos_df = pd.concat(all_test_predictions).reset_index(drop=True)
    
    oos_output_path = os.path.join(results_dir, "xgboost_oos_predictions_asymmetric.csv")
    oos_df.to_csv(oos_output_path, index=False)
    print(f"Saved out-of-sample asymmetric predictions to: {oos_output_path}")
    
    # 4. Multi-Threshold Performance Evaluation
    thresholds = [0.0, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80]
    results_summary = []
    
    print("\n" + "="*80)
    print("  OUT-OF-SAMPLE ROLL-FORWARD PERFORMANCE BY CONFIDENCE THRESHOLD (ASYMMETRIC)")
    print("  (TARGET: UNCAPPED 60-DAY PNL > +25%)")
    print("="*80)
    
    for t in thresholds:
        filtered_trades = oos_df[oos_df['predicted_prob'] >= t] if t > 0 else oos_df
        
        trade_count = len(filtered_trades)
        if trade_count == 0:
            results_summary.append({
                "Threshold": f">={t*100:.0f}%", "Trades": 0, "Win Rate (L1)": "0.0%", "Win Rate (PnL)": "0.0%",
                "Net PnL": "+0.00%", "Avg PnL": "+0.00%", "Profit Factor": "0.00"
            })
            continue
            
        l1_wins = len(filtered_trades[filtered_trades[target_col] == 1])
        win_rate_l1 = (l1_wins / trade_count) * 100
        
        pnl_wins = len(filtered_trades[filtered_trades[pnl_col] > 0])
        win_rate_pnl = (pnl_wins / trade_count) * 100
        
        net_pnl = filtered_trades[pnl_col].sum()
        avg_pnl = filtered_trades[pnl_col].mean()
        
        gross_wins = filtered_trades[filtered_trades[pnl_col] > 0][pnl_col].sum()
        gross_losses = abs(filtered_trades[filtered_trades[pnl_col] <= 0][pnl_col].sum())
        profit_factor = gross_wins / gross_losses if gross_losses > 0 else (99.99 if gross_wins > 0 else 1.0)
        
        results_summary.append({
            "Threshold": "Baseline" if t == 0.0 else f">={t*100:.0f}%",
            "Trades": trade_count,
            "Win Rate (L1)": f"{win_rate_l1:.1f}%",
            "Win Rate (PnL)": f"{win_rate_pnl:.1f}%",
            "Net PnL": f"{net_pnl:+.2f}%",
            "Avg PnL": f"{avg_pnl:+.2f}%",
            "Profit Factor": f"{profit_factor:.2f}"
        })
        
    summary_df = pd.DataFrame(results_summary)
    print(summary_df.to_string(index=False))
    print("="*80 + "\n")
    
    # Save final live production model with asymmetric weights
    print(f"Training final asymmetric live production model on the most recent {window_size} trades...")
    final_train_df = df.tail(window_size).copy()
    X_final = final_train_df[feature_cols]
    y_final = final_train_df[target_col]
    
    num_pos_final = sum(y_final == 1)
    num_neg_final = sum(y_final == 0)
    scale_weight_final = float(num_neg_final) / num_pos_final if num_pos_final > 0 else 1.0
    
    pnl_final = final_train_df[pnl_col].values
    final_weights = np.ones(len(final_train_df))
    
    pos_mask_final = pnl_final > 25.0
    final_weights[pos_mask_final] = 1.0 + np.clip(pnl_final[pos_mask_final] / 15.0, 0, 3.0)
    
    neg_mask_final = pnl_final < 0
    final_weights[neg_mask_final] = 1.0 + np.clip(np.abs(pnl_final[neg_mask_final]) / 15.0, 0, 3.0)
    
    final_model = xgb.XGBClassifier(
        max_depth=3,
        learning_rate=0.05,
        n_estimators=100,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=42,
        eval_metric="logloss",
        scale_pos_weight=scale_weight_final
    )
    final_model.fit(X_final, y_final, sample_weight=final_weights)
    
    model_output_path = os.path.join(results_dir, "xgboost_live_model_asymmetric.json")
    final_model.save_model(model_output_path)
    print(f"Successfully saved asymmetric live production model to: {model_output_path}")

if __name__ == "__main__":
    main()
