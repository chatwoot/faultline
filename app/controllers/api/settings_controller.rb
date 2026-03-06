module API
  class SettingsController < BaseController
    def index
      @settings = current_account.settings.visible
    end

    def update
      permitted = settings_params
      return render_error('Invalid settings payload') if permitted[:settings].blank?

      Setting.transaction do
        permitted[:settings].each do |key, value|
          next if value.nil?

          record = current_account.settings.find_or_initialize_by(key: key.to_s)
          record.update!(value: value.to_s)
        end
      end

      provision_pagerduty_webhooks(permitted[:settings])

      render_success
    rescue ActiveRecord::RecordInvalid => e
      render_error(e.record.errors.full_messages.join(', '))
    end

    def status
      @integrations = integration_status
      @webhooks = current_account.integration_webhooks
    end

    def test_connection
      integration = params.require(:integration)
      valid = %w[openai newrelic sentry aws github pagerduty hetzner]
      return render_error('Invalid integration name') unless valid.include?(integration)

      @result = if integration_status[integration.to_sym]
                  { success: true, message: "#{integration} configuration is valid." }
                else
                  { success: false, message: "Missing required configuration for #{integration}" }
                end
    end

    def destroy_integration
      integration = params[:integration]
      index = params[:index].to_i
      valid = %w[newrelic sentry aws github pagerduty hetzner]
      return render_error('Invalid integration name') unless valid.include?(integration)

      prefix = "#{integration}.#{index}."

      Setting.transaction do
        # Delete all keys for this instance
        current_account.settings.where('key LIKE ?', "#{prefix}%").destroy_all

        # Delete the webhook for this instance
        current_account.integration_webhooks
                       .where(integration: integration, integration_index: index)
                       .destroy_all

        # Reindex higher instances (N+1 → N, N+2 → N+1, etc.)
        higher = current_account.settings.where('key LIKE ?', "#{integration}.%")
                                         .order(:key)

        higher.each do |setting|
          match = setting.key.match(/\A#{Regexp.escape(integration)}\.(\d+)\.(.+)\z/)
          next unless match

          old_index = match[1].to_i
          field = match[2]
          next unless old_index > index

          setting.update_columns(key: "#{integration}.#{old_index - 1}.#{field}")
        end

        # Reindex webhook integration_index values
        current_account.integration_webhooks
                       .where(integration: integration)
                       .where('integration_index > ?', index)
                       .order(:integration_index)
                       .each do |webhook|
          webhook.update_columns(integration_index: webhook.integration_index - 1)
        end
      end

      render_success
    end

    private

    def settings_params
      params.permit(settings: {})
    end

    def provision_pagerduty_webhooks(settings_hash)
      settings_hash.each_key do |key|
        match = key.to_s.match(/\Apagerduty\.(\d+)\.api_key\z/)
        next unless match

        idx = match[1].to_i
        current_account.integration_webhooks.find_or_create_by!(
          integration: 'pagerduty',
          integration_index: idx
        )
      end
    end

    def integration_status
      keys = current_account.settings.visible.pluck(:key)

      {
        openai: keys.include?('openai.api_key'),
        newrelic: keys.any? { |k| k.match?(/\Anewrelic\.\d+\.api_key\z/) },
        sentry: keys.any? { |k| k.match?(/\Asentry\.\d+\.auth_token\z/) },
        aws: keys.any? { |k| k.match?(/\Aaws\.\d+\.access_key_id\z/) },
        github: keys.any? { |k| k.match?(/\Agithub\.\d+\.token\z/) },
        pagerduty: keys.any? { |k| k.match?(/\Apagerduty\.\d+\.api_key\z/) },
        hetzner: keys.any? { |k| k.match?(/\Ahetzner\.\d+\.api_token\z/) }
      }
    end
  end
end
