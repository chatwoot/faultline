class User < ApplicationRecord
  include Devise::JWT::RevocationStrategies::JTIMatcher

  devise :database_authenticatable, :registerable, :validatable,
         :jwt_authenticatable, jwt_revocation_strategy: self

  has_many :account_members, dependent: :destroy
  has_many :accounts, through: :account_members
  has_many :sessions, dependent: :destroy

  validates :name, presence: true

  def avatar_url(size = 80)
    gravatar_url(size)
  end

  def jwt_payload
    super.merge(
      'account_id' => last_active_account_id
    )
  end

  private

  def gravatar_url(size)
    hash = Digest::SHA256.hexdigest(email.downcase.strip)
    "https://www.gravatar.com/avatar/#{hash}?s=#{size}&d=identicon&r=g"
  end
end
