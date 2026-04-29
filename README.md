# Automated Trading Bot

A production-grade automated BTC/USDT trading bot using the Binance exchange. The bot utilizes a combination of technical indicators (via `pandas-ta`) and AI-powered decision making (via Anthropic Claude) to orchestrate and execute trend-following trades.

## 🚀 Features
- **AI Orchestrator**: Uses Claude Haiku 4.5 to evaluate market conditions and decide if trading is viable.
- **Trend Agent**: Analyzes technical indicators and generates BUY/SELL/HOLD signals with confidence scoring.
- **Risk Management**: Pre-trade safety layer that enforces daily loss limits, max positions, and dynamically sizes positions with Stop-Loss (SL) and Take-Profit (TP).
- **Execution Engine**: Asynchronous order execution via `ccxt` with full support for Binance Testnet.
- **Monitoring**: Live trade logging and Telegram alerts for trade executions and critical errors.
- **Backtesting Harness**: Simulate the strategy on historical data with detailed performance reporting.

---

## 🛠️ Installation & Setup

1. **Clone the repository:**
   ```bash
   git clone <repo-url>
   cd Automated_Trading
   ```

2. **Set up the virtual environment:**
   It is highly recommended to use a virtual environment to avoid dependency conflicts.
   ```bash
   python -m venv automated_trading_env
   ```

3. **Activate the virtual environment:**
   - On **Windows**:
     ```bash
     .\automated_trading_env\Scripts\activate
     ```
   - On **Unix or MacOS**:
     ```bash
     source automated_trading_env/bin/activate
     ```

4. **Install Dependencies:**
   Make sure your virtual environment is activated, then run:
   ```bash
   pip install -r requirements.txt
   ```

---

## ⚙️ Configuration

1. **Environment Variables:**
   Copy the provided `.env.example` file to create your own `.env` file:
   ```bash
   cp .env.example .env
   ```
   *(On Windows Command Prompt, use `copy .env.example .env`)*

2. **Fill in the API Keys:**
   Open the `.env` file and provide the required keys:
   ```env
   ANTHROPIC_API_KEY=your_anthropic_api_key
   BINANCE_API_KEY=your_binance_api_key
   BINANCE_SECRET=your_binance_secret_key
   BINANCE_TESTNET=true
   TELEGRAM_BOT_TOKEN=your_telegram_bot_token
   TELEGRAM_CHAT_ID=your_telegram_chat_id
   ```
   > ⚠️ **WARNING**: Real API keys must NEVER be committed to version control. The `.env` file is ignored by Git, keep it that way for production security.

3. **Trading Parameters:**
   You can adjust trading parameters like `TIMEFRAME`, `CANDLE_LIMIT`, `MAX_POSITIONS`, `RISK_PER_TRADE_PCT`, `STOP_LOSS_PCT`, and `TAKE_PROFIT_PCT` directly in `config.py`.

---

## 🏃‍♂️ How to Run

### 1. Running the Backtester
The backtester allows you to simulate the trading strategy against historical data to evaluate performance metrics like win rate, profit factor, and max drawdown.

Make sure your virtual environment is activated, then run:
```bash
python backtest.py
```
*Note: The backtester uses a local rule-based engine by default to avoid burning AI API credits on historical candles.*

### 2. Running the Live Bot
To run the automated trading bot in live or testnet mode (as configured by `BINANCE_TESTNET=true` in your `.env`):

Make sure your virtual environment is activated, then run:
```bash
python main.py
```
The bot will run in an infinite loop, fetching data every cycle, evaluating trades, executing them if approved, and sleeping based on the configured timeframe rhythm.

### 3. Running the Test Suite
To ensure all components are functioning correctly, run the integration tests using `pytest`:

```bash
python -m pytest tests/
```
*(This will run `test_orchestrator.py`, `test_trend_agent.py`, etc.)*

---

## 🏗️ Architecture Overview

- `main.py`: The core event loop tying all modules together.
- `backtest.py`: Historical simulation engine and reporting.
- `config.py`: Global trading parameters.
- `core/`: 
  - `data_feed.py`: Async Binance data fetching and streaming.
  - `indicators.py`: Technical indicator computations using `pandas-ta`.
  - `risk_manager.py`: Position sizing and hard-block safety rules.
  - `database.py`: SQLite persistence for candles, trades, and agent decisions.
- `agents/`: 
  - `orchestrator.py`: AI-based high-level market viability gate.
  - `trend_agent.py`: AI-based signal generation.
- `execution/`:
  - `executor.py`: Async order placement and management.
- `monitoring/`:
  - `telegram_alerts.py`: Integration with Telegram Bot API for real-time notifications.
