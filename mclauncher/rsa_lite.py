# -*- coding: utf-8 -*-
"""零依赖 RSA：给离线皮肤的本地 Yggdrasil 服务签名材质用。

只面向「本机 127.0.0.1 纹理服务」这一个场景：客户端（authlib-injector
替换了 Mojang 公钥）用元数据里的公钥验证材质属性签名，签名算法与官方
一致（SHA1withRSA，PKCS#1 v1.5）。密钥生成一次后持久化复用。
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import secrets
from pathlib import Path

# PKCS#1 v1.5 DigestInfo 前缀（SHA-1）
_SHA1_PREFIX = bytes.fromhex("3021300906052b0e03021a05000414")
# rsaEncryption OID (1.2.840.113549.1.1.1) + NULL 参数
_RSA_OID_DER = bytes.fromhex("300d06092a864886f70d0101010500")

_SMALL_PRIMES = [2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47,
                 53, 59, 61, 67, 71, 73, 79, 83, 89, 97, 101, 103, 107]


def _is_probable_prime(n: int, rounds: int = 40) -> bool:
    if n < 2:
        return False
    for p in _SMALL_PRIMES:
        if n % p == 0:
            return n == p
    d = n - 1
    r = 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for _ in range(rounds):
        a = secrets.randbelow(n - 3) + 2
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _random_prime(bits: int, e: int) -> int:
    while True:
        cand = secrets.randbits(bits) | (1 << (bits - 1)) | (1 << (bits - 2)) | 1
        if math.gcd(cand - 1, e) != 1:
            continue
        if _is_probable_prime(cand):
            return cand


def generate(bits: int = 2048) -> dict:
    """生成 RSA 私钥 {n, e, d}（int）。2048 位纯 Python 约一两秒，仅首次。"""
    e = 65537
    while True:
        p = _random_prime(bits // 2, e)
        q = _random_prime(bits // 2, e)
        if p == q:
            continue
        n = p * q
        if n.bit_length() != bits:
            continue
        lam = (p - 1) * (q - 1) // math.gcd(p - 1, q - 1)
        d = pow(e, -1, lam)
        return {"n": n, "e": e, "d": d}


def _key_bytes(key: dict) -> int:
    return (int(key["n"]).bit_length() + 7) // 8


def _emsa_pkcs1_v15_sha1(data: bytes, k: int) -> bytes:
    t = _SHA1_PREFIX + hashlib.sha1(data).digest()
    if k < len(t) + 11:
        raise ValueError("RSA 密钥太短")
    return b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t


def sign_sha1(data: bytes, key: dict) -> bytes:
    """SHA1withRSA（PKCS#1 v1.5）签名，与 Yggdrasil 材质属性签名一致。"""
    k = _key_bytes(key)
    em = int.from_bytes(_emsa_pkcs1_v15_sha1(data, k), "big")
    return pow(em, int(key["d"]), int(key["n"])).to_bytes(k, "big")


def verify_sha1(data: bytes, signature: bytes, key: dict) -> bool:
    k = _key_bytes(key)
    if len(signature) != k:
        return False
    em = pow(int.from_bytes(signature, "big"), int(key["e"]), int(key["n"]))
    return em.to_bytes(k, "big") == _emsa_pkcs1_v15_sha1(data, k)


# ---------------------------------------------------------------- DER / PEM

def _der_len(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    body = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(body)]) + body


def _der_int(value: int) -> bytes:
    body = value.to_bytes((value.bit_length() + 7) // 8 or 1, "big")
    if body[0] & 0x80:
        body = b"\x00" + body
    return b"\x02" + _der_len(len(body)) + body


def _der_seq(*parts: bytes) -> bytes:
    body = b"".join(parts)
    return b"\x30" + _der_len(len(body)) + body


def public_der(key: dict) -> bytes:
    """SubjectPublicKeyInfo DER（X.509 里的公钥格式）。"""
    rsa_pub = _der_seq(_der_int(int(key["n"])), _der_int(int(key["e"])))
    bitstring = b"\x03" + _der_len(len(rsa_pub) + 1) + b"\x00" + rsa_pub
    return _der_seq(_RSA_OID_DER, bitstring)


def public_pem(key: dict) -> str:
    b64 = base64.b64encode(public_der(key)).decode("ascii")
    lines = [b64[i:i + 64] for i in range(0, len(b64), 64)]
    return "-----BEGIN PUBLIC KEY-----\n" + "\n".join(lines) + "\n-----END PUBLIC KEY-----\n"


# ---------------------------------------------------------------- 持久化

def load_or_create(path) -> dict:
    """从 JSON 加载私钥，缺失或损坏则新生成并写回（权限尽量收紧）。"""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        key = {"n": int(data["n"], 16), "e": int(data["e"], 16), "d": int(data["d"], 16)}
        probe = b"pymcl-key-check"
        if verify_sha1(probe, sign_sha1(probe, key), key):
            return key
    except (OSError, ValueError, KeyError, TypeError):
        pass
    key = generate()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "n": format(key["n"], "x"),
        "e": format(key["e"], "x"),
        "d": format(key["d"], "x"),
    }), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key
