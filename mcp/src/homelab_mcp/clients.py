"""Shared HTTP clients and auth helpers for all homelab services."""

from __future__ import annotations

import os
from dataclasses import dataclass

import httpx


@dataclass
class ServiceConfig:
    """Configuration for a single service, loaded from env vars."""
    url: str
    api_key: str = ""
    username: str = ""
    password: str = ""


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def get_sonarr_config() -> ServiceConfig:
    return ServiceConfig(url=env("SONARR_URL"), api_key=env("SONARR_API_KEY"))


def get_radarr_config() -> ServiceConfig:
    return ServiceConfig(url=env("RADARR_URL"), api_key=env("RADARR_API_KEY"))


def get_lidarr_config() -> ServiceConfig:
    return ServiceConfig(url=env("LIDARR_URL"), api_key=env("LIDARR_API_KEY"))


def get_readarr_config() -> ServiceConfig:
    return ServiceConfig(url=env("READARR_URL"), api_key=env("READARR_API_KEY"))


def get_mylar3_config() -> ServiceConfig:
    return ServiceConfig(url=env("MYLAR3_URL"), api_key=env("MYLAR3_API_KEY"))


def get_prowlarr_config() -> ServiceConfig:
    return ServiceConfig(url=env("PROWLARR_URL"), api_key=env("PROWLARR_API_KEY"))


def get_qbt_config() -> ServiceConfig:
    return ServiceConfig(
        url=env("QBT_URL"),
        username=env("QBT_USER"),
        password=env("QBT_PASS"),
    )


def get_plex_config() -> ServiceConfig:
    return ServiceConfig(url=env("PLEX_URL"), api_key=env("PLEX_TOKEN"))


def get_homebridge_config() -> ServiceConfig:
    return ServiceConfig(
        url=env("HOMEBRIDGE_URL"),
        username=env("HOMEBRIDGE_USER"),
        password=env("HOMEBRIDGE_PASS"),
    )


def get_scrypted_config() -> ServiceConfig:
    return ServiceConfig(url=env("SCRYPTED_URL", "http://scrypted:11080"))


def get_dirigera_config() -> ServiceConfig:
    """IKEA DIRIGERA hub. Pair once with `generate-token <hub-ip>` then put
    the token in DIRIGERA_TOKEN. Hub IP goes in DIRIGERA_IP."""
    return ServiceConfig(
        url=env("DIRIGERA_IP"),       # plain IP address (lib adds port + scheme)
        api_key=env("DIRIGERA_TOKEN"),
    )


def get_unifi_config() -> dict:
    """Ubiquiti UniFi controller (UDM/UDM-Pro/Cloud Key). Local-only login.
    Create a dedicated Limited Admin (no 2FA) in UDM web UI > Settings > Admins."""
    return {
        "host":     env("UNIFI_HOST"),                  # IP or hostname of the UDM
        "username": env("UNIFI_USER"),
        "password": env("UNIFI_PASS"),
        "port":     int(env("UNIFI_PORT", "443")),
        "site":     env("UNIFI_SITE", "default"),
    }



# --- Servarr (Sonarr/Radarr/Prowlarr) shared client ---

class ServarrClient:
    """HTTP client for Servarr-family APIs (Sonarr, Radarr, Prowlarr)."""

    def __init__(self, config: ServiceConfig, api_version: str = "v3"):
        self.base = config.url
        self.api_version = api_version
        self._client = httpx.Client(
            headers={"X-Api-Key": config.api_key, "Accept": "application/json"},
            timeout=15.0,
        )

    def get(self, endpoint: str, **params) -> dict | list:
        r = self._client.get(f"{self.base}/api/{self.api_version}{endpoint}", params=params)
        r.raise_for_status()
        return r.json()

    def post(self, endpoint: str, json: dict | None = None) -> dict:
        r = self._client.post(
            f"{self.base}/api/{self.api_version}{endpoint}", json=json or {}
        )
        r.raise_for_status()
        return r.json()


