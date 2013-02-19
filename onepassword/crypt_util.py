import base64
import functools
import hashlib
import hmac
import struct

import Crypto.Cipher.AES
import Crypto.Hash.HMAC
import Crypto.Hash.MD5
import Crypto.Hash.SHA256
import Crypto.Hash.SHA512
import Crypto.Protocol.KDF
import pbkdf2

from . import padding
from . import pbkdf1


# 8 bytes for "opdata1"
# 8 bytes for plaintext length
# 16 bytes for IV
# 16 bytes for mimum cryptext size
# 32 bytes for HMAC-SHA256
OPDATA1_MINIMUM_SIZE = 80


DEFAULT_PBKDF_ITERATIONS = 1000
MINIMUM_PBKDF_ITERATIONS = 1000

A_AES_SIZE = 128
C_AES_SIZE = 256
KEY_SIZE = {
    128: 16,
    192: 24,
    256: 32,
}

SALT_SIZE = 8
SALT_MARKER = 'Salted__'


class BadKeyError(Exception):
    pass


def a_decrypt_key(key_obj, password, aes_size=A_AES_SIZE):
    key_size = KEY_SIZE[aes_size]
    data = base64.b64decode(key_obj['data'])
    salt = '\x00'*SALT_SIZE
    if data[:len(SALT_MARKER)] == SALT_MARKER:
        salt = data[len(SALT_MARKER):len(SALT_MARKER) + SALT_SIZE]
        data = data[len(SALT_MARKER) + SALT_SIZE:]
    iterations = max(int(key_obj.get('iterations', DEFAULT_PBKDF_ITERATIONS)), MINIMUM_PBKDF_ITERATIONS)
    pb_gen = pbkdf2.PBKDF2(password, salt, iterations)
    key = pb_gen.read(key_size)
    iv = pb_gen.read(key_size)
    aes_er = Crypto.Cipher.AES.new(key, Crypto.Cipher.AES.MODE_CBC, iv)
    potential_key = padding.pkcs5_unpad(aes_er.decrypt(data))
    validation = base64.b64decode(key_obj['validation'])
    decrypted_validation = a_decrypt_item(validation, potential_key)
    if decrypted_validation != potential_key:
        raise BadKeyError("Validation did not match")
    return potential_key


def hexize(byte_string):
    res = []
    for c in byte_string:
        res.append('%02x' % ord(c))
    return ''.join(res).upper()

def unhexize(hex_string):
    res = []
    for i in range(len(hex_string)/2):
        res.append(int((hex_string[2*i] + hex_string[2*i+1]), 16))
    return ''.join(chr(i) for i in res)


def a_decrypt_item(data, key, aes_size=A_AES_SIZE):
    key_size = KEY_SIZE[aes_size]
    if data[:len(SALT_MARKER)] == SALT_MARKER:
        salt = data[len(SALT_MARKER):len(SALT_MARKER) + SALT_SIZE]
        data = data[len(SALT_MARKER) + SALT_SIZE:]
        pb_gen = pbkdf1.PBKDF1(key, salt)
        nkey = pb_gen.read(key_size)
        iv = pb_gen.read(key_size)
    else:
        nkey = Crypto.Hash.MD5.new(key).digest()
        iv = '\x00'*key_size
    aes_er = Crypto.Cipher.AES.new(nkey, Crypto.Cipher.AES.MODE_CBC, iv)
    return padding.pkcs5_unpad(aes_er.decrypt(data))


def opdata1_unpack(data):
    if data[:8] != "opdata01":
        data = base64.b64decode(data)
    if data[:8] != "opdata01":
        raise TypeError("expected opdata1 format message")
    plaintext_length = int(struct.unpack("<Q", data[8:16])[0])
    iv = data[16:32]
    cryptext = data[32:-32]
    expected_hmac = data[-32:]
    return plaintext_length, iv, cryptext, expected_hmac


def opdata1_decrypt_item(data, key, hmac_key, aes_size=C_AES_SIZE):
    key_size = KEY_SIZE[aes_size]
    assert len(key) == key_size
    assert len(data) >= OPDATA1_MINIMUM_SIZE
    plaintext_length, iv, cryptext, expected_hmac = opdata1_unpack(data)
    message_to_hmac = cryptext
    verifier = Crypto.Hash.HMAC.new(key=hmac_key, msg=message_to_hmac, digestmod=Crypto.Hash.SHA256)
    if verifier.digest() != expected_hmac:
        raise ValueError("HMAC did not match for opdata1 record")
    decryptor = Crypto.Cipher.AES.new(key, Crypto.Cipher.AES.MODE_CBC, iv)
    decrypted = decryptor.decrypt(cryptext)
    unpadded = padding.ab_unpad(decrypted, plaintext_length)
    return unpadded


def opdata1_derive_keys(password, salt, iterations=1000, aes_size=C_AES_SIZE):
    """Key derivation function for .cloudkeychain files"""
    key_size = KEY_SIZE[aes_size]
    prf = lambda p,s: Crypto.Hash.HMAC.new(p, s, digestmod=Crypto.Hash.SHA512).digest()
    keys = Crypto.Protocol.KDF.PBKDF2(password=password, salt=salt, dkLen=2*key_size, count=iterations, prf=prf)
    key1 = keys[:key_size]
    key2 = keys[key_size:]
    return key1, key2
