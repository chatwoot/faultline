require 'active_support/core_ext/integer/time'

Rails.application.configure do
  config.enable_reloading = true
  config.eager_load = false
  config.consider_all_requests_local = true

  # ActionCable
  config.action_cable.disable_request_forgery_protection = true

  # Cache store
  config.cache_store = :memory_store

  # Active Record
  config.active_record.migration_error = :page_load
  config.active_record.verbose_query_logs = true

  # Logger
  config.log_level = AppConfig.log_level

  # ActionMailer
  config.action_mailer.perform_deliveries = true
  config.action_mailer.raise_delivery_errors = true
  config.action_mailer.default_url_options = { host: 'localhost', port: AppConfig.port }

  if AppConfig.smtp_host.present?
    config.action_mailer.delivery_method = :smtp
    config.action_mailer.smtp_settings = {
      address: AppConfig.smtp_host,
      port: ENV.fetch('SMTP_PORT', 587).to_i,
      user_name: ENV['SMTP_USER'],
      password: ENV['SMTP_PASSWORD'],
      authentication: ENV.fetch('SMTP_AUTH', 'plain'),
      enable_starttls_auto: ENV.fetch('SMTP_STARTTLS', 'true') == 'true'
    }
  else
    # Use file delivery for development when no SMTP configured
    config.action_mailer.delivery_method = :file
    config.action_mailer.file_settings = { location: Rails.root.join('tmp', 'mail') }
  end
end
