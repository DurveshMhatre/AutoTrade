module.exports = {
  apps: [{
    name: "trading-dashboard-api",
    script: "uvicorn",
    args: "backend.main:app --host 0.0.0.0 --port 8001 --workers 1",
    cwd: "/opt/trading-dashboard",
    interpreter: "none",
    env_production: {
      NODE_ENV: "production",
      DB_PATH: "/opt/trading_bot/trading.db",
      CORS_ORIGINS: '["http://YOUR_SERVER_IP", "https://YOUR_DOMAIN"]',
    },
    watch: false,
    max_memory_restart: "256M",
    error_file: "/var/log/trading-dashboard/error.log",
    out_file: "/var/log/trading-dashboard/out.log",
  }]
};
