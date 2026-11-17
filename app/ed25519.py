"""Ed25519 по RFC 8032 на чистом Python.

Нужен ровно для одного: проверять подпись манифеста релиза в exe. Ради этого тянуть cryptography или
libsodium не хочется — в сборке прибавилось бы несколько мегабайт DLL, а здесь двести строк и hashlib.
Подпись тоже здесь: tools/sign_release.py подписывает тем же кодом, что и проверяет, поэтому расхождения
между «чем подписали» и «чем проверили» быть не может.
"""
import hashlib

P = 2**255 - 19
L = 2**252 + 27742317777372353535851937790883648493
D = -121665 * pow(121666, P - 2, P) % P
SQRT_M1 = pow(2, (P - 1) // 4, P)


def _add(p, q):
    """Сложение в расширенных координатах (X, Y, Z, T) для кривой с a = -1; формулы полные."""
    a = (p[1] - p[0]) * (q[1] - q[0]) % P
    b = (p[1] + p[0]) * (q[1] + q[0]) % P
    c = p[3] * q[3] * 2 * D % P
    d = p[2] * q[2] * 2 % P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % P, g * h % P, f * g % P, e * h % P)


def _double(p):
    a = p[0] * p[0] % P
    b = p[1] * p[1] % P
    c = 2 * p[2] * p[2] % P
    h = a + b
    e = h - (p[0] + p[1]) ** 2
    g = a - b
    f = c + g
    return (e * f % P, g * h % P, f * g % P, e * h % P)


def _mul(p, n):
    q = (0, 1, 1, 0)
    while n:
        if n & 1:
            q = _add(q, p)
        p = _double(p)
        n >>= 1
    return q


def _recover_x(y, sign):
    if y >= P:
        return None
    x2 = (y * y - 1) * pow(D * y * y + 1, P - 2, P) % P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (P + 3) // 8, P)
    if (x * x - x2) % P:
        x = x * SQRT_M1 % P
    if (x * x - x2) % P:
        return None
    if (x & 1) != sign:
        x = P - x
    return x


def _decode(s: bytes):
    if len(s) != 32:
        return None
    y = int.from_bytes(s, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % P)


def _encode(p):
    zinv = pow(p[2], P - 2, P)
    x, y = p[0] * zinv % P, p[1] * zinv % P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


G = _decode(bytes.fromhex("58" + "66" * 31))


def _small_order(p) -> bool:
    """Точка малого порядка: 8P — нейтральный элемент. Такие ключи и R дают подписи, которые «сходятся»
    при любом сообщении (identity: public=01 00…, R=01 00…, S=0). Настоящий ключ всегда кратен G."""
    q = _mul(p, 8)
    return q[0] % P == 0 and (q[1] - q[2]) % P == 0


def verify(public: bytes, msg: bytes, sig: bytes) -> bool:
    """True, если sig — 64-байтная подпись msg ключом public (32 байта)."""
    if len(public) != 32 or len(sig) != 64:
        return False
    a, r = _decode(public), _decode(sig[:32])
    if a is None or r is None or _small_order(a) or _small_order(r):
        return False
    s = int.from_bytes(sig[32:], "little")
    if s >= L:  # S >= L даёт вторую подпись для того же сообщения, а на этом строят подделки
        return False
    h = int.from_bytes(hashlib.sha512(sig[:32] + public + msg).digest(), "little") % L
    lhs, rhs = _mul(G, s), _add(r, _mul(a, h))
    # Z после сложения не равен 1, поэтому сравниваем в проективных координатах
    return (lhs[0] * rhs[2] - rhs[0] * lhs[2]) % P == 0 and (lhs[1] * rhs[2] - rhs[1] * lhs[2]) % P == 0


def _expand(seed: bytes):
    if len(seed) != 32:
        raise ValueError("ключ Ed25519 — ровно 32 байта")
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a = (a & ((1 << 254) - 8)) | (1 << 254)
    return a, h[32:]


def public_key(seed: bytes) -> bytes:
    a, _ = _expand(seed)
    return _encode(_mul(G, a))


def sign(seed: bytes, msg: bytes) -> bytes:
    a, prefix = _expand(seed)
    pub = _encode(_mul(G, a))
    r = int.from_bytes(hashlib.sha512(prefix + msg).digest(), "little") % L
    big_r = _encode(_mul(G, r))
    k = int.from_bytes(hashlib.sha512(big_r + pub + msg).digest(), "little") % L
    return big_r + ((r + k * a) % L).to_bytes(32, "little")
