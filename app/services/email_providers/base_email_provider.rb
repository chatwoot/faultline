module EmailProviders
  class BaseEmailProvider
    def self.verification_url(user)
      # Use the frontend URL from CORS configuration
      frontend_url = AppConfig.cors_origin
      "#{frontend_url}/verify-email?token=#{user.email_verification_token}"
    end

    def self.workspace_invitation_url(invite)
      frontend_url = AppConfig.cors_origin
      "#{frontend_url}/accept-invite?token=#{invite.token}"
    end

    def self.send_verification_email(user)
      raise NotImplementedError, 'Subclasses must implement send_verification_email'
    end

    def self.send_workspace_invitation(invite)
      raise NotImplementedError, 'Subclasses must implement send_workspace_invitation'
    end
  end
end