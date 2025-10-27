import requests

from expb.payloads.utils.jwt import JWTProvider


class RPCError(Exception):
    def __init__(
        self,
        error: str,
        status_code: int,
        response: requests.Response,
    ):
        super().__init__(error)
        self.error = error
        self.status_code = status_code
        self.response = response


def engine_request(
    engine_url: str,
    jwt_provider: JWTProvider,
    rpc_request,
    timeout: int = 3600,
    expiration_seconds: int = 120,
    retries=10,
):
    while retries > 0:
        jwt = jwt_provider.get_jwt(expiration_seconds=expiration_seconds)
        resp = requests.post(
            url=engine_url,
            headers={
                "Authorization": f"Bearer {jwt}",
                "Content-Type": "application/json",
            },
            json=rpc_request,
            timeout=timeout,
        )
        if not resp.ok:
            if resp.status_code == 401:
                expiration_seconds = min(expiration_seconds * 2, 3600)
                jwt_provider.invalidate_jwt()
                retries -= 1
                continue
            raise RPCError(
                error=resp.text,
                status_code=resp.status_code,
                response=resp,
            )

        body = resp.json()
        if "error" in body:
            raise RPCError(
                error=body["error"],
                status_code=resp.status_code,
                response=resp,
            )

        if "result" not in body:
            raise RPCError(
                error="No result in response",
                status_code=resp.status_code,
                response=resp,
            )

        return body["result"]

    raise RPCError(
        error="Authentication retries exhausted",
        status_code=401,
        response=None,
    )
