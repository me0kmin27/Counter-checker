CREATE TABLE IF NOT EXISTS pop_accounts (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    host VARCHAR(255) NOT NULL,
    port SMALLINT UNSIGNED NOT NULL DEFAULT 995,
    username VARCHAR(255) NOT NULL,
    password_cipher TEXT NOT NULL,
    use_tls BOOLEAN NOT NULL DEFAULT TRUE,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    delete_after_receive BOOLEAN NOT NULL DEFAULT FALSE,
    last_checked_at DATETIME NULL,
    last_error VARCHAR(500) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS email_messages (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    account_id BIGINT UNSIGNED NOT NULL,
    message_id VARCHAR(998) NULL,
    content_sha256 CHAR(64) NOT NULL,
    sender VARCHAR(998) NOT NULL DEFAULT '',
    recipients TEXT NOT NULL,
    subject TEXT NOT NULL,
    sent_at DATETIME NULL,
    received_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    text_body MEDIUMTEXT NOT NULL,
    html_body MEDIUMTEXT NOT NULL,
    raw_path VARCHAR(500) NOT NULL,
    attachment_count INT UNSIGNED NOT NULL DEFAULT 0,
    status VARCHAR(30) NOT NULL DEFAULT 'received',
    CONSTRAINT fk_email_account FOREIGN KEY (account_id) REFERENCES pop_accounts(id),
    UNIQUE KEY uq_account_sha (account_id, content_sha256),
    INDEX ix_received_at (received_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS attachments (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    email_id BIGINT UNSIGNED NOT NULL,
    filename VARCHAR(500) NOT NULL,
    mime_type VARCHAR(255) NOT NULL,
    size_bytes BIGINT UNSIGNED NOT NULL,
    content_sha256 CHAR(64) NOT NULL,
    storage_path VARCHAR(500) NOT NULL,
    CONSTRAINT fk_attachment_email FOREIGN KEY (email_id) REFERENCES email_messages(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
