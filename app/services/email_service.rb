class EmailService
  class << self
    def send_verification_email(user)
      provider_class.send_verification_email(user)
    rescue => e
      Rails.logger.error "Failed to send verification email for user #{user.id}: #{e.message}"
      # Store failed email for retry if needed
      raise
    end

    def send_workspace_invitation(invite)
      provider_class.send_workspace_invitation(invite)
    rescue => e
      Rails.logger.error "Failed to send workspace invitation #{invite.id}: #{e.message}"
      raise
    end

    private

    def provider_class
      case AppConfig.email_provider
      when 'resend'
        EmailProviders::ResendEmailProvider
      when 'ses'
        EmailProviders::SesEmailProvider
      when 'sendgrid'
        EmailProviders::SendGridEmailProvider
      when 'smtp'
        EmailProviders::ActionMailerProvider
      else
        raise "Unsupported email provider: #{AppConfig.email_provider}"
      end
    end
  end
end