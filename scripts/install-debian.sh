#!/bin/bash
set -euo pipefail
if [[ $EUID -ne 0 ]]; then echo "root 권한으로 실행하세요." >&2; exit 1; fi
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
read_value(){ local prompt="$1" default="$2" value; read -r -p "$prompt [$default]: " value; printf '%s' "${value:-$default}"; }
read_secret(){ local prompt="$1" value; read -r -s -p "$prompt: " value; echo >&2; [[ -n "$value" ]] || { echo "값을 입력해야 합니다." >&2; exit 1; }; printf '%s' "$value"; }
APP_NAME="$(read_value '서비스 표시 이름' 'Counter Checker')"
SERVER_NAME="$(read_value '웹 도메인 또는 서버 IP' 'counter.example.invalid')"
DB_HOST="$(read_value 'MariaDB 호스트' '127.0.0.1')"
DB_PORT="$(read_value 'MariaDB 포트' '3306')"
DB_NAME="$(read_value '데이터베이스 이름' 'counter_checker')"
DB_USER="$(read_value '애플리케이션 DB 사용자' 'counter_app')"
DB_PASSWORD="$(read_secret '애플리케이션 DB 비밀번호')"
DB_ADMIN_USER="$(read_value '스키마 생성용 DB 관리자' 'root')"
DB_ADMIN_PASSWORD="$(read_secret '스키마 생성용 DB 관리자 비밀번호')"
ADMIN_USER="$(read_value '웹 관리자 아이디' 'admin')"
ADMIN_PASSWORD="$(read_secret '웹 관리자 비밀번호')"
SESSION_SECURE="$(read_value 'HTTPS 세션 쿠키 사용(true/false)' 'false')"
[[ "$DB_NAME" =~ ^[A-Za-z0-9_]+$ && "$DB_USER" =~ ^[A-Za-z0-9_]+$ ]] || { echo 'DB 이름과 사용자는 영문, 숫자, 밑줄만 사용할 수 있습니다.' >&2; exit 1; }
[[ "$SERVER_NAME" =~ ^[A-Za-z0-9.-]+$ ]] || { echo '도메인/IP 형식이 올바르지 않습니다.' >&2; exit 1; }
[[ "$SESSION_SECURE" == true || "$SESSION_SECURE" == false ]] || { echo '세션 쿠키 값은 true 또는 false여야 합니다.' >&2; exit 1; }
[[ "$DB_PASSWORD" != *$'\n'* && "$DB_PASSWORD" != *$'\r'* ]] || { echo 'DB 비밀번호에는 줄바꿈을 사용할 수 없습니다.' >&2; exit 1; }
DB_PASSWORD_SQL="${DB_PASSWORD//\\/\\\\}"
DB_PASSWORD_SQL="${DB_PASSWORD_SQL//\'/\'\'}"

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y nginx mariadb-server mariadb-client php-fpm php-cli php-mysql php-mbstring
PHP_VERSION="$(php -r 'echo PHP_MAJOR_VERSION.".".PHP_MINOR_VERSION;')"
APP_KEY="$(php -r 'echo base64_encode(random_bytes(32));')"
ADMIN_HASH="$(php -r 'echo password_hash($argv[1], PASSWORD_DEFAULT);' "$ADMIN_PASSWORD")"
install -d -m 0750 -o www-data -g www-data /etc/counter-checker "$APP_DIR/var/mail"
cat > /etc/counter-checker/config.php <<PHP
<?php
return [
 'app_name' => $(printf '%s' "$APP_NAME" | php -r 'echo var_export(stream_get_contents(STDIN),true);'),
 'app_url' => $(printf 'http://%s' "$SERVER_NAME" | php -r 'echo var_export(stream_get_contents(STDIN),true);'),
 'app_key' => '$APP_KEY',
 'admin_user' => $(printf '%s' "$ADMIN_USER" | php -r 'echo var_export(stream_get_contents(STDIN),true);'),
 'admin_password_hash' => '$ADMIN_HASH',
 'database' => ['host'=>$(printf '%s' "$DB_HOST" | php -r 'echo var_export(stream_get_contents(STDIN),true);'),'port'=>(int)$DB_PORT,'name'=>$(printf '%s' "$DB_NAME" | php -r 'echo var_export(stream_get_contents(STDIN),true);'),'user'=>$(printf '%s' "$DB_USER" | php -r 'echo var_export(stream_get_contents(STDIN),true);'),'password'=>$(printf '%s' "$DB_PASSWORD" | php -r 'echo var_export(stream_get_contents(STDIN),true);')],
 'mail_storage' => '$APP_DIR/var/mail',
 'session_secure' => $SESSION_SECURE,
];
PHP
chmod 0640 /etc/counter-checker/config.php; chown root:www-data /etc/counter-checker/config.php; chown -R www-data:www-data "$APP_DIR/var/mail"
DB_ADMIN_ARGS=(-h "$DB_HOST" -P "$DB_PORT" -u "$DB_ADMIN_USER"); [[ -n "$DB_ADMIN_PASSWORD" ]] && DB_ADMIN_ARGS+=("-p$DB_ADMIN_PASSWORD")
mariadb "${DB_ADMIN_ARGS[@]}" <<SQL
CREATE DATABASE IF NOT EXISTS \`$DB_NAME\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '$DB_USER'@'%' IDENTIFIED BY '$DB_PASSWORD_SQL';
ALTER USER '$DB_USER'@'%' IDENTIFIED BY '$DB_PASSWORD_SQL';
GRANT SELECT, INSERT, UPDATE, DELETE ON \`$DB_NAME\`.* TO '$DB_USER'@'%';
SQL
mariadb "${DB_ADMIN_ARGS[@]}" "$DB_NAME" < "$APP_DIR/database/schema.sql"
cat > /etc/nginx/sites-available/counter-checker <<NGINX
server {
 listen 80;
 server_name $SERVER_NAME;
 root $APP_DIR/public;
 index index.php;
 client_max_body_size 1m;
 location / { try_files \$uri \$uri/ /index.php?\$query_string; }
 location ~ \.php$ { include snippets/fastcgi-php.conf; fastcgi_pass unix:/run/php/php${PHP_VERSION}-fpm.sock; }
 location ~ /\. { deny all; }
}
NGINX
ln -sfn /etc/nginx/sites-available/counter-checker /etc/nginx/sites-enabled/counter-checker
cat > /etc/cron.d/counter-checker <<CRON
*/5 * * * * www-data COUNTER_CHECKER_CONFIG=/etc/counter-checker/config.php /usr/bin/php $APP_DIR/scripts/fetch-mail.php >> /var/log/counter-checker-mail.log 2>&1
CRON
chmod 0644 /etc/cron.d/counter-checker; touch /var/log/counter-checker-mail.log; chown www-data:adm /var/log/counter-checker-mail.log
nginx -t; systemctl enable --now mariadb "php${PHP_VERSION}-fpm" nginx; systemctl reload nginx
echo "설치 완료: http://$SERVER_NAME"
