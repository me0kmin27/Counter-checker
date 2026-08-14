<?php
declare(strict_types=1);

final class PopClient
{
    /** @var resource|null */ private $socket = null;

    public function connect(string $host, int $port, bool $tls): void
    {
        if (!filter_var($host, FILTER_VALIDATE_DOMAIN, FILTER_FLAG_HOSTNAME) && !filter_var($host, FILTER_VALIDATE_IP)) {
            throw new InvalidArgumentException('POP 서버 주소 형식이 올바르지 않습니다.');
        }
        $target = ($tls ? 'tls://' : 'tcp://') . $host . ':' . $port;
        $context = stream_context_create(['ssl' => ['verify_peer' => true, 'verify_peer_name' => true, 'SNI_enabled' => true]]);
        $this->socket = @stream_socket_client($target, $errorNumber, $errorMessage, 30, STREAM_CLIENT_CONNECT, $context);
        if (!$this->socket) { throw new RuntimeException("POP 연결 실패 ({$errorNumber}): {$errorMessage}"); }
        stream_set_timeout($this->socket, 30);
        $this->expectOk($this->readLine());
    }

    public function login(string $username, string $password): void
    {
        $this->command('USER ' . $this->singleLine($username));
        $this->command('PASS ' . $this->singleLine($password));
    }

    public function count(): int
    {
        $response = $this->command('STAT');
        if (!preg_match('/^\+OK\s+(\d+)/', $response, $matches)) { throw new RuntimeException('POP STAT 응답 오류'); }
        return (int) $matches[1];
    }

    public function retrieve(int $number, int $maxBytes = 26214400): string
    {
        $this->write("RETR {$number}\r\n");
        $this->expectOk($this->readLine());
        $data = '';
        while (($line = $this->readLine()) !== ".\r\n") {
            if (str_starts_with($line, '..')) { $line = substr($line, 1); }
            $data .= $line;
            if (strlen($data) > $maxBytes) { throw new RuntimeException('메일 크기가 25MB 제한을 초과했습니다.'); }
        }
        return $data;
    }

    public function delete(int $number): void { $this->command("DELE {$number}"); }
    public function close(): void { if ($this->socket) { try { $this->command('QUIT'); } catch (Throwable) {} fclose($this->socket); $this->socket = null; } }
    public function __destruct() { $this->close(); }

    private function command(string $command): string { $this->write($command . "\r\n"); $response = $this->readLine(); $this->expectOk($response); return $response; }
    private function write(string $data): void { if (!$this->socket || fwrite($this->socket, $data) === false) { throw new RuntimeException('POP 서버 쓰기 실패'); } }
    private function readLine(): string { if (!$this->socket || ($line = fgets($this->socket, 8192)) === false) { throw new RuntimeException('POP 서버 응답 시간 초과'); } return $line; }
    private function expectOk(string $response): void { if (!str_starts_with($response, '+OK')) { throw new RuntimeException('POP 서버 오류: ' . trim(preg_replace('/[^\P{C}\r\n\t]/u', '', $response) ?? '')); } }
    private function singleLine(string $value): string { return str_replace(["\r", "\n"], '', $value); }
}
