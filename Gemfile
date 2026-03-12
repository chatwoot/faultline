source 'https://rubygems.org'

ruby '~> 3.4'

gem 'pg', '~> 1.5'
gem 'puma', '>= 5.0'
gem 'rails', '~> 8.0'

# Background jobs
gem 'redis', '~> 5.0'
gem 'sidekiq', '~> 8.0'

# Auth
gem 'devise', '~> 4.9'
gem 'devise-jwt', '~> 0.12'

# Frontend
gem 'vite_rails', '~> 3.0'

# Authorization
gem 'pundit', '~> 2.4'

# API
gem 'jbuilder', '~> 2.12'
gem 'oj', '~> 3.16'
gem 'rack-cors', '~> 2.0'

# HTTP client (for Python SSE bridge and email providers)
gem 'faraday', '~> 2.9'

# HTTP client for email providers
gem 'httparty', '~> 0.21'

# AWS SDK for SES (optional)
gem 'aws-sdk-ses', '~> 1.0', require: false

# Encryption
gem 'lockbox', '~> 1.3'

# Timezone data
gem 'tzinfo-data', platforms: %i[windows jruby]

# Boot speed
gem 'bootsnap', require: false

group :development, :test do
  gem 'debug', platforms: %i[mri windows]
  gem 'dotenv-rails', '~> 3.1'
  gem 'rubocop', require: false
  gem 'rubocop-performance', require: false
  gem 'rubocop-rails', require: false
end

group :development do
  gem 'foreman'
end
