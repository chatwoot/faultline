class SendVerificationEmailJob
  include Sidekiq::Job

  sidekiq_options queue: :emails, retry: 3

  def perform(user_id)
    user = User.find(user_id)
    return unless user&.email_verification_token&.present?

    EmailService.send_verification_email(user)
  rescue ActiveRecord::RecordNotFound => e
    Rails.logger.error "User not found for verification email: #{user_id}"
    # Don't retry if user is deleted
  rescue => e
    Rails.logger.error "Failed to send verification email for user #{user_id}: #{e.message}"
    raise # Sidekiq will handle retry
  end
end