import sqlite3


integrity_errors: list[type[Exception]] = [sqlite3.IntegrityError]

try:
    from psycopg import IntegrityError as PostgresIntegrityError
except ImportError:
    pass
else:
    integrity_errors.append(PostgresIntegrityError)

INTEGRITY_ERRORS = tuple(integrity_errors)
