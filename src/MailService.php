<?php
declare(strict_types=1);

final class MailService
{
    public function __construct(private PDO $pdo, private array $config) {}

    public function fetch(array $account): int
    {
        $client = new PopClient(); $saved = 0;
        try {
            $client->connect($account['host'], (int) $account['port'], (bool) $account['use_tls']);
            $client->login($account['username'], (new Crypto($this->config['app_key']))->decrypt($account['password_cipher']));
            for ($i = 1, $count = $client->count(); $i <= $count; $i++) {
                $raw = $client->retrieve($i);
                if ($this->store((int) $account['id'], $raw)) $saved++;
                if ($account['delete_after_receive']) $client->delete($i);
            }
            $this->status((int) $account['id'], null); return $saved;
        } catch (Throwable $e) { $this->status((int) $account['id'], mb_substr($e->getMessage(), 0, 500)); throw $e; }
        finally { $client->close(); }
    }

    private function store(int $accountId, string $raw): bool
    {
        $sha = hash('sha256', $raw);
        $check = $this->pdo->prepare('SELECT id FROM email_messages WHERE account_id=? AND content_sha256=?'); $check->execute([$accountId, $sha]);
        if ($check->fetch()) return false;
        $mail = (new MimeParser())->parse($raw); $base = rtrim($this->config['mail_storage'], '/') . '/' . date('Y/m');
        if (!is_dir($base) && !mkdir($base, 0750, true) && !is_dir($base)) throw new RuntimeException('메일 저장 경로를 만들 수 없습니다.');
        $rawPath = $base . '/' . $sha . '.eml'; if (file_put_contents($rawPath, $raw, LOCK_EX) === false) throw new RuntimeException('메일 원문 저장 실패');
        $this->pdo->beginTransaction();
        try {
            $stmt = $this->pdo->prepare('INSERT INTO email_messages(account_id,message_id,content_sha256,sender,recipients,subject,sent_at,text_body,html_body,raw_path,attachment_count) VALUES(?,?,?,?,?,?,?,?,?,?,?)');
            $stmt->execute([$accountId, $mail['message_id'] ?: null, $sha, $mail['sender'], $mail['recipients'], $mail['subject'], $mail['sent_at'], $mail['text_body'], $mail['html_body'], $rawPath, count($mail['attachments'])]);
            $emailId = (int) $this->pdo->lastInsertId();
            foreach ($mail['attachments'] as $n => $item) {
                $safe = preg_replace('/[^\pL\pN._-]+/u', '_', basename($item['filename'])) ?: 'attachment';
                $path = $base . '/' . $sha . '-' . $n . '-' . $safe; file_put_contents($path, $item['content'], LOCK_EX);
                $insert = $this->pdo->prepare('INSERT INTO attachments(email_id,filename,mime_type,size_bytes,content_sha256,storage_path) VALUES(?,?,?,?,?,?)');
                $insert->execute([$emailId, $item['filename'], $item['mime_type'], strlen($item['content']), hash('sha256', $item['content']), $path]);
            }
            $this->pdo->commit(); return true;
        } catch (Throwable $e) { $this->pdo->rollBack(); @unlink($rawPath); throw $e; }
    }

    private function status(int $id, ?string $error): void { $stmt=$this->pdo->prepare('UPDATE pop_accounts SET last_checked_at=NOW(),last_error=? WHERE id=?'); $stmt->execute([$error,$id]); }
}
