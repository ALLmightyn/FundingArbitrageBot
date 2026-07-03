module.exports = {
  apps: [
    {
      name: "hlcarrybot",
      script: "main.py",
      interpreter: "python",
      cwd: __dirname,
      restart_delay: 5000,
      max_restarts: 10,
      autorestart: true,
      watch: false,
      log_date_format: "YYYY-MM-DD HH:mm:ss",
      out_file: `${__dirname}/logs/out.log`,
      error_file: `${__dirname}/logs/err.log`,
    },
    {
      name: "hlcarrybot-cross",
      script: "main_cross.py",
      interpreter: "python",
      cwd: __dirname,
      restart_delay: 5000,
      max_restarts: 10,
      autorestart: true,
      watch: false,
      kill_timeout: 5000,
      log_date_format: "YYYY-MM-DD HH:mm:ss",
      out_file: `${__dirname}/logs/cross_out.log`,
      error_file: `${__dirname}/logs/cross_err.log`,
    }
  ]
}
