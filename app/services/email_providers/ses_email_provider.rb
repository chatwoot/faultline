module EmailProviders
  class SesEmailProvider < BaseEmailProvider
    def self.send_verification_email(user)
      ses_client.send_email({
        source: AppConfig.smtp_from,
        destination: {
          to_addresses: [user.email]
        },
        message: {
          subject: {
            data: 'Verify your email address'
          },
          body: {
            html: {
              data: verification_email_html(user)
            }
          }
        }
      })
    end

    def self.send_workspace_invitation(invite)
      ses_client.send_email({
        source: AppConfig.smtp_from,
        destination: {
          to_addresses: [invite.email]
        },
        message: {
          subject: {
            data: "You're invited to join #{invite.account.name}"
          },
          body: {
            html: {
              data: invitation_email_html(invite)
            }
          }
        }
      })
    end

    private

    def self.ses_client
      require 'aws-sdk-ses'

      Aws::SES::Client.new(
        access_key_id: AppConfig.aws_access_key_id,
        secret_access_key: AppConfig.aws_secret_access_key,
        region: AppConfig.aws_region
      )
    end

    def self.verification_email_html(user)
      <<~HTML
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
          <h2 style="color: #333;">Welcome to Faultline!</h2>
          <p>Hi #{user.name},</p>
          <p>Please click the link below to verify your email address:</p>
          <p style="margin: 20px 0;">
            <a href="#{verification_url(user)}" style="background-color: #007bff; color: white; padding: 12px 20px; text-decoration: none; border-radius: 4px; display: inline-block;">
              Verify Email Address
            </a>
          </p>
          <p>If the button above doesn't work, copy and paste this link into your browser:</p>
          <p style="word-break: break-all; color: #666;">#{verification_url(user)}</p>
          <p style="color: #666; font-size: 14px;">This link will expire in 24 hours.</p>
          <hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;">
          <p style="color: #999; font-size: 12px;">If you didn't create this account, you can safely ignore this email.</p>
        </div>
      HTML
    end

    def self.invitation_email_html(invite)
      <<~HTML
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
          <h2 style="color: #333;">You're invited to join #{invite.account.name}</h2>
          <p>Hi there,</p>
          <p>You've been invited to join the workspace "#{invite.account.name}" as a #{invite.role}.</p>
          <p style="margin: 20px 0;">
            <a href="#{workspace_invitation_url(invite)}" style="background-color: #28a745; color: white; padding: 12px 20px; text-decoration: none; border-radius: 4px; display: inline-block;">
              Accept Invitation
            </a>
          </p>
          <p>If the button above doesn't work, copy and paste this link into your browser:</p>
          <p style="word-break: break-all; color: #666;">#{workspace_invitation_url(invite)}</p>
          <p style="color: #666; font-size: 14px;">This invitation will expire in 7 days.</p>
          <hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;">
          <p style="color: #999; font-size: 12px;">If this invitation was sent in error, you can safely ignore this email.</p>
        </div>
      HTML
    end
  end
end