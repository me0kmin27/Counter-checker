<?php
declare(strict_types=1);

final class Crypto
{
    private string $key;

    public function __construct(string $encodedKey)
    {
        $key = base64_decode($encodedKey, true);
        if ($key === false || strlen($key) !== 32) {
            throw new RuntimeException('APP_KEY는 32바이트 base64 값이어야 합니다.');
        }
        $this->key = $key;
    }

    public function encrypt(string $plain): string
    {
        $iv = random_bytes(12); $tag = '';
        $cipher = openssl_encrypt($plain, 'aes-256-gcm', $this->key, OPENSSL_RAW_DATA, $iv, $tag);
        if ($cipher === false) { throw new RuntimeException('POP 비밀번호 암호화에 실패했습니다.'); }
        return base64_encode($iv . $tag . $cipher);
    }

    public function decrypt(string $encoded): string
    {
        $value = base64_decode($encoded, true);
        if ($value === false || strlen($value) < 29) { throw new RuntimeException('암호화된 POP 비밀번호가 올바르지 않습니다.'); }
        $plain = openssl_decrypt(substr($value, 28), 'aes-256-gcm', $this->key, OPENSSL_RAW_DATA, substr($value, 0, 12), substr($value, 12, 16));
        if ($plain === false) { throw new RuntimeException('POP 비밀번호를 복호화할 수 없습니다.'); }
        return $plain;
    }
}
