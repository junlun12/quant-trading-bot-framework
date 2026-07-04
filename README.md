# 📈 Algorithmic Trading Bot & Backtesting Framework

A production-ready, modular cryptocurrency trading framework built in Python. This repository showcases structural components of a professional quantitative system, focusing on risk management, strategy backtesting, and automated execution.

---

## 🌟 Key Components

### 1. `final_pro.py` (Live Trading Engine)
*   **Purpose:** Active trend-following execution bot using the `ccxt` library configured for Binance Futures.
*   **Key Features:**
    *   Dynamic entry filters utilizing Average True Range (ATR) breakouts.
    *   Strict risk budgeting (stops based on account balance percentage).
    *   Advanced trailing stop mechanism to lock in profits dynamically.

### 2. `BacktestEngine.py` (Vectorized Backtester)
*   **Purpose:** A custom, high-speed backtesting simulator designed to validate strategies against historical data.
*   **Key Features:**
    *   Vectorized indicator calculations using `pandas_ta` for execution speed.
    *   Simulates realistic trading costs (slippage, Binance VIP taker fees).
    *   Outputs detailed analytics reports (Win Rate, Profit Factor, Max Drawdown).

### 3. `alpha_hunter.py` (Low-Drawdown Spot Strategy)
*   **Purpose:** A highly conservative strategy focused on wealth preservation and minimal drawdowns.
*   **Key Features:**
    *   1x leverage configuration (Spot simulator).
    *   Tightened single-trade risk limits (max 2% risk of total balance).
    *   Chandelier exit implementation (ATR-based trailing stop).

### 4. `backtest_result.csv` (Sample Trade Logs)
*   **Purpose:** Real simulation logs demonstrating how the system auto-executes Stop-Loss (SL) and Take-Profit (TP) conditions.

## Usage
*  Configure your Binance Testnet/API keys securely (do not hardcode them in production).
*  Run BacktestEngine.py to test and generate historical reports.
*  Run final_pro.py to start the live trading loop.

---

## ⚙️ Getting Started

### Prerequisites
Install the required dependencies using pip:
```bash
pip install pandas pandas-ta ccxt numpy

