<?php
// Copy to config/config.php only for local development. The Debian installer writes
// secrets to /etc/counter-checker/config.php, outside the public repository.
return [
    'app_name' => getenv('APP_NAME') ?: 'Counter Checker',
    'app_url' => getenv('APP_URL') ?: 'http://localhost',
    'app_key' => getenv('APP_KEY') ?: '',
    'admin_user' => getenv('ADMIN_USER') ?: '',
    'admin_password_hash' => getenv('ADMIN_PASSWORD_HASH') ?: '',
    'database' => [
        'host' => getenv('DB_HOST') ?: '127.0.0.1',
        'port' => (int) (getenv('DB_PORT') ?: 3306),
        'name' => getenv('DB_NAME') ?: '',
        'user' => getenv('DB_USER') ?: '',
        'password' => getenv('DB_PASSWORD') ?: '',
    ],
    'mail_storage' => getenv('MAIL_STORAGE') ?: dirname(__DIR__) . '/var/mail',
    'session_secure' => filter_var(getenv('SESSION_SECURE') ?: 'false', FILTER_VALIDATE_BOOL),
];
