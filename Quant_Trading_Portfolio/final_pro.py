import sys
import os
import time
import ccxt
import logging
import statistics
import math
from datetime import datetime

# ==========================================
# 1. V19.1 Survival Edition Configuration (5m timeframe)
# ==========================================
# 🔴 SECURITY WARNING: NEVER put your real API keys in this file. 
# Use environment variables or a separate config file in production.
DEMO_API_KEY = '' 
DEMO_SECRET_KEY = '' 

CONFIG = {
    'symbol': 'BTC/USDT',
    'leverage': 10,            # 🔴 Lower Leverage: 5m is volatile, 10x is safer
    
    # 🔥 Core Change 1: Upgrade to 5-minute timeframe
    'timeframe': '5m',         
    'check_interval': 10,      # No need to check every few seconds, 10 seconds is enough

    # 🔥 Core Change 2: Increase entry threshold (Based on 5m ATR)
    'entry_atr_filter': 1.0,   # Price must deviate from EMA by 1.0 ATR (Requires genuine breakout)
    'min_trend_atr_mult': 0.6, # Trend strength must be > 0.6 ATR (Rejects sideways chop)
    
    # --- Risk Management (Inherited from V19 Safety Patch) ---
    'risk_per_trade': 0.01,    # Max loss per trade: 1% of total balance
    'max_leverage_cap': 3.0,   # Hard Cap: 3x position size leverage (e.g., 5000U -> Max 15000U position)
    'stop_loss_atr': 2.0,      # Stop-Loss band: 2 ATR
    
    # --- Strategy Parameters ---
    'ema_fast': 9,
    'ema_slow': 21,
    'atr_period': 14,
    'directional_cooldown': 900 # Cooldown extended to 15 minutes (3 candles)
}

# Log File Path
LOG_FILE = os.path.expanduser("~/bot/v19_1_5m.log")
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)

