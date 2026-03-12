class ApplicationMailer < ActionMailer::Base
  default from: -> { AppConfig.smtp_from }
  layout 'mailer'
end