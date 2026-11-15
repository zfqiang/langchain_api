import logging

from fastapi import HTTPException, Response, status
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import Settings

logger = logging.getLogger(__name__)

_INCREMENT_AND_EXPIRE = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return {count, redis.call('TTL', KEYS[1])}
"""


async def enforce_rate_limit(
    redis: Redis,
    settings: Settings,
    identifier: str,
    response: Response,
) -> None:
    try:
        count, ttl = await redis.eval(
            _INCREMENT_AND_EXPIRE,
            1,
            f"rate-limit:{identifier}",
            settings.rate_limit_window_seconds,
        )
    except RedisError as exc:
        logger.exception("Redis rate limiter is unavailable")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Rate limiting service is unavailable",
        ) from exc

    remaining = max(0, settings.rate_limit_requests - int(count))
    response.headers["X-RateLimit-Limit"] = str(settings.rate_limit_requests)
    response.headers["X-RateLimit-Remaining"] = str(remaining)

    if int(count) > settings.rate_limit_requests:
        retry_after = max(1, int(ttl))
        response.headers["Retry-After"] = str(retry_after)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded",
            headers={
                "Retry-After": str(retry_after),
                "X-RateLimit-Limit": str(settings.rate_limit_requests),
                "X-RateLimit-Remaining": "0",
            },
        )
