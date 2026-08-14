#!/usr/bin/env php
<?php
declare(strict_types=1);
require dirname(__DIR__) . '/src/bootstrap.php';
require dirname(__DIR__) . '/src/Crypto.php';
require dirname(__DIR__) . '/src/PopClient.php';
require dirname(__DIR__) . '/src/MimeParser.php';
require dirname(__DIR__) . '/src/MailService.php';
session_write_close();
$accounts = $pdo->query('SELECT * FROM pop_accounts WHERE enabled=1')->fetchAll();
$service = new MailService($pdo, $config);
$failed = false;
foreach ($accounts as $account) {
    try { $count = $service->fetch($account); fwrite(STDOUT, sprintf("[%s] %s: %d new\n", date(DATE_ATOM), $account['name'], $count)); }
    catch (Throwable $e) { $failed = true; fwrite(STDERR, sprintf("[%s] account #%d: %s\n", date(DATE_ATOM), $account['id'], $e->getMessage())); }
}
exit($failed ? 1 : 0);
