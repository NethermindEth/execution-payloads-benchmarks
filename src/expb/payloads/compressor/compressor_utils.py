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
):
    jwt = jwt_provider.get_jwt()
    resp = requests.post(
        url=engine_url,
        headers={
            "Authorization": f"Bearer {jwt}",
            "Content-Type": "application/json",
        },
        json=rpc_request,
    )
    if not resp.ok:
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

    return body["result"]
