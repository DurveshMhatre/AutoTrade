# Automated Trading Bot

A production-grade automated BTC/USDT trading bot using the Binance exchange.

## Setup Steps

1. **Clone the repository:**
   ```bash
   git clone <repo-url>
   cd Automated_Trading
   ```

2. **Set up the virtual environment:**
   ```bash
   python -m venv automated_trading_env
   # On Windows:
   .\automated_trading_env\Scripts\activate
   # On Unix or MacOS:
   source automated_trading_env/bin/activate
   ```

3. **Install Dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Environment Variables:**
   - Copy the `.env.example` file to `.env`:
     ```bash
     cp .env.example .env
     ```
   - Fill in your API keys in the `.env` file.
   - ⚠️ **WARNING**: Real API keys must NEVER be committed to version control. The `.env` file is ignored by Git, keep it that way for production security.
