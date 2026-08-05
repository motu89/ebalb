import secrets
import string
import time


CREDENTIAL_LENGTH = 16
MAX_LOGIN_ATTEMPTS = 10
LOGIN_LOCKOUT_SECONDS = 5 * 60
_CREDENTIAL_ALPHABET = string.ascii_letters + string.digits


def generate_random_credential(length=CREDENTIAL_LENGTH):
    return "".join(secrets.choice(_CREDENTIAL_ALPHABET) for _ in range(length))


class LoginRateLimiter:
    def __init__(self):
        self._attempts = {}

    def get_wait_seconds(self, scope, client_key):
        key = self._key(scope, client_key)
        record = self._attempts.get(key)
        if not record:
            return 0

        locked_until = record.get("locked_until", 0)
        now = time.time()
        if locked_until > now:
            return int(locked_until - now) + 1

        if locked_until:
            self._attempts.pop(key, None)
        return 0

    def record_failure(self, scope, client_key):
        key = self._key(scope, client_key)
        wait_seconds = self.get_wait_seconds(scope, client_key)
        if wait_seconds:
            return wait_seconds

        record = self._attempts.setdefault(key, {"count": 0, "locked_until": 0})
        record["count"] += 1
        if record["count"] >= MAX_LOGIN_ATTEMPTS:
            record["locked_until"] = time.time() + LOGIN_LOCKOUT_SECONDS
            return LOGIN_LOCKOUT_SECONDS
        return 0

    def clear(self, scope, client_key):
        self._attempts.pop(self._key(scope, client_key), None)

    @staticmethod
    def _key(scope, client_key):
        return f"{scope}:{client_key or 'unknown'}"


login_rate_limiter = LoginRateLimiter()


def format_wait_time(seconds):
    minutes, remaining_seconds = divmod(max(0, int(seconds)), 60)
    return f"{minutes}:{remaining_seconds:02d}"
