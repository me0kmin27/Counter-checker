<?php
declare(strict_types=1);

$configFile = getenv('COUNTER_CHECKER_CONFIG') ?: '/etc/counter-checker/config.php';
if (!is_file($configFile)) {
    $configFile = dirname(__DIR__) . '/config/config.php';
}
if (!is_file($configFile)) {
    http_response_code(503);
    exit('설정 파일이 없습니다. scripts/install-debian.sh를 먼저 실행하세요.');
}
$config = require $configFile;
foreach (['app_key', 'admin_user', 'admin_password_hash'] as $required) {
    if (empty($config[$required])) {
        throw new RuntimeException("필수 설정 {$required}이(가) 없습니다.");
    }
}
$db = $config['database'];
$dsn = sprintf('mysql:host=%s;port=%d;dbname=%s;charset=utf8mb4', $db['host'], $db['port'], $db['name']);
$pdo = new PDO($dsn, $db['user'], $db['password'], [
    PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
    PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
    PDO::ATTR_EMULATE_PREPARES => false,
]);
ini_set('session.use_strict_mode', '1');
session_name('counter_checker_session');
session_set_cookie_params(['httponly' => true, 'secure' => $config['session_secure'], 'samesite' => 'Strict']);
session_start();

function h(?string $value): string { return htmlspecialchars($value ?? '', ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8'); }
function csrf_token(): string { return $_SESSION['csrf'] ??= bin2hex(random_bytes(32)); }
function verify_csrf(): void {
    if (!hash_equals($_SESSION['csrf'] ?? '', (string) ($_POST['_token'] ?? ''))) {
        http_response_code(419); exit('잘못된 요청입니다.');
    }
}
function require_login(): void {
    if (empty($_SESSION['authenticated'])) { header('Location: /login'); exit; }
}
function redirect(string $path): never { header('Location: ' . $path, true, 303); exit; }
