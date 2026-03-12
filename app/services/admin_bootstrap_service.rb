class AdminBootstrapService
  class << self
    def create_admin_if_configured
      return unless AppConfig.admin_email.present?
      return if User.exists?(email: AppConfig.admin_email)

      Rails.logger.info "Creating admin account for #{AppConfig.admin_email}"

      ActiveRecord::Base.transaction do
        user = User.create!(
          email: AppConfig.admin_email.downcase,
          password: AppConfig.admin_password,
          name: 'Administrator',
          email_verified: true,
          email_verification_token: nil,
          email_verification_expires_at: nil
        )

        # Create admin workspace
        account = Account.create!(
          name: 'Admin Workspace',
          slug: generate_unique_slug('admin-workspace')
        )

        AccountMember.create!(
          account: account,
          user: user,
          role: 'owner'
        )

        user.update!(last_active_account_id: account.id)

        Rails.logger.info "Admin account created successfully"
        user
      end
    rescue => e
      Rails.logger.error "Failed to create admin account: #{e.message}"
      raise
    end

    private

    def generate_unique_slug(base_name)
      slug = base_name
      attempt = 0

      while Account.exists?(slug: slug)
        attempt += 1
        slug = "#{base_name}-#{attempt}"
      end

      slug
    end
  end
end