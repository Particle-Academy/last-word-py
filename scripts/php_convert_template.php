<?php

declare(strict_types=1);

/**
 * Cross-runtime parity helper: convert a template package with the PHP
 * last-word, so the Python converter's WHOLE output archive can be compared
 * byte for byte. Same autoloader and `LAST_WORD_PHP_SRC` rule as
 * php_tobytes.php.
 *
 *   php php_convert_template.php <input> <output> [document|template]
 *
 * Exit 4 with the reason on stderr when PHP refuses, so a refusal is compared
 * too rather than read as a crash.
 */

spl_autoload_register(function (string $class): void {
    $prefix = 'LastWord\\';
    if (strncmp($class, $prefix, strlen($prefix)) !== 0) {
        return;
    }
    $rel = substr($class, strlen($prefix));
    $root = getenv('LAST_WORD_PHP_SRC') ?: __DIR__.'/../../last-word/src';
    $file = rtrim($root, '/').'/'.str_replace('\\', '/', $rel).'.php';
    if (is_file($file)) {
        require $file;
    }
});

if ($argc < 3) {
    fwrite(STDERR, "usage: php php_convert_template.php <input> <output> [document|template]\n");
    exit(2);
}

if (! method_exists(\LastWord\Agent::class, 'convertTemplate')) {
    fwrite(STDERR, "LastWord\\Agent::convertTemplate not found. Set LAST_WORD_PHP_SRC to a PHP last-word src/ that has it.\n");
    exit(3);
}

try {
    $out = \LastWord\Agent::convertTemplate((string) file_get_contents($argv[1]), $argv[3] ?? null);
} catch (\LastWord\Exceptions\OfficeTemplateRefused|\LastWord\Exceptions\ArchiveRefused $error) {
    fwrite(STDERR, $error->reason);
    exit(4);
}
file_put_contents($argv[2], $out);
