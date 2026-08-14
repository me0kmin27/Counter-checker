<?php
declare(strict_types=1);
require dirname(__DIR__) . '/src/Crypto.php';
require dirname(__DIR__) . '/src/MimeParser.php';
function check(bool $condition, string $message): void { if (!$condition) { fwrite(STDERR, "FAIL: {$message}\n"); exit(1); } }
$key = base64_encode(random_bytes(32)); $crypto = new Crypto($key); $cipher = $crypto->encrypt('POP-password');
check($cipher !== 'POP-password', 'password must be encrypted'); check($crypto->decrypt($cipher) === 'POP-password', 'password round trip');
$raw = "From: Copier <device@example.invalid>\r\nTo: counter@example.invalid\r\nSubject: =?UTF-8?B?7Lm07Jq07YSwIOuztOqzoA==?=\r\nMessage-ID: <sample@example.invalid>\r\nContent-Type: multipart/mixed; boundary=demo\r\n\r\n--demo\r\nContent-Type: text/plain; charset=UTF-8\r\n\r\nTotal: 12,345\r\n--demo\r\nContent-Type: image/png; name=counter.png\r\nContent-Disposition: attachment; filename=counter.png\r\nContent-Transfer-Encoding: base64\r\n\r\naW1hZ2U=\r\n--demo--\r\n";
$mail = (new MimeParser())->parse($raw);
check($mail['subject'] === '카운터 보고', 'encoded subject parsing'); check(str_contains($mail['text_body'], '12,345'), 'text body parsing');
check(count($mail['attachments']) === 1 && $mail['attachments'][0]['content'] === 'image', 'attachment parsing');
echo "All tests passed\n";
