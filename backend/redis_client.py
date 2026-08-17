"""
Redis distributed seat locking.

⭐ Ye Phase 4 ka core hai. Interview me sabse zyada isi par sawaal aayenge.

Kaam kya hai:
  User seat select kare -> wo seat 5 minute ke liye uske naam hold ho jaye,
  taki wo aaram se payment kar sake aur beech me koi aur na le jaye.

Redis kyu, Postgres kyu nahi:
  1. Speed  — in-memory hai, lock check ~0.1ms me ho jata hai.
              5000 log ek saath aayein to 4999 yahin ruk jaate hain,
              DB tak pahunchte hi nahi.
  2. TTL    — Redis khud key expire kar deta hai. "Cart chhod ke chala gaya
              user" ka cleanup job likhne ki zaroorat hi nahi.

Redis "asli" guarantee NAHI hai — wo Postgres ke constraints hi dete hain.
Redis ek fast filter hai jo load kam karta hai. Isliye dono chahiye.
"""

import redis

from config import settings

# decode_responses=True -> Redis bytes ki jagah str deta hai.
# Iske bina har jagah b"123".decode() likhna padta.
redis_client = redis.Redis.from_url(
    settings.REDIS_URL,
    decode_responses=True,
    socket_connect_timeout=3,
    socket_timeout=3,
)


def _lock_key(seat_id: int) -> str:
    """seat:42:lock — namespace rakhne se Redis me cheezein saaf rehti hain."""
    return f"seat:{seat_id}:lock"


# ---------------------------------------------------------------------------
# Lock chhodne ka Lua script
# ---------------------------------------------------------------------------
# Seedha DEL kyu nahi kar sakte:
#
#   1. User A ka lock hai, wo 5 min me expire ho gaya
#   2. User B ne turant lock le liya
#   3. User A ka "release" request ab aata hai aur DEL kar deta hai
#      -> B ka lock uda diya, jabki B ne kuch galat nahi kiya
#
# Isliye pehle check karo "lock mera hi hai?", tabhi delete karo.
# Python me do steps (GET phir DEL) likhte to unke beech me bhi wahi race
# reh jati. Lua script Redis ke andar ATOMIC chalti hai — beech me kuch
# nahi ghus sakta.
_RELEASE_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""

_release_lock = redis_client.register_script(_RELEASE_SCRIPT)


def acquire_seat_lock(seat_id: int, user_id: int, ttl: int | None = None) -> bool:
    """
    Seat pe lock lo.

    True  = lock mil gaya
    False = kisi aur ke paas hai

    Poora faisla ek hi command me hota hai:

        SET seat:42:lock 7 NX EX 300

    nx=True -> key pehle se hai to kuch mat karo, False lauta do.
    Ye Redis ke andar ATOMIC hai — "check karo phir set karo" do alag steps
    nahi hain. Isliye 5000 parallel requests me se theek EK ko True milta hai.
    """
    return bool(
        redis_client.set(
            _lock_key(seat_id),
            str(user_id),
            nx=True,
            ex=ttl or settings.SEAT_LOCK_TTL,
        )
    )


def release_seat_lock(seat_id: int, user_id: int) -> bool:
    """
    Apna lock chhodo. Doosre ka lock ho to kuch nahi hoga (Lua script check karta hai).

    True = hamara lock tha aur release ho gaya
    """
    return bool(_release_lock(keys=[_lock_key(seat_id)], args=[str(user_id)]))


def get_lock_owner(seat_id: int) -> int | None:
    """Lock kiske paas hai? None = kisi ke paas nahi."""
    owner = redis_client.get(_lock_key(seat_id))
    return int(owner) if owner else None


def get_lock_ttl(seat_id: int) -> int:
    """
    Lock kitne second aur chalega.

    Redis -2 deta hai (key hi nahi hai) ya -1 (key hai par TTL nahi).
    Dono case me 0 lauta rahe hain — caller ko sirf "kitna time bacha" chahiye.
    """
    ttl = redis_client.ttl(_lock_key(seat_id))
    return ttl if ttl > 0 else 0


def is_lock_owner(seat_id: int, user_id: int) -> bool:
    return get_lock_owner(seat_id) == user_id


def ping() -> bool:
    """Health check ke liye."""
    try:
        return redis_client.ping()
    except redis.RedisError:
        return False
