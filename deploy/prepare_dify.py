"""Prepare the official Dify 1.8.0 Docker directory with local private keys."""
import re
import secrets
import tarfile
from pathlib import Path
import sys
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from dotenv import dotenv_values


def pair_celery_broker_password(env_path):
    """Keep the existing Redis secret and repair only the broker URL password."""
    config = dotenv_values(env_path, interpolate=False)
    parts = urlsplit(config["CELERY_BROKER_URL"])
    if parts.scheme not in {"redis", "rediss"} or not parts.hostname or not config.get("REDIS_PASSWORD"):
        raise ValueError("Expected the existing password-protected Redis broker URL")
    host = parts.netloc.rsplit("@", 1)[-1]
    auth = quote(unquote(parts.username or ""), safe="") + ":" + quote(config["REDIS_PASSWORD"], safe="")
    updated_url = urlunsplit(parts._replace(netloc=auth + "@" + host))
    source = env_path.read_text(encoding="utf-8")
    updated, count = re.subn(r"^CELERY_BROKER_URL=.*$", lambda _: "CELERY_BROKER_URL=" + updated_url,
                             source, flags=re.M)
    if count != 1:
        raise ValueError("Expected exactly one CELERY_BROKER_URL setting")
    if updated != source:
        env_path.write_text(updated, encoding="utf-8")
    return updated != source


if sys.argv[1:] == ["--self-check"]:
    from tempfile import TemporaryDirectory
    with TemporaryDirectory() as folder:
        sample = Path(folder) / ".env"
        sample.write_text('REDIS_PASSWORD="synthetic:@/% #?&9"\nCELERY_BROKER_URL=redis://:old@redis:6379/1\nUNCHANGED=yes\n', encoding="utf-8")
        before = dotenv_values(sample, interpolate=False)
        assert pair_celery_broker_password(sample)
        after = dotenv_values(sample, interpolate=False)
        assert unquote(urlsplit(after["CELERY_BROKER_URL"]).password) == before["REDIS_PASSWORD"]
        assert {key for key in before if before[key] != after[key]} == {"CELERY_BROKER_URL"}
        assert urlsplit(after["CELERY_BROKER_URL"]).path == "/1"
        assert not pair_celery_broker_password(sample)
    print("Broker password encoding, single-field repair and idempotence checks passed; project files unchanged.")
    sys.exit(0)

ROOT = Path(__file__).resolve().parents[1]
DOCKER = ROOT / "deploy" / "dify-1.8.0" / "docker"
env_path = DOCKER / ".env"
if not env_path.exists():
    sandbox_key = secrets.token_hex(32)
    values = {key: secrets.token_hex(32) for key in (
        "SECRET_KEY", "DB_PASSWORD", "REDIS_PASSWORD", "PLUGIN_DAEMON_KEY",
        "PLUGIN_DIFY_INNER_API_KEY", "WEAVIATE_API_KEY",
    )}
    values.update({
        "WEAVIATE_AUTHENTICATION_APIKEY_ALLOWED_KEYS": values["WEAVIATE_API_KEY"],
        "WEAVIATE_AUTHENTICATION_ANONYMOUS_ACCESS_ENABLED": "false",
        "CODE_EXECUTION_API_KEY": sandbox_key,
        "SANDBOX_API_KEY": sandbox_key,
        "CONSOLE_API_URL": "http://192.168.18.176",
        "CONSOLE_WEB_URL": "http://192.168.18.176",
        "SERVICE_API_URL": "http://192.168.18.176",
        "APP_API_URL": "http://192.168.18.176",
        "APP_WEB_URL": "http://192.168.18.176",
        "FILES_URL": "http://192.168.18.176",
        "LOG_TZ": "Asia/Shanghai",
        "PIP_MIRROR_URL": "https://pypi.tuna.tsinghua.edu.cn/simple",
    })
    source = (DOCKER / ".env.example").read_text(encoding="utf-8")
    for key, value in values.items():
        source, count = re.subn(rf"^{key}=.*$", f"{key}={value}", source, flags=re.M)
        if count != 1:
            raise ValueError(f"Expected one {key} setting; found {count}")
    env_path.write_text(source, encoding="utf-8")

pair_celery_broker_password(env_path)

archive = ROOT / "_work" / ".env.dify-deploy.tar.gz"
with tarfile.open(archive, "w:gz") as tar:
    tar.add(DOCKER, arcname="docker")
print("Prepared Dify Docker archive; private settings were not printed.")
