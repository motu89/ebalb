import secrets
import string


CREDENTIAL_LENGTH = 16
_CREDENTIAL_ALPHABET = string.ascii_letters + string.digits


def generate_random_credential(length=CREDENTIAL_LENGTH):
    return "".join(secrets.choice(_CREDENTIAL_ALPHABET) for _ in range(length))
