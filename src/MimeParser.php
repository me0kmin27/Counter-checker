<?php
declare(strict_types=1);

final class MimeParser
{
    public function parse(string $raw): array
    {
        [$headerText, $body] = $this->split($raw);
        $headers = $this->headers($headerText);
        $result = ['message_id' => $this->header($headers, 'message-id'), 'sender' => $this->decode($this->header($headers, 'from')),
            'recipients' => $this->decode($this->header($headers, 'to')), 'subject' => $this->decode($this->header($headers, 'subject')),
            'sent_at' => $this->date($this->header($headers, 'date')), 'text_body' => '', 'html_body' => '', 'attachments' => []];
        $this->part($headers, $body, $result);
        return $result;
    }

    private function part(array $headers, string $body, array &$result): void
    {
        $type = $this->header($headers, 'content-type') ?: 'text/plain';
        if (str_starts_with(strtolower($type), 'multipart/') && preg_match('/boundary\s*=\s*(?:"([^"]+)"|([^;\s]+))/i', $type, $m)) {
            $boundary = preg_quote($m[1] ?: $m[2], '/');
            $chunks = preg_split('/(?:\r?\n)?--' . $boundary . '(?:--)?\r?\n/', $body) ?: [];
            foreach (array_slice($chunks, 1) as $chunk) { if (trim($chunk) === '' || str_starts_with(trim($chunk), '--')) continue; [$h, $b] = $this->split($chunk); $this->part($this->headers($h), $b, $result); }
            return;
        }
        $encoding = strtolower($this->header($headers, 'content-transfer-encoding'));
        $content = match ($encoding) { 'base64' => base64_decode(preg_replace('/\s+/', '', $body) ?? '', true) ?: '', 'quoted-printable' => quoted_printable_decode($body), default => $body };
        $disposition = $this->header($headers, 'content-disposition');
        $filename = $this->parameter($disposition, 'filename') ?: $this->parameter($type, 'name');
        $mime = strtolower(trim(explode(';', $type)[0]));
        if ($filename !== '' || str_starts_with(strtolower($disposition), 'attachment')) {
            $result['attachments'][] = ['filename' => $this->decode($filename) ?: 'attachment', 'mime_type' => $mime, 'content' => $content];
        } elseif ($mime === 'text/plain') { $result['text_body'] .= $this->toUtf8($content, $this->parameter($type, 'charset')) . "\n"; }
        elseif ($mime === 'text/html') { $result['html_body'] .= $this->toUtf8($content, $this->parameter($type, 'charset')) . "\n"; }
    }

    private function split(string $value): array { $parts = preg_split("/\r?\n\r?\n/", $value, 2); return [$parts[0] ?? '', $parts[1] ?? '']; }
    private function headers(string $text): array { $text = preg_replace("/\r?\n[\t ]+/", ' ', $text) ?? $text; $out = []; foreach (preg_split('/\r?\n/', $text) ?: [] as $line) { if (($p = strpos($line, ':')) !== false) $out[strtolower(trim(substr($line, 0, $p)))][] = trim(substr($line, $p + 1)); } return $out; }
    private function header(array $headers, string $name): string { return implode(', ', $headers[$name] ?? []); }
    private function parameter(string $header, string $name): string { return preg_match('/(?:^|;)\s*' . preg_quote($name, '/') . '\*?\s*=\s*(?:"([^"]*)"|([^;\s]*))/i', $header, $m) ? trim($m[1] ?: $m[2]) : ''; }
    private function decode(string $value): string { return function_exists('mb_decode_mimeheader') ? mb_decode_mimeheader($value) : $value; }
    private function toUtf8(string $value, string $charset): string { return $charset && strcasecmp($charset, 'utf-8') !== 0 ? mb_convert_encoding($value, 'UTF-8', $charset) : $value; }
    private function date(string $value): ?string { if (!$value || ($time = strtotime($value)) === false) return null; return gmdate('Y-m-d H:i:s', $time); }
}
