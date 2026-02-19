json.user do
  json.id @user.id
  json.email @user.email
  json.name @user.name
  json.avatar_url @user.avatar_url
end
json.account do
  json.id @account.id
  json.name @account.name
  json.slug @account.slug
  json.role @role
end
json.role @role
json.token @token