# --- qBittorrent client (cookie auth with re-login) ---

class QbtClient:
    """HTTP client for qBittorrent WebUI API v2."""

    def __init__(self, config: ServiceConfig):
        self.base = config.url
        self.username = config.username
        self.password = config.password
        self._client = httpx.Client(timeout=15.0)
        self._logged_in = False

    def _login(self):
        r = self._client.post(
            f"{self.base}/api/v2/auth/login",
            data={"username": self.username, "password": self.password},
            headers={"Referer": self.base},
        )
        r.raise_for_status()
        self._logged_in = True

    def get(self, endpoint: str, **params) -> dict | list:
        if not self._logged_in:
            self._login()
        r = self._client.get(f"{self.base}/api/v2{endpoint}", params=params)
        if r.status_code == 403:
            self._login()
            r = self._client.get(f"{self.base}/api/v2{endpoint}", params=params)
        r.raise_for_status()
        return r.json()

    def post(self, endpoint: str, data: dict | None = None) -> str:
        if not self._logged_in:
            self._login()
        r = self._client.post(f"{self.base}/api/v2{endpoint}", data=data or {})
        if r.status_code == 403:
            self._login()
            r = self._client.post(f"{self.base}/api/v2{endpoint}", data=data or {})
        r.raise_for_status()
        return r.text


# --- Mylar3 client (query-string apikey, not Servarr-shaped) ---

class Mylar3Client:
    """HTTP client for Mylar3 API. Uses ?apikey=X&cmd=Y query-string auth.
    Responses vary: most return {"success": bool, "data": ...} but a few
    (getUpcoming, getWanted, forceSearch) return bare lists, dicts, or 'OK'."""

    def __init__(self, config: ServiceConfig):
        self.base = config.url
        self.api_key = config.api_key
        self._client = httpx.Client(timeout=20.0)

    def call(self, cmd: str, **params) -> object:
        params["apikey"] = self.api_key
        params["cmd"] = cmd
        r = self._client.get(f"{self.base}/api", params=params)
        r.raise_for_status()
        ctype = r.headers.get("content-type", "")
        if "json" in ctype or r.text.startswith(("{", "[")):
            return r.json()
        return r.text.strip()


# --- Plex client (token-based) ---

class PlexClient:
    """HTTP client for Plex Media Server API."""

    def __init__(self, config: ServiceConfig):
        self.base = config.url
        self.token = config.api_key
        self._client = httpx.Client(
            headers={"Accept": "application/json"},
            timeout=15.0,
        )

    def get(self, endpoint: str, **params) -> dict:
        params["X-Plex-Token"] = self.token
        r = self._client.get(f"{self.base}{endpoint}", params=params)
        r.raise_for_status()
        return r.json()

    def put(self, endpoint: str, **params) -> None:
        params["X-Plex-Token"] = self.token
        r = self._client.put(f"{self.base}{endpoint}", params=params)
        r.raise_for_status()


# --- Homebridge client (JWT auth with re-login) ---

class HomebridgeClient:
    """HTTP client for Homebridge Config UI X API."""

    def __init__(self, config: ServiceConfig):
        self.base = config.url
        self.username = config.username
        self.password = config.password
        self._client = httpx.Client(timeout=15.0)
        self._token: str | None = None

    def _login(self):
        r = self._client.post(
            f"{self.base}/api/auth/login",
            json={"username": self.username, "password": self.password},
        )
        r.raise_for_status()
        self._token = r.json().get("access_token")

    def get(self, endpoint: str) -> dict | list:
        if not self._token:
            self._login()
        r = self._client.get(
            f"{self.base}/api{endpoint}",
            headers={"Authorization": f"Bearer {self._token}"},
        )
        if r.status_code == 401:
            self._login()
            r = self._client.get(
                f"{self.base}/api{endpoint}",
                headers={"Authorization": f"Bearer {self._token}"},
            )
        r.raise_for_status()
        return r.json()
