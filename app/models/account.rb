class Account < ApplicationRecord
  has_many :account_members, dependent: :destroy
  has_many :users, through: :account_members
  has_many :settings, dependent: :destroy
  has_many :conversations, dependent: :destroy
  has_many :resource_maps, dependent: :destroy
  has_many :integration_webhooks, dependent: :destroy
  has_many :workspace_invites, dependent: :destroy

  validates :name, presence: true
  validates :slug, presence: true, uniqueness: true

  scope :for_user, lambda { |user_id|
    joins(:account_members)
      .where(account_members: { user_id: user_id })
      .includes(:account_members)
  }

  def membership_for(user_id)
    account_members.find_by(user_id: user_id)
  end

  def member_role(user_id)
    membership_for(user_id)&.role
  end

  def members_with_users
    account_members.includes(:user).map do |member|
      {
        id: member.id,
        user_id: member.user_id,
        email: member.user.email,
        name: member.user.name,
        avatar_url: member.user.avatar_url,
        role: member.role,
        joined_at: member.created_at
      }
    end
  end
end
