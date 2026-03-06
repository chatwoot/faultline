class Setting < ApplicationRecord
  belongs_to :account

  encrypts :value

  validates :key, presence: true, uniqueness: { scope: :account_id }
  validates :value, presence: true
  validate :openai_key_format, if: -> { key == 'openai.api_key' }

  SENSITIVE_SUFFIXES = %w[api_key secret_access_key auth_token token api_token].freeze

  scope :visible, -> { where.not('key LIKE ?', '_internal.%') }

  def sensitive?
    SENSITIVE_SUFFIXES.any? { |suffix| key.end_with?(".#{suffix}") }
  end

  def masked_value
    return value unless sensitive?
    return '***' if value.blank? || value.length <= 4

    "***#{value[-4..]}"
  end

  private

  def openai_key_format
    errors.add(:value, 'must start with "sk-"') unless value&.start_with?('sk-')
    errors.add(:value, 'appears to be invalid (too short)') if value && value.length < 20
  end
end
