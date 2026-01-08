import time
import redis
from functools import wraps

def redis_retry(max_attempts=3, backoff_factor=0.2):
    """
    Retry logic specifically for the Client SDK.
    Retries faster than the server side to keep UI responsive.
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            attempt = 0
            while True:
                try:
                    return func(*args, **kwargs)
                except (redis.ConnectionError, redis.TimeoutError) as e:
                    attempt += 1
                    if attempt > max_attempts:
                        # print(f"[isoAutomate SDK Error] Failed {func.__name__}: {e}")
                        raise e
                    time.sleep(backoff_factor * (2 ** (attempt - 1)))
        return wrapper
    return decorator