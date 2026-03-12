Rails.application.config.after_initialize do
  # Only run in non-test environments and when the database is ready
  unless Rails.env.test?
    begin
      # Check if database is accessible
      ActiveRecord::Base.connection.execute('SELECT 1')
      AdminBootstrapService.create_admin_if_configured
    rescue ActiveRecord::NoDatabaseError, ActiveRecord::StatementInvalid => e
      # Database not ready, skip admin creation
      Rails.logger.warn "Database not ready for admin bootstrap: #{e.message}"
    rescue => e
      # Log other errors but don't fail startup
      Rails.logger.error "Error during admin bootstrap: #{e.message}"
    end
  end
end