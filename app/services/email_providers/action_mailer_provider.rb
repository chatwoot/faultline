module EmailProviders
  class ActionMailerProvider < BaseEmailProvider
    def self.send_verification_email(user)
      UserMailer.verification_email(user).deliver_now
    end

    def self.send_workspace_invitation(invite)
      UserMailer.workspace_invitation(invite).deliver_now
    end
  end
end