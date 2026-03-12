require 'active_support/core_ext/integer/time'

Rails.application.configure do
  config.enable_reloading = false
  config.eager_load = true
  config.consider_all_requests_local = false

  config.force_ssl = false
  config.assume_ssl = true

  config.log_tags = [:request_id]
  config.log_level = AppConfig.rails_log_level

  config.action_cable.disable_request_forgery_protection = true

  config.active_record.dump_schema_after_migration = false

  # ActionMailer
  config.action_mailer.perform_deliveries = true
  config.action_mailer.raise_delivery_errors = false
  config.action_mailer.default_url_options = {
    host: ENV.fetch('MAILER_HOST', AppConfig.cors_origin.sub(/^https?:\/\//, '')),
    protocol: ENV.fetch('MAILER_PROTOCOL', 'https')
  }

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
  end
end
