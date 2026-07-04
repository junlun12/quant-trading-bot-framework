import ccxt
import pandas as pd
import pandas_ta as ta
import time
import logging
import numpy as np # Import numpy for calculations
from datetime import datetime, timedelta

# ==========================================
# 1. Risk Management Configuration (Safety First)
# ==========================================
STRATEGY_PARAMS = {
    "ema_fast": 50,
    "ema_slow": 200,
    "atr_length": 14,
    
    # --- Core Change 1: Tightened Risk Control ---
    "leverage": 1.0,           # Forced 1x (Spot mode), physical foundation to reduce drawdowns
    "risk_per_trade": 0.02,    # Single trade loss limit 2%
    
    # --- Core Change 2: Trailing Stop Parameters ---
    "atr_sl_mult": 2.0,        # Initial stop loss (wider)
    "trailing_mult": 2.5,      # Trailing stop distance (Exit when price retraces 2.5x ATR)
    
    "adx_period": 14,
    "adx_threshold": 25,
    "initial_balance": 10000,
    "fee_rate": 0.0004
}

# ==========================================
# 2. Upgraded Backtest Engine (with Trailing Stop)
# ==========================================
class Backtest:
    def __init__(self, df, symbol):
        self.df = df.copy()
        self.symbol = symbol
        self.balance = STRATEGY_PARAMS["initial_balance"]
        self.trades = []
        self.position = None
        self.equity = []
        self.peak_balance = self.balance # For drawdown calculation

    def run(self):
        # 1. Prepare Indicators
        df = self.df
        df['EMA_Fast'] = ta.ema(df['close'], length=STRATEGY_PARAMS['ema_fast'])
        df['EMA_Slow'] = ta.ema(df['close'], length=STRATEGY_PARAMS['ema_slow'])
        df['ATR'] = ta.atr(df['high'], df['low'], df['close'], length=STRATEGY_PARAMS['atr_length'])
        adx = ta.adx(df['high'], df['low'], df['close'], length=STRATEGY_PARAMS['adx_period'])
        df['ADX'] = adx[f'ADX_{STRATEGY_PARAMS["adx_period"]}']
        df.dropna(inplace=True)
        df.reset_index(drop=True, inplace=True)

        print(f"🚀 Starting backtest for {self.symbol} (Trailing Stop version)...")

        # 2. Trading Loop
        for i in range(1, len(df)):
            curr = df.iloc[i]
            prev = df.iloc[i-1]
            
            # --- Record Equity and Monitor Drawdown ---
            curr_equity = self.balance
            if self.position:
                # Update floating PNL
                pnl = (curr['close'] - self.position['entry']) * self.position['size']
                if self.position['side'] == 'short': pnl *= -1
                curr_equity += self.position['margin'] + pnl - self.position['open_fee']
            
            self.equity.append(curr_equity)
            self.peak_balance = max(self.peak_balance, curr_equity)

            # --- Core Logic: Check and update SL first, then check exit ---
            if self.position:
                self._update_trailing_sl(curr) # <--- New: Update trailing stop line
                self._check_exit(curr)
                if self.position is None: continue

            # --- Check Entry ---
            if self.position is None:
                self._check_entry(curr, prev)

        self._report()

    def _update_trailing_sl(self, curr):
        """Core: Move trailing stop line based on price volatility (Chandelier Exit)"""
        pos = self.position
        atr_dist = curr['ATR'] * STRATEGY_PARAMS['trailing_mult']
        
        if pos['side'] == 'long':
            # Long: Stop line can only move up, not down
            # New potential stop level = current high - ATR distance
            new_sl = curr['high'] - atr_dist
            if new_sl > pos['sl']:
                pos['sl'] = new_sl
                
        elif pos['side'] == 'short':
            # Short: Stop line can only move down, not up
            # New potential stop level = current low + ATR distance
            new_sl = curr['low'] + atr_dist
            if new_sl < pos['sl']:
                pos['sl'] = new_sl

    def _check_exit(self, curr):
        pos = self.position
        exit_price = None
        reason = ""
        
        # No longer checking TP (Take Profit), only SL (Stop Loss / Trailing Stop)
        if pos['side'] == 'long':
            if curr['low'] <= pos['sl']:
                exit_price = pos['sl'] # Should technically be min(open, sl) for slippage simulation, simplified here
                reason = "Trailing Stop"
        else:
            if curr['high'] >= pos['sl']:
                exit_price = pos['sl']
                reason = "Trailing Stop"

        if exit_price:
            self._execute_close(exit_price, reason)

    def _execute_close(self, price, reason):
        pos = self.position
        raw_pnl = (price - pos['entry']) * pos['size']
        if pos['side'] == 'short': raw_pnl *= -1
        
        close_fee = (pos['size'] * price) * STRATEGY_PARAMS['fee_rate']
        net_pnl = raw_pnl - close_fee - pos['open_fee'] # Deduct 2-way fees
        
        self.balance += pos['margin'] + net_pnl
        self.trades.append(net_pnl)
        self.position = None

    def _check_entry(self, curr, prev):
        if curr['ADX'] < STRATEGY_PARAMS['adx_threshold']: return

        bull = (prev['EMA_Fast'] < prev['EMA_Slow']) and (curr['EMA_Fast'] > curr['EMA_Slow'])
        bear = (prev['EMA_Fast'] > prev['EMA_Slow']) and (curr['EMA_Fast'] < curr['EMA_Slow'])

        if bull or bear:
            side = 'long' if bull else 'short'
            
            # Initial Stop (Hard Stop)
            sl_dist = curr['ATR'] * STRATEGY_PARAMS['atr_sl_mult']
            sl = curr['close'] - sl_dist if side == 'long' else curr['close'] + sl_dist
            
            # Calculate position size (Risk-based)
            risk = self.balance * STRATEGY_PARAMS['risk_per_trade']
            dist = abs(curr['close'] - sl)
            if dist == 0: return
            
            # ⚠️ Critical Fix: Limit maximum leverage
            # Theoretical position size
            risk_size = risk / dist
            # Leverage limit size (Balance * Leverage / Price)
            max_leverage_size = (self.balance * STRATEGY_PARAMS['leverage']) / curr['close']
            
            # Take the smaller of the two (Double insurance)
            size = min(risk_size, max_leverage_size)
            
            margin = (size * curr['close']) / STRATEGY_PARAMS['leverage'] # Under 1x leverage, margin = size*price
            
            if self.balance < margin: return
            self.balance -= margin
            
            # Pre-deduct opening fee
            open_fee = (size * curr['close']) * STRATEGY_PARAMS['fee_rate']
            
            self.position = {
                'side': side, 'size': size, 'entry': curr['close'],
                'sl': sl, 'margin': margin, 'open_fee': open_fee
            }

    def _report(self):
        if not self.trades:
            print(f"❌ {self.symbol}: No trades executed")
            return
            
        wins = [t for t in self.trades if t > 0]
        losses = [t for t in self.trades if t <= 0]
        pf = abs(sum(wins) / sum(losses)) if sum(losses) != 0 else 999
        ret = ((self.balance - STRATEGY_PARAMS['initial_balance']) / STRATEGY_PARAMS['initial_balance']) * 100
        
        # Calculate max drawdown
        s = pd.Series(self.equity)
        dd = ((s - s.cummax()) / s.cummax()).min() * 100

        print(f"\n🛡️ Optimization Results (Trailing Stop version): {self.symbol}")
        print("-" * 30)
        print(f"Profit Factor:  {pf:.2f}")
        print(f"Total Return:   {ret:.2f}%")
        print(f"Max Drawdown:   {dd:.2f}%")
        print(f"Trades:         {len(self.trades)}")
        print("-" * 30)

# ==========================================
# 3. Execution Block
# ==========================================
if __name__ == "__main__":
    try:
        df_eth = pd.read_csv("ETHUSDT_1h.csv")
        df_eth['time'] = pd.to_datetime(df_eth['time'])
        
        bt_eth = Backtest(df_eth, "ETH/USDT")
        bt_eth.run()
    except FileNotFoundError:
        print("Data file not found. Please download data first!")