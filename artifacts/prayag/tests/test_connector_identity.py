import io
import json
import os
from unittest.mock import patch

import sheets


class _JsonResponse:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return io.BytesIO(json.dumps(self._payload).encode("utf-8"))

    def __exit__(self, *_args):
        return False


def test_deployment_identity_is_minted_for_connectors():
    env = {
        "REPLIT_DEPLOYMENT_ID": "deployment-test",
        "REPLIT_CONNECTORS_AUDIENCE": "connectors.replit.com",
        "REPL_IDENTITY": "repl-test",
    }
    response = _JsonResponse({"identityToken": "minted-test-token"})

    with patch.dict(os.environ, env, clear=True):
        with patch("urllib.request.urlopen", return_value=response) as urlopen:
            assert sheets._connector_xtokens() == ["depl minted-test-token"]

    request = urlopen.call_args.args[0]
    assert request.full_url == "http://127.0.0.1:1105/getIdentityToken"
    assert json.loads(request.data) == {
        "audience": "https://connectors.replit.com"
    }


def test_development_connector_uses_repl_identity():
    with patch.dict(os.environ, {"REPL_IDENTITY": "repl-test"}, clear=True):
        assert sheets._connector_xtokens() == ["repl repl-test"]


def test_connector_audience_preserves_full_url():
    with patch.dict(
        os.environ,
        {"REPLIT_CONNECTORS_AUDIENCE": "https://custom.example"},
        clear=True,
    ):
        assert sheets._connector_audience() == "https://custom.example"