class SurvivalHunter:
    def __init__(self):
        logging.info("="*50)
        logging.info("🌲 V19.1 Survival Edition Initialized (5m timeframe)")
        logging.info(f"🛡️ Mode: Trend Following | Timeframe: 5m | Hard Cap: {CONFIG['max_leverage_cap']}x")
        logging.info("="*50)
        
        self.connect_exchange()
        
        self.state = {
            'position': None, 'entry_price': 0.0, 'amount': 0.0,
            'current_stop': 0.0, 'initial_stop': 0.0, 'risk_dist': 0.0,
            'last_exit_side': None, 'last_exit_time': 0, 'exit_reason': None
        }

    def connect_exchange(self):
        try:
            self.exchange = ccxt.binance({
                'apiKey': DEMO_API_KEY,
                'secret': DEMO_SECRET_KEY,
                'enableRateLimit': True,
                'options': {'defaultType': 'future'}
            })
            self.exchange.enable_demo_trading(True)
            self.exchange.load_markets()
            self.exchange.set_leverage(CONFIG['leverage'], CONFIG['symbol'])
            bal = self.exchange.fetch_balance()['total']['USDT']
            logging.info(f"✅ Account Ready | Balance: {bal:.2f} USDT")
        except Exception as e:
            logging.error(f"❌ Initialization Failed: {e}"); sys.exit()

    def calculate_indicators(self, ohlcv):
        confirmed_candles = ohlcv[:-1] # Wait for candle close confirmation
        closes = [x[4] for x in confirmed_candles]
        highs = [x[2] for x in confirmed_candles]
        lows = [x[3] for x in confirmed_candles]
        
        if len(closes) < 50: return None
        
        def get_ema(data, period):
            alpha = 2 / (period + 1)
            ema = statistics.mean(data[:period])
            for p in data[period:]:
                ema = (p * alpha) + (ema * (1 - alpha))
            return ema

        ema_f = get_ema(closes, CONFIG['ema_fast'])
        ema_s = get_ema(closes, CONFIG['ema_slow'])
        
        # ATR Calculation
        tr_list = []
        for i in range(1, len(closes)):
            h, l, pc = highs[i], lows[i], closes[i-1]
            tr = max(h - l, abs(h - pc), abs(l - pc))
            tr_list.append(tr)
        atr = statistics.mean(tr_list[-CONFIG['atr_period']:])
        
        return {
            'close': closes[-1],
            'ema_f': ema_f, 'ema_s': ema_s, 'atr': atr,
            'trend': 'bull' if ema_f > ema_s else 'bear'
        }

    def calculate_safe_quantity(self, price, stop_distance):
        try:
            balance = self.exchange.fetch_balance()['total']['USDT']
            # 1. Risk Model: Based on max loss per trade
            qty_risk = (balance * CONFIG['risk_per_trade']) / stop_distance
            # 2. Hard Cap Model: Max total leverage allowed
            qty_cap = (balance * CONFIG['max_leverage_cap']) / price
            # 3. Take the smaller, safer quantity
            final_qty = min(qty_risk, qty_cap)
            
            symbol = CONFIG['symbol']
            qty_str = self.exchange.amount_to_precision(symbol, final_qty)
            if float(qty_str) < self.exchange.markets[symbol]['limits']['amount']['min']:
                return None
            return qty_str
        except: return None

    def place_order(self, side, quantity, params={}):
        try:
            return self.exchange.create_order(CONFIG['symbol'], 'market', side, quantity, None, params)
        except Exception as e:
            logging.error(f"❌ Order Exception: {e}"); return None

    def close_position(self, reason):
        if not self.state['position']: return
        logging.info(f"🔄 Closing Position: {reason}")
        side = 'sell' if self.state['position'] == 'buy' else 'buy'
        self.place_order(side, self.state['amount'], params={'reduceOnly': True})
        
        self.state['last_exit_side'] = self.state['position']
        self.state['last_exit_time'] = time.time()
        self.state['exit_reason'] = reason
        self.state['position'] = None; self.state['entry_price'] = 0

    def open_position(self, side, price, atr):
        # Directional Cooldown Check
        if self.state['last_exit_side'] == side:
            elapsed = time.time() - self.state['last_exit_time']
            if elapsed < CONFIG['directional_cooldown']:
                logging.info(f"❄️ {side} Cooling down ({int(CONFIG['directional_cooldown']-elapsed)}s)"); return

        stop_dist = atr * CONFIG['stop_loss_atr']
        qty = self.calculate_safe_quantity(price, stop_dist)
        if not qty: return

        order = self.place_order(side, qty)
        if order:
            avg = float(order['average']) if order['average'] else price
            self.state['position'] = side
            self.state['entry_price'] = avg
            self.state['amount'] = qty
            self.state['risk_dist'] = stop_dist
            self.state['initial_stop'] = avg - stop_dist if side == 'buy' else avg + stop_dist
            self.state['current_stop'] = self.state['initial_stop']
            logging.info(f"⚡ 5m Trend Entry {side} @ {avg} | Stop-Loss: {self.state['current_stop']:.2f}")

    def manage_trailing(self, curr):
        if not self.state['position']: return
        entry, risk, stop = self.state['entry_price'], self.state['risk_dist'], self.state['current_stop']
        
        # 5m Trend allows for larger swings, widening trailing steps
        if self.state['position'] == 'buy':
            r = (curr - entry) / risk
            if r > 6.0: new = entry + (4.0 * risk) # 6R locks 4R
            elif r > 4.0: new = entry + (2.0 * risk) # 4R locks 2R
            elif r > 2.0: new = entry * 1.001        # 2R moves to breakeven
            else: new = stop
            if new > stop: 
                self.state['current_stop'] = new
                logging.info(f"🔒 Profit Locked: {new:.2f} (Current {r:.1f}R)")
                
        else: # sell
            r = (entry - curr) / risk
            if r > 6.0: new = entry - (4.0 * risk)
            elif r > 4.0: new = entry - (2.0 * risk)
            elif r > 2.0: new = entry * 0.999
            else: new = stop
            if new < stop:
                self.state['current_stop'] = new
                logging.info(f"🔒 Profit Locked: {new:.2f} (Current {r:.1f}R)")

    def run(self):
        logging.info("🌲 Waiting for 5m candle close confirmation...")
        while True:
            try:
                # Fetch Data
                ohlcv = self.exchange.fetch_ohlcv(CONFIG['symbol'], CONFIG['timeframe'], limit=100)
                data = self.calculate_indicators(ohlcv)
                curr_price = self.exchange.fetch_ticker(CONFIG['symbol'])['last']
                
                if not data: time.sleep(10); continue

                # Display Status
                pnl_str = "No Position"
                if self.state['position']:
                    self.manage_trailing(curr_price)
                    entry = self.state['entry_price']
                    pnl = (curr_price - entry)/entry if self.state['position'] == 'buy' else (entry - curr_price)/entry
                    pnl_str = f"{self.state['position']} Floating PNL: {pnl*100:.2f}%"

                print(f"\r⏳ P:{curr_price:.0f} | 5m Trend: {data['trend']} | Strength: {abs(data['ema_f']-data['ema_s'])/data['atr']:.1f}x | {pnl_str}    ", end="")

                # --- Core Logic ---
                # 1. Close Position
                if self.state['position']:
                    stop = self.state['current_stop']
                    if (self.state['position'] == 'buy' and curr_price < stop) or \
                       (self.state['position'] == 'sell' and curr_price > stop):
                        print(""); self.close_position("Stop-Loss/Take-Profit Triggered")
                    
                    # Trend Reversal Exit
                    elif (self.state['position'] == 'buy' and data['ema_f'] < data['ema_s']) or \
                         (self.state['position'] == 'sell' and data['ema_f'] > data['ema_s']):
                         print(""); self.close_position("5m Trend Reversed")

                # 2. Open Position (Only if flat)
                elif self.state['position'] is None:
                    diff = abs(data['ema_f'] - data['ema_s'])
                    # Strict Entry Threshold
                    if diff > (data['atr'] * CONFIG['min_trend_atr_mult']):
                        if data['trend'] == 'bull':
                            if data['close'] > data['ema_f'] + (data['atr'] * CONFIG['entry_atr_filter']):
                                print(""); self.open_position('buy', curr_price, data['atr'])
                        elif data['trend'] == 'bear':
                            if data['close'] < data['ema_f'] - (data['atr'] * CONFIG['entry_atr_filter']):
                                print(""); self.open_position('sell', curr_price, data['atr'])
                
                time.sleep(CONFIG['check_interval'])

            except KeyboardInterrupt: break
            except Exception as e: logging.error(f"Error: {e}"); time.sleep(10)

if __name__ == "__main__":
    SurvivalHunter().run()