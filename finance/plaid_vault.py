"""OS-protected bank authorizations; never fall back to plaintext storage."""
import base64
import ctypes
from ctypes import wintypes
import hashlib
import os
import sys

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAC_PREFIX = b'Finance-Keychain-v1\0'


def mac_key_id():
    from .db import ROOT
    return hashlib.sha256(str(ROOT.resolve()).encode('utf-8')).hexdigest().encode('ascii')


def mac_key(create=False, key_id=None):
    # Select the native backend explicitly: no configurable/plaintext fallback.
    from keyring.backends.macOS import Keyring
    from .db import ROOT, database_lock
    with database_lock(ROOT / 'data' / 'keychain-key'):
        backend = Keyring()
        # Namespace independent clones; embed the ID in ciphertext so moving a
        # folder still finds its original key. Never overwrite another clone's key.
        service = 'Personal Finance Tracker'
        account = 'token-v1-' + (key_id or mac_key_id()).decode('ascii')
        try:
            encoded = backend.get_password(service, account)
            if encoded is None and create:
                encoded = base64.b64encode(os.urandom(32)).decode('ascii')
                backend.set_password(service, account, encoded)
            if encoded is None:
                raise ValueError('Missing Keychain encryption key.')
            key = base64.b64decode(encoded, validate=True)
            if len(key) != 32:
                raise ValueError('Invalid Keychain encryption key.')
            return key
        except Exception:
            raise RuntimeError('Cannot unlock Finance tokens. Unlock your macOS login Keychain and allow access; otherwise import an encrypted migration from the original computer.') from None


def protect(data: bytes, encrypt: bool) -> bytes:
    if sys.platform == 'win32':
        if not encrypt and data.startswith(MAC_PREFIX):
            raise RuntimeError('These tokens belong to macOS. Use an encrypted migration from the original computer.')
        return windows_protect(data, encrypt)
    if sys.platform == 'darwin':
        if encrypt:
            nonce = os.urandom(12)
            key_id = mac_key_id()
            aad = MAC_PREFIX + key_id
            return aad + nonce + AESGCM(mac_key(create=True, key_id=key_id)).encrypt(nonce, data, aad)
        if not data.startswith(MAC_PREFIX):
            raise RuntimeError('These tokens belong to Windows. Use an encrypted migration from the original computer.')
        try:
            key_id = data[len(MAC_PREFIX):len(MAC_PREFIX) + 64]
            if len(key_id) != 64 or any(c not in b'0123456789abcdef' for c in key_id):
                raise ValueError('Invalid vault key identifier.')
            value = data[len(MAC_PREFIX) + 64:]
            return AESGCM(mac_key(key_id=key_id)).decrypt(value[:12], value[12:], MAC_PREFIX + key_id)
        except RuntimeError:
            raise
        except Exception:
            raise RuntimeError('Saved authorization could not be unlocked. Restore a migration from the original computer.') from None
    raise RuntimeError('Connected accounts require Windows DPAPI or macOS Keychain. Manual accounts remain available.')


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.c_void_p)]


def windows_protect(data: bytes, encrypt: bool) -> bytes:
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.addressof(buffer))
    result = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    operation = crypt.CryptProtectData if encrypt else crypt.CryptUnprotectData
    operation.argtypes = [
        ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob),
    ]
    operation.restype = wintypes.BOOL
    if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise ctypes.WinError(ctypes.get_last_error())
    free = ctypes.WinDLL("kernel32").LocalFree
    free.argtypes = [ctypes.c_void_p]
    free.restype = ctypes.c_void_p
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        free(result.data)

