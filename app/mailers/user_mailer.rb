class UserMailer < ApplicationMailer
  def verification_email(user)
    @user = user
    @verification_url = EmailProviders::BaseEmailProvider.verification_url(user)

    mail(
      to: user.email,
      subject: 'Verify your email address'
    )
  end

  def workspace_invitation(invite)
    @invite = invite
    @invitation_url = EmailProviders::BaseEmailProvider.workspace_invitation_url(invite)

    mail(
      to: invite.email,
      subject: "You're invited to join #{invite.account.name}"
    )
  end
end