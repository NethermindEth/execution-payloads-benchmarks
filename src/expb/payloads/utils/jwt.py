import json
import hmac
import time
import base64
import hashlib
import threading

from pathlib import Path


class JWTProvider:
    def __init__(
        self,
        jwt_secret_file: Path,
        expiration_threshold_seconds: int = 10,
    ) -> None:
        if not jwt_secret_file.is_file():
            raise FileNotFoundError(f"JWT secret file not found: {jwt_secret_file}")

        # Read and decode hex secret to bytes
        raw_jwt_secret = jwt_secret_file.read_text().strip()
        self.jwt_secret_bytes = bytes.fromhex(raw_jwt_secret)

        # Cache for JWT token
        self._jwt_cache: dict[str, int] = {"token": "", "exp": 0}
        # Semaphore for thread-safe cache access
        self._cache_lock = threading.Lock()
        # Token expiration threshold
        self._expiration_threshold_seconds = expiration_threshold_seconds

    def get_jwt(
        self,
        expiration_seconds: int = 120,
    ) -> str:
        """
        Get JWT token with caching mechanism.
        Returns cached token if still valid, otherwise generates new one.
        Thread-safe implementation using semaphore.
        """
        now = int(time.time())

        # Check cache with lock
        with self._cache_lock:
            # Return cached token if still valid (with 2-second buffer)
            if self._jwt_cache["token"] and now < (
                self._jwt_cache["exp"] - self._expiration_threshold_seconds
            ):
                return self._jwt_cache["token"]

        # Generate new token (outside lock to avoid blocking other threads)
        iat = now
        exp = iat + expiration_seconds  # seconds since iat for expiration

        # Create header
        header = {"typ": "JWT", "alg": "HS256"}
        header_b64 = self._base64url_encode(
            json.dumps(header, separators=(",", ":")).encode()
        )

        # Create payload
        payload = {"iat": iat, "exp": exp}
        payload_b64 = self._base64url_encode(
            json.dumps(payload, separators=(",", ":")).encode()
        )

        # Create signature
        message = f"{header_b64}.{payload_b64}"
        signature = hmac.new(
            self.jwt_secret_bytes, message.encode(), hashlib.sha256
        ).digest()
        signature_b64 = self._base64url_encode(signature)

        # Create final token
        token = f"{header_b64}.{payload_b64}.{signature_b64}"

        # Update cache with lock
        with self._cache_lock:
            # Double-check pattern: another thread might have updated the cache
            if not (
                self._jwt_cache["token"]
                and now < (self._jwt_cache["exp"] - self._expiration_threshold_seconds)
            ):
                self._jwt_cache = {"token": token, "exp": exp}

        return token

    @staticmethod
    def _base64url_encode(data: bytes) -> str:
        """
        Base64 URL encoding without padding.
        """
        return base64.urlsafe_b64encode(data).decode().rstrip("=")
