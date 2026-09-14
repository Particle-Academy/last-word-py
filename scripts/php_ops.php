<?php

declare(strict_types=1);

/**
 * Cross-runtime OPS parity helper: run a batch of `Agent::diff` / `reduce` /
 * `equivalent` / `opSchema` / `DocDiff::same` / `DocDiff::hunks` calls through
 * the PHP last-word and print every result as JSON, so the Python port of
 * `LastWord\Ops` can be compared call for call. Same autoloader and lookup order
 * as php_tobytes.php.
 *
 *   php php_ops.php <calls.json>
 *
 * The input is a JSON list of calls:
 *
 *   {"fn": "diff", "a": {...}, "b": {...}}      -> {"ops": [...], "reduced": reduce(a, ops)}
 *   {"fn": "reduce", "doc": {...}, "ops": ...}  -> {"doc": {...}}
 *   {"fn": "equivalent", "a": {...}, "b": {...}} -> {"equivalent": bool}
 *   {"fn": "same", "a": ..., "b": ..., "wrap": [na, nb]}
 *                                               -> {"same": bool}, each side first
 *                                                  wrapped in na / nb lists, so a
 *                                                  depth past json_decode's 512
 *                                                  never has to cross the pipe
 *   {"fn": "opSchema"}                          -> {"schema": {...}, "json": "<json_encode of it>"}
 *   {"fn": "hunks", "a": [...], "b": [...]}     -> {"hunks": [...]}
 *   {"fn": "version"}                           -> {"version": Agent::version()}
 *
 * One process for the whole batch, because each PHP start costs more than the
 * call. A call that throws reports `{"error": class, "message": ...}` instead of
 * stopping the batch.
 *
 * JSON_PRESERVE_ZERO_FRACTION keeps a PHP float a float (`1.0`, not `1`),
 * because the int/float distinction is part of what is being compared.
 */

// A deprecation printed to STDOUT would corrupt the JSON answer.
ini_set('display_errors', 'stderr');

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

if ($argc < 2) {
    fwrite(STDERR, "usage: php php_ops.php <calls.json>\n");
    exit(2);
}

// Fail loudly if the autoloader found nothing, or found a PHP last-word too old
// to diff. Without this every call answers "class not found", which reads like
// a parity failure rather than a missing or stale checkout.
if (! class_exists(\LastWord\Agent::class) || ! class_exists(\LastWord\Ops\DocDiff::class)) {
    fwrite(STDERR, "LastWord\\Agent or LastWord\\Ops\\DocDiff not found. Set LAST_WORD_PHP_SRC to the src/ directory of PHP last-word 0.6.3 or later.\n");
    exit(3);
}

$flags = JSON_PRESERVE_ZERO_FRACTION | JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR;
$calls = json_decode((string) file_get_contents($argv[1]), true, 512, JSON_THROW_ON_ERROR);

$wrap = static function (mixed $value, int $levels): mixed {
    for ($i = 0; $i < $levels; $i++) {
        $value = [$value];
    }

    return $value;
};

$results = [];

foreach ($calls as $call) {
    try {
        $results[] = match ($call['fn']) {
            'diff' => (static function () use ($call): array {
                $ops = \LastWord\Agent::diff($call['a'], $call['b']);

                return ['ops' => $ops, 'reduced' => \LastWord\Agent::reduce($call['a'], $ops)];
            })(),
            'reduce' => ['doc' => \LastWord\Agent::reduce($call['doc'], $call['ops'])],
            'equivalent' => ['equivalent' => \LastWord\Agent::equivalent($call['a'], $call['b'])],
            'same' => ['same' => \LastWord\Ops\DocDiff::same(
                $wrap($call['a'], $call['wrap'][0] ?? 0),
                $wrap($call['b'], $call['wrap'][1] ?? 0),
            )],
            'opSchema' => [
                'schema' => \LastWord\Agent::opSchema(),
                'json' => json_encode(\LastWord\Agent::opSchema(), JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR),
            ],
            'hunks' => ['hunks' => \LastWord\Ops\DocDiff::hunks($call['a'], $call['b'])],
            'version' => ['version' => \LastWord\Agent::version()],
        };
    } catch (\Throwable $e) {
        $results[] = ['error' => get_class($e), 'message' => $e->getMessage()];
    }
}

echo json_encode($results, $flags);
