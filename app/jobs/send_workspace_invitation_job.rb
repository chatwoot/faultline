class SendWorkspaceInvitationJob
  include Sidekiq::Job

  sidekiq_options queue: :emails, retry: 3

  def perform(invite_id)
    invite = WorkspaceInvite.find(invite_id)
    return unless invite&.pending?

    EmailService.send_workspace_invitation(invite)
  rescue ActiveRecord::RecordNotFound => e
    Rails.logger.error "Workspace invite not found: #{invite_id}"
    # Don't retry if invite is deleted
  rescue => e
    Rails.logger.error "Failed to send workspace invitation #{invite_id}: #{e.message}"
    raise # Sidekiq will handle retry
  end
end