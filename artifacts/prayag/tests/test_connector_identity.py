import io
import json
import os
import subprocess
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
        with patch(
            "subprocess.run", side_effect=FileNotFoundError("replit")
        ):
            with patch("urllib.request.urlopen", return_value=response) as urlopen:
                assert sheets._connector_xtokens() == ["depl minted-test-token"]

    request = urlopen.call_args.args[0]
    assert request.full_url == "http://127.0.0.1:1105/getIdentityToken"
    assert json.loads(request.data) == {
        "audience": "https://connectors.replit.com"
    }


def test_deployment_identity_prefers_replit_cli():
    env = {
        "REPLIT_DEPLOYMENT_ID": "deployment-test",
        "REPLIT_CONNECTORS_AUDIENCE": "connectors.replit.com",
    }
    completed = subprocess.CompletedProcess(
        args=["replit"], returncode=0, stdout="cli-minted-token\n", stderr=""
    )

    with patch.dict(os.environ, env, clear=True):
        with patch("subprocess.run", return_value=completed) as run:
            with patch("urllib.request.urlopen") as urlopen:
                assert sheets._connector_xtokens() == ["depl cli-minted-token"]

    assert run.call_args.args[0] == [
        "replit",
        "identity",
        "create",
        "--audience",
        "https://connectors.replit.com",
    ]
    urlopen.assert_not_called()


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


def test_deployment_sheets_request_uses_connector_proxy():
    response = _JsonResponse({"values": [["ok"]]})
    env = {
        "REPLIT_DEPLOYMENT_ID": "deployment-test",
        "REPLIT_CONNECTORS_HOSTNAME": "connectors.example",
    }
    url = "https://sheets.googleapis.com/v4/spreadsheets/file/values/Tab%201"

    with patch.dict(os.environ, env, clear=True):
        with patch.object(
            sheets, "_mint_deployment_identity", return_value="minted-test-token"
        ):
            with patch("urllib.request.urlopen", return_value=response) as urlopen:
                assert sheets._api_get(url, sheets._PROXY_TOKEN) == {
                    "values": [["ok"]]
                }

    request = urlopen.call_args.args[0]
    headers = {key.lower(): value for key, value in request.header_items()}
    assert request.full_url == (
        "https://connectors.example/api/v2/proxy"
        "/v4/spreadsheets/file/values/Tab%201"
    )
    assert headers["connector-name"] == "google-sheet"
    assert headers["x-replit-token"] == "depl minted-test-token"
    assert "authorization" not in headers


def test_deployment_drive_request_uses_drive_connector():
    env = {
        "REPLIT_DEPLOYMENT_ID": "deployment-test",
        "REPLIT_CONNECTORS_HOSTNAME": "https://connectors.example",
    }
    url = "https://www.googleapis.com/drive/v3/files?q=example"

    with patch.dict(os.environ, env, clear=True):
        with patch.object(
            sheets, "_mint_deployment_identity", return_value="minted-test-token"
        ):
            request = sheets._google_api_request(url, sheets._PROXY_TOKEN)

    headers = {key.lower(): value for key, value in request.header_items()}
    assert request.full_url == (
        "https://connectors.example/api/v2/proxy/drive/v3/files?q=example"
    )
    assert headers["connector-name"] == "google-drive"