class WorkspaceService
  class << self
    def rename(account_id:, new_name:)
      raise 'Workspace name is required' if new_name.blank?

      trimmed = new_name.strip
      slug = generate_unique_slug(trimmed, exclude_id: account_id)
      account = Account.find(account_id)
      account.update!(name: trimmed, slug: slug)

      { name: trimmed, slug: slug }
    end

    def invite_member(account_id:, invited_by_user_id:, email:, role:)
      raise 'Valid email is required' unless email&.include?('@')
      raise 'Role must be admin or member' unless %w[admin member].include?(role)

      normalized_email = email.downcase
      account = Account.find(account_id)

      if account.members_with_users.any? { |m| m[:email] == normalized_email }
        raise 'This user is already a member of this workspace'
      end

      if WorkspaceInvite.exists?(account_id: account_id, email: normalized_email, status: 'pending')
        raise 'A pending invite already exists for this email'
      end

      invite = WorkspaceInvite.create!(
        account_id: account_id,
        email: normalized_email,
        role: role,
        invited_by_user_id: invited_by_user_id,
        status: 'pending',
        expires_at: 7.days.from_now
      )

      # Send invitation email if email is configured
      SendWorkspaceInvitationJob.perform_async(invite.id) if AppConfig.email_configured?

      { id: invite.id, email: normalized_email, role: role }
    end

    def accept_invite(token:, user_id:)
      invite = WorkspaceInvite.find_by(token: token)
      raise 'Invalid invite token' unless invite
      raise 'This invite has already been accepted' if invite.status == 'accepted'

      if invite.expired?
        invite.expire! unless invite.status == 'expired'
        raise 'This invite has expired'
      end

      user = User.find(user_id)
      raise 'This invite was sent to a different email address' if user.email != invite.email

      existing = AccountMember.find_by(account_id: invite.account_id, user_id: user_id)
      AccountMember.create!(account_id: invite.account_id, user_id: user_id, role: invite.role) unless existing

      invite.accept!

      account = Account.find(invite.account_id)
      { account_id: account.id, account_name: account.name }
    end

    def list_members(account_id)
      Account.find(account_id).members_with_users
    end

    def list_pending_invites(account_id)
      WorkspaceInvite.where(account_id: account_id, status: 'pending').map do |inv|
        { id: inv.id, email: inv.email, role: inv.role, created_at: inv.created_at, expires_at: inv.expires_at }
      end
    end

    def remove_member(account_id:, requesting_user_id:, target_user_id:)
      raise 'You cannot remove yourself from the workspace' if requesting_user_id == target_user_id

      member = AccountMember.find_by(account_id: account_id, user_id: target_user_id)
      raise 'User is not a member of this workspace' unless member

      if member.owner?
        owner_count = AccountMember.where(account_id: account_id, role: 'owner').count
        raise 'Cannot remove the last owner of the workspace' if owner_count <= 1
      end

      member.destroy!
    end

    def update_member_role(account_id:, target_user_id:, new_role:)
      raise 'Invalid role' unless %w[owner admin member].include?(new_role)

      target = AccountMember.find_by(account_id: account_id, user_id: target_user_id)
      raise 'User is not a member of this workspace' unless target

      if target.owner? && new_role != 'owner'
        owner_count = AccountMember.where(account_id: account_id, role: 'owner').count
        raise 'Cannot change the role of the last owner' if owner_count <= 1
      end

      target.update!(role: new_role)
    end

    def cancel_invite(account_id:, invite_id:)
      invite = WorkspaceInvite.find_by(id: invite_id, account_id: account_id)
      raise 'Invite not found' unless invite

      invite.destroy!
    end

    def pending_invites_for_user(email)
      WorkspaceInvite
        .pending
        .for_email(email)
        .includes(:account)
        .select(&:expires_at)
        .reject(&:expired?)
        .map do |inv|
          {
            id: inv.id,
            token: inv.token,
            account_id: inv.account_id,
            account_name: inv.account.name,
            role: inv.role,
            expires_at: inv.expires_at
          }
        end
    end

    private

    def generate_unique_slug(name, exclude_id: nil)
      base_slug = name.downcase.gsub(/[^a-z0-9]+/, '-').gsub(/^-|-$/, '')[0..47]
      slug = base_slug
      attempt = 0

      loop do
        existing = Account.find_by(slug: slug)
        break if existing.nil? || existing.id == exclude_id

        attempt += 1
        slug = "#{base_slug}-#{attempt}"
      end

      slug
    end
  end
end
