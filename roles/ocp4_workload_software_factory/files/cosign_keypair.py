#!/usr/bin/env python3
"""Generate a cosign-compatible signing key pair for Tekton Chains.

Writes JSON {"cosign.key": <PEM>, "cosign.pub": <PEM>} to stdout. The private key is an
ECDSA P-256 key, PKCS#8 encoded, encrypted the way cosign encrypts it (scrypt N=32768 r=8
p=1 -> nacl/secretbox), wrapped in an "ENCRYPTED SIGSTORE PRIVATE KEY" PEM block. The
password comes from COSIGN_PASSWORD and is never printed. Same bytes as
`cosign generate-key-pair`, produced here so the deployer needs no cosign image or Job.
"""
import base64
import hashlib
import json
import os
import sys
import textwrap

import nacl.secret
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

SCRYPT_N, SCRYPT_R, SCRYPT_P = 32768, 8, 1


def b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def pem(block_type: str, body: bytes) -> str:
    lines = textwrap.wrap(base64.b64encode(body).decode(), 64)
    return f"-----BEGIN {block_type}-----\n" + "\n".join(lines) + f"\n-----END {block_type}-----\n"


def main() -> int:
    password = os.environ.get("COSIGN_PASSWORD", "")
    if not password:
        print("COSIGN_PASSWORD is required", file=sys.stderr)
        return 2
    private_key = ec.generate_private_key(ec.SECP256R1())
    pkcs8 = private_key.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    salt, nonce = os.urandom(32), os.urandom(24)
    key = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
                         dklen=32, maxmem=128 * 1024 * 1024)
    ciphertext = nacl.secret.SecretBox(key).encrypt(pkcs8, nonce).ciphertext
    envelope = {
        "kdf": {"name": "scrypt", "params": {"N": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P}, "salt": b64(salt)},
        "cipher": {"name": "nacl/secretbox", "nonce": b64(nonce)},
        "ciphertext": b64(ciphertext),
    }
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    json.dump({
        "cosign.key": pem("ENCRYPTED SIGSTORE PRIVATE KEY", json.dumps(envelope, separators=(",", ":")).encode()),
        "cosign.pub": public_pem,
    }, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
