# RET-C2-566 - pins the STG_MOCK_MODE bridge added to src/api/server.py after
# deploy-stg (provisional) crashed at import time with MissingSecret for
# PRODUCT_KG_API_KEY (that CI job has no way to materialize a real secret -
# an established precedent for STG-only stub fallbacks).
#
# Both directions run in a subprocess: src/api/server.py binds its KB client
# at import time, so re-importing the module in-process would reuse the first
# binding and silently assert nothing on the second case.

import subprocess
import sys


def _run(env_extra: dict) -> subprocess.CompletedProcess:
    code = (
        "import os\n"
        "os.environ.setdefault('INVOKE_AUTH_TOKEN', 'x')\n"
        "import src.api.server as server\n"
        "print('KB_CLIENT_TYPE=' + type(server._kb_client).__name__)\n"
    )
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env_extra,
    )


def test_stg_mock_mode_binds_stub_kb_client_without_secret(monkeypatch_env=None):
    import os

    env = {**os.environ, "STG_MOCK_MODE": "true"}
    env.pop("PRODUCT_KG_API_KEY", None)
    env.pop("RET_C2_566_ALLOW_STUB_KB", None)
    result = _run(env)
    assert result.returncode == 0, result.stderr
    assert "KB_CLIENT_TYPE=_StubKGClient" in result.stdout


def test_no_mock_mode_still_fails_closed_without_secret():
    import os

    env = {**os.environ}
    env.pop("STG_MOCK_MODE", None)
    env.pop("PRODUCT_KG_API_KEY", None)
    env.pop("RET_C2_566_ALLOW_STUB_KB", None)
    result = _run(env)
    assert result.returncode != 0
    assert "KBConfigError" in result.stderr
    assert "PRODUCT_KG_API_KEY" in result.stderr
