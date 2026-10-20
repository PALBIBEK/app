"""The vault: secrets the supervisor uses on a session's behalf, encrypted at rest.

Nothing in a sandbox, and nothing a model sees, ever holds a vault secret: the
supervisor reads one at the moment it makes the call that needs it (a model
call through the session's gateway key, an MCP call through the credential
proxy). Each secret is bound to its owner and name, so a ciphertext copied into
another row does not decrypt.
"""

from supervisor.vault.crypto import Keyring, VaultKeyError
from supervisor.vault.store import Owner, Secret, Vault

__all__ = ["Keyring", "Owner", "Secret", "Vault", "VaultKeyError"]
