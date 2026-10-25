# MITOM Launcher 2026.10.25.9

Resumes an owned interrupted Panel installation after authenticated metadata is
checked. Recovery runs before downloading or expanding the full Panel again.
Only a valid pending receipt and its verified local transaction can trigger
rollback. Successful recovery preserves backups, journals and private settings;
changed user files, invalid ownership or damaged backups remain protected.

Empty parent directories left by a rolled-back first installation are removed
only when empty, nonrecursively and within the validated managed roots. This
allows fresh installation to continue without deleting an unverified journal.

Short HTTP responses now receive up to three fresh download attempts. A full
response with an incorrect SHA-256 remains a hard failure. Downloaded bytes are
flushed and rehashed from disk before promotion.

Retains automatic Launcher/app updates and waiting for Photoshop from .25.8.
Panel 2026.10.25.1 contains Companion 1.0.27. Chrome still requires reloading an
updated unpacked extension or loading the managed Companion folder initially.
