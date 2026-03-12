# Centralised environment access — every ENV read goes through here.
# Loaded before the Rails application boots (required in config/application.rb).

module AppConfig
  module_function

  # ── Database ────────────────────────────────────────────────
  def database_url
    ENV.fetch('DATABASE_URL', nil)
  end

  def db_pool
    ENV.fetch('RAILS_MAX_THREADS', 5).to_i
  end

  # ── Redis ───────────────────────────────────────────────────
  def redis_url
    ENV.fetch('REDIS_URL', 'redis://localhost:6379/0')
  end

  # ActionCable uses a separate Redis DB by default
  def cable_redis_url
    ENV.fetch('REDIS_URL', 'redis://localhost:6379/1')
  end

  # ── Security ────────────────────────────────────────────────
  def secret_key_base
    ENV.fetch('SECRET_KEY_BASE')
  end

  def jwt_secret
    ENV.fetch('JWT_SECRET', 'dev-secret-change-in-production')
  end

  # Used only by the data migration to decrypt old custom-encrypted settings
  def encryption_key
    secret_key_base[0..31]
  end

  # ── Agent service ───────────────────────────────────────────
  def agent_service_url
    ENV.fetch('AGENT_SERVICE_URL', 'http://localhost:8000')
  end

  # ── Web server ──────────────────────────────────────────────
  def port
    ENV.fetch('PORT', 3000).to_i
  end

  def rails_env
    ENV.fetch('RAILS_ENV', 'development')
  end

  def rails_max_threads
    ENV.fetch('RAILS_MAX_THREADS', 5).to_i
  end

  def rails_min_threads
    ENV.fetch('RAILS_MIN_THREADS', rails_max_threads).to_i
  end

  def pidfile
    ENV.fetch('PIDFILE', 'tmp/pids/server.pid')
  end

  # ── Sidekiq ─────────────────────────────────────────────────
  def sidekiq_concurrency
    ENV.fetch('SIDEKIQ_CONCURRENCY', 5).to_i
  end

  # ── CORS ────────────────────────────────────────────────────
  def cors_origin
    ENV.fetch('CORS_ORIGIN', 'http://localhost:5173')
  end

  # ── Admin Account Setup ─────────────────────────────────────
  def admin_email
    ENV.fetch('ADMIN_EMAIL', nil)
  end

  def admin_password
    ENV.fetch('ADMIN_PASSWORD', 'admin123')
  end

  # ── Email Provider Configuration ────────────────────────────
  def email_provider
    ENV.fetch('EMAIL_PROVIDER', 'smtp') # smtp, resend, ses, sendgrid
  end

  def email_provider_configured?
    case email_provider
    when 'smtp'
      smtp_host.present?
    when 'resend'
      resend_api_key.present?
    when 'ses'
      aws_access_key_id.present? && aws_secret_access_key.present?
    when 'sendgrid'
      sendgrid_api_key.present?
    else
      false
    end
  end

  # API provider keys
  def resend_api_key
    ENV.fetch('RESEND_API_KEY', nil)
  end

  def sendgrid_api_key
    ENV.fetch('SENDGRID_API_KEY', nil)
  end

  def aws_access_key_id
    ENV.fetch('AWS_ACCESS_KEY_ID', nil)
  end

  def aws_secret_access_key
    ENV.fetch('AWS_SECRET_ACCESS_KEY', nil)
  end

  def aws_region
    ENV.fetch('AWS_REGION', 'us-east-1')
  end

  # ── SMTP / Email (existing) ─────────────────────────────────
  def smtp_host
    ENV.fetch('SMTP_HOST', nil)
  end

  def smtp_port
    ENV.fetch('SMTP_PORT', 587).to_i
  end

  def smtp_user
    ENV.fetch('SMTP_USER', nil)
  end

  def smtp_password
    ENV.fetch('SMTP_PASSWORD', nil)
  end

  def smtp_from
    ENV.fetch('SMTP_FROM', 'noreply@faultline.dev')
  end

  def email_configured?
    email_provider_configured?
  end

  # ── Logging ─────────────────────────────────────────────────
  def log_level
    ENV.fetch('LOG_LEVEL', 'debug').to_sym
  end

  def rails_log_level
    ENV.fetch('RAILS_LOG_LEVEL', 'info').to_sym
  end

  # ── CI ──────────────────────────────────────────────────────
  def ci?
    ENV['CI'].present?
  end
end
