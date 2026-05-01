module.exports = {
  apps: [{
    name: "trading-dashboard-api",
    script: "python3",
    args: "-m uvicorn backend.main:app --host 0.0.0.0 --port 8001 --workers 1",
    cwd: "/root/AutoTrade/trading-dashboard",
    interpreter: "none",
    env_production: {
      NODE_ENV: "production",
      DB_PATH: "/root/AutoTrade/trading.db",
      CORS_ORIGINS: '["http://77.42.69.63"]',
    },
    watch: false,
    max_memory_restart: "256M",
    error_file: "/root/AutoTrade/trading-dashboard/error.log",
    out_file: "/root/AutoTrade/trading-dashboard/out.log",
  }]
};
