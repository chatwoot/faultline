class AuthService
  class << self
    def signup(email:, password:, name:)
      raise 'An account with this email already exists' if User.find_by(email: email)

      auto_confirm = !email_configured?

      ActiveRecord::Base.transaction do
        user = User.create!(
          email: email.downcase,
          password: password,
          name: name,
          email_verified: auto_confirm,
          email_verification_token: auto_confirm ? nil : SecureRandom.hex(32),
          email_verification_expires_at: auto_confirm ? nil : 24.hours.from_now
        )

        # Create default account
        slug = generate_unique_slug("#{name}s-workspace")
        account = Account.create!(name: "#{name}'s Workspace", slug: slug)
        AccountMember.create!(account: account, user: user, role: 'owner')

        user.update!(last_active_account_id: account.id)

        # Send verification email if email is configured
        if !auto_confirm && AppConfig.email_configured?
          SendVerificationEmailJob.perform_async(user.id)
        end

        { user: user_json(user), auto_confirmed: auto_confirm }
      end
    end

    def login(user:, ip_address: nil, user_agent: nil)
      user_accounts = Account.for_user(user.id)
      raise 'No workspace found for this user' if user_accounts.empty?

      active_account = user_accounts.find { |a| a.id == user.last_active_account_id } || user_accounts.first
      user.update!(last_active_account_id: active_account.id) unless user.last_active_account_id == active_account.id

      role = AccountMember.find_by(account_id: active_account.id, user_id: user.id)&.role

      Session.create!(
        user: user,
        ip_address: ip_address,
        user_agent: user_agent,
        last_active_at: Time.current
      )

      {
        user: user_json(user),
        account: account_json(active_account, role),
        role: role
      }
    end

    def verify_email(token)
      user = User.find_by(email_verification_token: token)
      raise 'Invalid verification token' unless user

      if user.email_verification_expires_at && Time.current > user.email_verification_expires_at
        raise 'Verification token has expired'
      end

      user.update!(
        email_verified: true,
        email_verification_token: nil,
        email_verification_expires_at: nil
      )
    end

    def switch_account(user:, account_id:)
      membership = AccountMember.find_by(account_id: account_id, user_id: user.id)
      raise 'You are not a member of this workspace' unless membership

      account = Account.find(account_id)
      user.update!(last_active_account_id: account_id)

      {
        account: account_json(account, membership.role),
        role: membership.role
      }
    end

    def resend_verification(email)
      user = User.find_by(email: email)
      return unless user && !user.email_verified?

      user.update!(
        email_verification_token: SecureRandom.hex(32),
        email_verification_expires_at: 24.hours.from_now
      )

      SendVerificationEmailJob.perform_async(user.id) if AppConfig.email_configured?
    end

    private

    def email_configured?
      AppConfig.email_configured?
    end

    def generate_unique_slug(name)
      base_slug = name.downcase.gsub(/[^a-z0-9]+/, '-').gsub(/^-|-$/, '')[0..47]
      slug = base_slug
      attempt = 0

      while Account.exists?(slug: slug)
        attempt += 1
        slug = "#{base_slug}-#{attempt}"
      end

      slug
    end

    def user_json(user)
      { id: user.id, email: user.email, name: user.name }
    end

    def account_json(account, role = nil)
      json = { id: account.id, name: account.name, slug: account.slug }
      json[:role] = role if role
      json
    end
  end
end
