import pandas as pd
import numpy as np
import pandas_ta as ta
import ccxt
import os
import time
import logging
from datetime import datetime, timedelta

# ==========================================
# 1. Global Configuration (Adjustments here)
# ==========================================
CONFIG = {
    # Trading Pair & Data
    "symbol": "BTC/USDT",
    "timeframe": "5m",
    "data_days": 90,           # Fetch last 90 days of data
    "csv_filename": "BTCUSDT_5m.csv",

    # Risk & Capital Management
    "initial_balance": 10000,  # Initial balance 10000U
    "leverage": 5,             # 5x leverage
    "risk_per_trade": 0.02,    # Single trade risk: max loss 2% of account
    "fee_rate": 0.0004,        # Maker/Taker fee 0.04% (Binance VIP0)

    # Strategy Parameters
    "ema_fast": 12,
    "ema_slow": 26,
    "atr_length": 14,
    "atr_sl_mult": 1.5,        # Stop Loss = 1.5x ATR
    "atr_tp_mult": 2.5,        # Take Profit = 2.5x ATR
    "adx_period": 14,
    "adx_threshold": 20        # ADX > 20 required to open (Filter chop)
}

# Logging Setup
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger()

# ==========================================
# 2. Data Fetcher Module
# ==========================================
class DataLoader:
    @staticmethod
    def get_data(config):
        filename = config['csv_filename']
        
        # If local CSV exists, read directly
        if os.path.exists(filename):
            logger.info(f"📂 Found local data {filename}, loading...")
            df = pd.read_csv(filename)
            df['time'] = pd.to_datetime(df['time'])
            return df
        
        # If no local data, download from Binance
        logger.info(f"🌐 No local data, downloading from Binance {config['symbol']} ({config['data_days']} days)...")
        return DataLoader._download_from_binance(config)

    @staticmethod
    def _download_from_binance(config):
        exchange = ccxt.binance({'enableRateLimit': True})
        since = exchange.parse8601((datetime.utcnow() - timedelta(days=config['data_days'])).isoformat())
        
        all_candles = []
        while True:
            try:
                candles = exchange.fetch_ohlcv(config['symbol'], config['timeframe'], since, limit=1000)
                if not candles: break
                
                last_time = candles[-1][0]
                if since == last_time: break
                since = last_time + 1
                all_candles += candles
                
                # Simple progress indicator
                print(f"   Downloaded: {len(all_candles)} candles...", end='\r')
                time.sleep(0.1) # Rate limit protection
                
                if last_time >= time.time() * 1000 - 60000: break
            except Exception as e:
                logger.error(f"Download interrupted: {e}")
                time.sleep(2)
                continue
        
        print("\n✅ Download Complete!")
        df = pd.DataFrame(all_candles, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
        df['time'] = pd.to_datetime(df['time'], unit='ms')
        
        # Save locally for next time
        df.to_csv(config['csv_filename'], index=False)
        return df

# ==========================================
# 3. Core Backtest Engine
# ==========================================
class BacktestEngine:
    def __init__(self, df, config):
        self.df = df.copy()
        self.cfg = config
        
        self.balance = config["initial_balance"]
        self.equity_curve = [self.balance]
        self.position = None 
        self.trades = []      

    def prepare_indicators(self):
        """Vectorized indicator calculation (extremely fast)"""
        # EMA
        self.df['EMA_Fast'] = ta.ema(self.df['close'], length=self.cfg['ema_fast'])
        self.df['EMA_Slow'] = ta.ema(self.df['close'], length=self.cfg['ema_slow'])
        
        # ATR
        self.df['ATR'] = ta.atr(self.df['high'], self.df['low'], self.df['close'], length=self.cfg['atr_length'])
        
        # ADX
        adx_df = ta.adx(self.df['high'], self.df['low'], self.df['close'], length=self.cfg['adx_period'])
        self.df['ADX'] = adx_df[f'ADX_{self.cfg["adx_period"]}']
        
        # Drop NaN values
        self.df.dropna(inplace=True)
        self.df.reset_index(drop=True, inplace=True)

    def calculate_position_size(self, entry_price, sl_price):
        """Fixed fractional risk sizing model"""
        risk_amount = self.balance * self.cfg["risk_per_trade"]
        distance_per_unit = abs(entry_price - sl_price)
        if distance_per_unit == 0: return 0
        
        size = risk_amount / distance_per_unit
        
        # Leverage limit check
        max_size = (self.balance * self.cfg["leverage"]) / entry_price
        return min(size, max_size)

    def run(self):
        logger.info(f"🚀 Starting backtest, dataset size: {len(self.df)} candles")
        
        # Loop through candles
        for i in range(1, len(self.df)):
            curr = self.df.iloc[i]
            prev = self.df.iloc[i-1]
            
            # 1. Record dynamic equity
            self._update_equity(curr)

            # 2. Check position exits (SL/TP)
            if self.position:
                self._check_exit(curr)
                if self.position is None: continue # Closed on this candle, don't reopen

            # 3. Check entry signals
            if self.position is None:
                self._check_entry(curr, prev)

        self._generate_report()

    def _update_equity(self, curr):
        current_equity = self.balance
        if self.position:
            unrealized_pnl = (curr['close'] - self.position['entry']) * self.position['size']
            if self.position['side'] == 'short': unrealized_pnl *= -1
            current_equity += self.position['margin'] + unrealized_pnl
        self.equity_curve.append(current_equity)

    def _check_exit(self, candle):
        pos = self.position
        exit_price, exit_reason = None, None
        
        # Check if SL or TP is hit (assuming bar prices cover SL/TP)
        if pos['side'] == 'long':
            if candle['low'] <= pos['sl']:
                exit_price = pos['sl']
                exit_reason = 'Stop Loss'
            elif candle['high'] >= pos['tp']:
                exit_price = pos['tp']
                exit_reason = 'Take Profit'
        elif pos['side'] == 'short':
            if candle['high'] >= pos['sl']:
                exit_price = pos['sl']
                exit_reason = 'Stop Loss'
            elif candle['low'] <= pos['tp']:
                exit_price = pos['tp']
                exit_reason = 'Take Profit'

        if exit_price:
            self._execute_close(exit_price, exit_reason, candle['time'])

    def _check_entry(self, curr, prev):
        # Filter: ADX
        if curr['ADX'] < self.cfg['adx_threshold']: return

        # Signal: EMA Crossover
        bull_cross = (prev['EMA_Fast'] < prev['EMA_Slow']) and (curr['EMA_Fast'] > curr['EMA_Slow'])
        bear_cross = (prev['EMA_Fast'] > prev['EMA_Slow']) and (curr['EMA_Fast'] < curr['EMA_Slow'])

        if bull_cross:
            sl = curr['close'] - (curr['ATR'] * self.cfg['atr_sl_mult'])
            tp = curr['close'] + (curr['ATR'] * self.cfg['atr_tp_mult'])
            self._execute_open('long', curr['close'], sl, tp, curr['time'])
            
        elif bear_cross:
            sl = curr['close'] + (curr['ATR'] * self.cfg['atr_sl_mult'])
            tp = curr['close'] - (curr['ATR'] * self.cfg['atr_tp_mult'])
            self._execute_open('short', curr['close'], sl, tp, curr['time'])

    def _execute_open(self, side, price, sl, tp, time):
        size = self.calculate_position_size(price, sl)
        margin_needed = (size * price) / self.cfg['leverage']
        
        # Balance check
        if self.balance < margin_needed: return

        # Deduct funds
        self.balance -= margin_needed
        fee = (size * price) * self.cfg['fee_rate']
        self.balance -= fee 

        self.position = {
            'side': side, 'size': size, 'entry': price,
            'sl': sl, 'tp': tp, 'margin': margin_needed,
            'entry_time': time, 'open_fee': fee
        }

    def _execute_close(self, price, reason, time):
        pos = self.position
        
        # Calculate PNL
        raw_pnl = (price - pos['entry']) * pos['size']
        if pos['side'] == 'short': raw_pnl *= -1
        
        close_fee = (pos['size'] * price) * self.cfg['fee_rate']
        net_pnl = raw_pnl - close_fee
        
        # Return funds to balance
        self.balance += pos['margin'] + net_pnl
        
        self.trades.append({
            'entry_time': pos['entry_time'], 'exit_time': time,
            'side': pos['side'], 'entry_price': pos['entry'], 'exit_price': price,
            'pnl': net_pnl, 'reason': reason
        })
        self.position = None

    def _generate_report(self):
        if not self.trades:
            print("⚠️ WARNING: No trades executed during backtest. Check data or relax ADX filter.")
            return

        trades_df = pd.DataFrame(self.trades)
        equity_series = pd.Series(self.equity_curve)
        
        # Calculate core metrics
        total_trades = len(trades_df)
        wins = trades_df[trades_df['pnl'] > 0]
        losses = trades_df[trades_df['pnl'] <= 0]
        
        win_rate = len(wins) / total_trades * 100
        avg_win = wins['pnl'].mean() if not wins.empty else 0
        avg_loss = losses['pnl'].mean() if not losses.empty else 0
        profit_factor = abs(wins['pnl'].sum() / losses['pnl'].sum()) if losses['pnl'].sum() != 0 else 0
        
        # Maximum Drawdown
        peak = equity_series.cummax()
        drawdown = (equity_series - peak) / peak
        max_dd = drawdown.min() * 100

        print("\n" + "="*45)
        print("📊 PROFESSIONAL BACKTEST REPORT")
        print("="*45)
        print(f"Symbol:         {self.cfg['symbol']} ({self.cfg['timeframe']})")
        print(f"Initial Equity: ${self.cfg['initial_balance']:.2f}")
        print(f"Final Equity:   ${self.balance:.2f}")
        print(f"Total Return:   {((self.balance - self.cfg['initial_balance'])/self.cfg['initial_balance'])*100:.2f}%")
        print("-" * 30)
        print(f"🎯 Profit Factor: {profit_factor:.2f} (Target > 1.5)")
        print(f"📉 Max Drawdown:  {max_dd:.2f}% (Target < 20%)")
        print(f"🎲 Win Rate:      {win_rate:.2f}%")
        print(f"🔢 Total Trades:  {total_trades}")
        print("="*45)
        
        # Save trade log
        trades_df.to_csv("backtest_result.csv", index=False)
        print("📝 Detailed trade log saved to 'backtest_result.csv'")

# ==========================================
# 4. Program Entry Point
# ==========================================
if __name__ == "__main__":
    # 1. Fetch/Download data
    df = DataLoader.get_data(CONFIG)
    
    if df is not None and not df.empty:
        # 2. Run Backtest
        engine = BacktestEngine(df, CONFIG)
        engine.prepare_indicators()
        engine.run()
    else:
        print("❌ Data fetch failed, program terminated.")