"""
Declare third-party gateway settings here.

Example:

```python
import logging
from logging import Logger, basicConfig, getLogger

import google.cloud.logging
import notion_client
import psycopg
from google.cloud import translate_v2 as translate
from google.cloud.logging_v2.handlers import (
    StructuredLogHandler,
    setup_logging,
)
from google.cloud.logging_v2.handlers._monitored_resources import detect_resource
from google.cloud.logging_v2.handlers.handlers import EXCLUDED_LOGGER_DEFAULTS
from marketstack import Marketstack
from psycopg import Connection
from psycopg.errors import ConnectionTimeout
from psycopg.rows import DictRow, dict_row
from PyNaver import Naver
from tenacity import retry, retry_if_exception_type, stop_after_delay, wait_fixed

from src.libs.exchange_rate import ExchangeRate
from src.libs.fhound_genai import FHoundGenAI
from src.libs.sftp import SFTPClient

from .settings import Settings


@retry(
    stop=stop_after_delay(300),
    wait=wait_fixed(5),
    retry=retry_if_exception_type(ConnectionTimeout),
)
def get_database_connection(
    uri: str | None = None,
) -> Connection[DictRow]:
    settings = Settings()
    uri = uri or settings.db_uri
    if not uri:
        raise ValueError("Database URI is not set (check environment variable or pass as argument).")
    return psycopg.connect(
        uri,
        row_factory=dict_row,
    )


def get_naver_client() -> "Naver":
    settings = Settings()
    client_id = settings.naver_client_id
    client_secret = settings.naver_client_secret
    if not all([client_id, client_secret]):
        raise ValueError("Naver API credentials are not set in the environment variables.")
    return Naver(
        client_id=client_id,
        client_secret=client_secret,
    )


def get_rakuten_sftp_client() -> SFTPClient:
    settings = Settings()
    host = settings.rakuten_sftp_host
    port = settings.rakuten_sftp_port
    user = settings.rakuten_sftp_user
    password = settings.rakuten_sftp_pass

    if not all([host, port, user, password]):
        raise ValueError("Rakuten SFTP credentials are not set in the environment variables.")
    return SFTPClient(
        host=host,
        port=port,
        username=user,
        password=password,
    )


def get_google_translate() -> translate.Client:
    return translate.Client()


def get_fhound_genai() -> FHoundGenAI:
    settings = Settings()
    url = settings.fhound_genai_url
    key = settings.fhound_genai_key
    if not all([url, key]):
        raise ValueError("FHound GenAI credentials are not set in the environment variables.")
    return FHoundGenAI(
        url=url,
        key=key,
    )


def get_notion() -> notion_client.Client:
    settings = Settings()
    token = settings.notion_token
    if not token:
        raise ValueError("Notion API token is not set in the environment variables.")
    return notion_client.Client(auth=token)


def get_fx():
    return ExchangeRate()


def get_marketstack() -> Marketstack:
    settings = Settings()
    api_key = settings.marketstack_api_key
    if not api_key:
        raise ValueError("Marketstack API key is not set in the environment variables.")
    return Marketstack(api_key=api_key)
```
"""
