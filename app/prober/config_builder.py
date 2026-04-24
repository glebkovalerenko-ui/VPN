"""Build sing-box runtime configs from stored proxy candidates."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from app.parser.utils import decode_base64_flexible, parse_port

from .errors import ControlledProbeError, ProbeErrorCode
from .selectors import ProbeCandidate

_SUPPORTED_PROTOCOLS: set[str] = {"vless", "vmess", "trojan", "ss"}


def build_outbound_config(candidate: ProbeCandidate) -> dict[str, Any]:
    """Build sing-box outbound object for supported protocols."""
    split = urlsplit(candidate.raw_config)
    scheme = split.scheme.lower() if split.scheme else candidate.protocol.lower()

    if scheme not in _SUPPORTED_PROTOCOLS:
        raise ControlledProbeError(
            code=ProbeErrorCode.UNSUPPORTED_PROTOCOL,
            text=f"Protocol '{scheme}' is not supported by sing-box Stage 5 backend",
        )

    if scheme == "vless":
        return _build_vless_outbound(split)
    if scheme == "vmess":
        return _build_vmess_outbound(candidate.raw_config, split)
    if scheme == "trojan":
        return _build_trojan_outbound(split)
    return _build_shadowsocks_outbound(candidate.raw_config, split)


def build_probe_config(
    *,
    outbound: dict[str, Any],
    listen_host: str,
    listen_port: int,
) -> dict[str, Any]:
    """Build full sing-box config for one probe attempt."""
    return {
        "log": {"disabled": True},
        "inbounds": [
            {
                "type": "mixed",
                "tag": "probe-in",
                "listen": listen_host,
                "listen_port": listen_port,
            }
        ],
        "outbounds": [
            outbound,
            {"type": "direct", "tag": "direct"},
            {"type": "block", "tag": "block"},
        ],
        "route": {
            "final": "proxy",
        },
    }


def _build_vless_outbound(split: Any) -> dict[str, Any]:
    server = split.hostname
    server_port = _safe_split_port(split)
    uuid = unquote(split.username) if split.username else None
    if not server or not server_port or not uuid:
        raise ControlledProbeError(
            code=ProbeErrorCode.PROBE_FAILED,
            text="Invalid vless URI: missing server/port/uuid",
        )

    params = _flatten_query(split.query)
    outbound: dict[str, Any] = {
        "type": "vless",
        "tag": "proxy",
        "server": server,
        "server_port": server_port,
        "uuid": uuid,
        "network": "tcp",
    }

    flow = _pick_param(params, "flow")
    if flow:
        outbound["flow"] = flow

    packet_encoding = _pick_param(params, "packetencoding", "packet_encoding")
    if packet_encoding:
        outbound["packet_encoding"] = packet_encoding

    tls = _build_tls_settings(params, default_enabled=False)
    if tls:
        outbound["tls"] = tls

    transport = _build_transport_settings(
        transport_token=_pick_param(params, "type", "transport"),
        params=params,
    )
    if transport:
        outbound["transport"] = transport

    return outbound


def _build_trojan_outbound(split: Any) -> dict[str, Any]:
    server = split.hostname
    server_port = _safe_split_port(split)
    password = unquote(split.username) if split.username else None
    if not server or not server_port or not password:
        raise ControlledProbeError(
            code=ProbeErrorCode.PROBE_FAILED,
            text="Invalid trojan URI: missing server/port/password",
        )

    params = _flatten_query(split.query)
    outbound: dict[str, Any] = {
        "type": "trojan",
        "tag": "proxy",
        "server": server,
        "server_port": server_port,
        "password": password,
        "network": "tcp",
    }

    tls = _build_tls_settings(params, default_enabled=True)
    if tls:
        outbound["tls"] = tls

    transport = _build_transport_settings(
        transport_token=_pick_param(params, "type", "transport"),
        params=params,
    )
    if transport:
        outbound["transport"] = transport

    return outbound


def _build_vmess_outbound(raw_config: str, split: Any) -> dict[str, Any]:
    payload = _decode_vmess_payload(raw_config)
    if payload is not None:
        return _build_vmess_from_payload(payload)
    return _build_vmess_from_url(split)


def _build_vmess_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    server = _pick_payload(payload, "add", "server", "host")
    server_port = parse_port(_pick_payload(payload, "port"))
    uuid = _pick_payload(payload, "id", "uuid")
    if not server or not server_port or not uuid:
        raise ControlledProbeError(
            code=ProbeErrorCode.PROBE_FAILED,
            text="Invalid vmess payload: missing server/port/uuid",
        )

    params = _flatten_payload(payload)
    outbound: dict[str, Any] = {
        "type": "vmess",
        "tag": "proxy",
        "server": server,
        "server_port": server_port,
        "uuid": uuid,
        "network": "tcp",
    }

    security = _pick_param(params, "scy", "security")
    if security:
        outbound["security"] = security

    alter_id = _parse_int(_pick_param(params, "aid", "alterid", "alter_id"))
    if alter_id is not None:
        outbound["alter_id"] = alter_id

    tls_hint = _pick_param(params, "tls")
    tls = _build_tls_settings(
        params,
        default_enabled=_is_tls_hint_enabled(tls_hint),
        explicit_security=tls_hint,
    )
    if tls:
        outbound["tls"] = tls

    transport = _build_transport_settings(
        transport_token=_pick_param(params, "net"),
        params=params,
    )
    if transport:
        outbound["transport"] = transport

    packet_encoding = _pick_param(params, "packetencoding", "packet_encoding")
    if packet_encoding:
        outbound["packet_encoding"] = packet_encoding

    return outbound


def _build_vmess_from_url(split: Any) -> dict[str, Any]:
    server = split.hostname
    server_port = _safe_split_port(split)
    uuid = unquote(split.username) if split.username else None
    if not server or not server_port or not uuid:
        raise ControlledProbeError(
            code=ProbeErrorCode.PROBE_FAILED,
            text="Invalid vmess URI: missing server/port/uuid",
        )

    params = _flatten_query(split.query)
    outbound: dict[str, Any] = {
        "type": "vmess",
        "tag": "proxy",
        "server": server,
        "server_port": server_port,
        "uuid": uuid,
        "network": "tcp",
    }

    security = _pick_param(params, "security")
    if security:
        outbound["security"] = security

    alter_id = _parse_int(_pick_param(params, "aid", "alterid", "alter_id"))
    if alter_id is not None:
        outbound["alter_id"] = alter_id

    tls_hint = _pick_param(params, "tls")
    tls = _build_tls_settings(
        params,
        default_enabled=_is_tls_hint_enabled(tls_hint),
        explicit_security=tls_hint,
    )
    if tls:
        outbound["tls"] = tls

    transport = _build_transport_settings(
        transport_token=_pick_param(params, "type", "net", "transport"),
        params=params,
    )
    if transport:
        outbound["transport"] = transport

    return outbound


def _build_shadowsocks_outbound(raw_config: str, split: Any) -> dict[str, Any]:
    method, password, server, server_port = _parse_shadowsocks_parts(raw_config, split)
    outbound: dict[str, Any] = {
        "type": "shadowsocks",
        "tag": "proxy",
        "server": server,
        "server_port": server_port,
        "method": method,
        "password": password,
        "network": "tcp",
    }

    params = _flatten_query(split.query)
    plugin = _pick_param(params, "plugin")
    if plugin:
        plugin_name, sep, plugin_opts = plugin.partition(";")
        plugin_name = plugin_name.strip()
        if plugin_name:
            outbound["plugin"] = plugin_name
            if sep and plugin_opts.strip():
                outbound["plugin_opts"] = plugin_opts.strip()

    return outbound


def _parse_shadowsocks_parts(raw_config: str, split: Any) -> tuple[str, str, str, int]:
    server = split.hostname
    server_port = _safe_split_port(split)
    method: str | None = None
    password: str | None = None

    if split.username is not None:
        method = unquote(split.username)
        password = unquote(split.password or "")
        if not password:
            decoded_auth = decode_base64_flexible(method)
            if decoded_auth and ":" in decoded_auth:
                method, password = decoded_auth.split(":", 1)

    if (not method or not password) and "@" in split.netloc:
        userinfo = split.netloc.rsplit("@", 1)[0].lstrip("/")
        decoded_auth = decode_base64_flexible(unquote(userinfo))
        if decoded_auth and ":" in decoded_auth:
            method, password = decoded_auth.split(":", 1)

    if not method or not password or not server or not server_port:
        payload_token = _extract_ss_payload_token(raw_config)
        decoded_payload = decode_base64_flexible(payload_token)
        if decoded_payload:
            if "@" in decoded_payload:
                creds, endpoint = decoded_payload.rsplit("@", 1)
                if ":" in creds:
                    method, password = creds.split(":", 1)
                parsed_host, parsed_port = _split_host_port(endpoint)
                server = server or parsed_host
                server_port = server_port or parse_port(parsed_port)
            elif ":" in decoded_payload and server and server_port:
                method, password = decoded_payload.split(":", 1)

    if not method or password is None or not server or not server_port:
        raise ControlledProbeError(
            code=ProbeErrorCode.PROBE_FAILED,
            text="Invalid ss URI: missing method/password/server/port",
        )

    return method.strip(), password, server, server_port


def _decode_vmess_payload(raw_config: str) -> dict[str, Any] | None:
    token = raw_config.partition("://")[2]
    if not token:
        return None

    token = token.split("#", 1)[0].split("?", 1)[0].strip().lstrip("/")
    if not token:
        return None

    decoded = decode_base64_flexible(token)
    if not decoded:
        return None

    try:
        payload = json.loads(decoded)
    except json.JSONDecodeError:
        return None

    if not isinstance(payload, dict):
        return None
    return payload


def _build_tls_settings(
    params: dict[str, str],
    *,
    default_enabled: bool,
    explicit_security: str | None = None,
) -> dict[str, Any] | None:
    security = (explicit_security or _pick_param(params, "security") or "").strip().lower()
    reality_public_key = _pick_param(params, "pbk", "publickey", "public_key")

    enabled = default_enabled
    if security in {"tls", "xtls", "reality"}:
        enabled = True
    if reality_public_key:
        enabled = True
    if security in {"none", "off", "false", "0"}:
        enabled = False

    if not enabled:
        return None

    tls: dict[str, Any] = {"enabled": True}

    server_name = _pick_param(params, "sni", "servername", "server_name", "peer")
    if server_name:
        tls["server_name"] = server_name

    insecure = _parse_bool(_pick_param(params, "allowinsecure", "insecure", "skip-cert-verify"))
    if insecure is not None:
        tls["insecure"] = insecure

    alpn = _parse_csv(_pick_param(params, "alpn"))
    if alpn:
        tls["alpn"] = alpn

    fingerprint = _pick_param(params, "fp", "fingerprint", "client-fingerprint")
    if fingerprint:
        tls["utls"] = {"enabled": True, "fingerprint": fingerprint}

    if security == "reality" or reality_public_key:
        if not reality_public_key:
            raise ControlledProbeError(
                code=ProbeErrorCode.PROBE_FAILED,
                text="REALITY TLS requires public key (pbk)",
            )

        reality: dict[str, Any] = {
            "enabled": True,
            "public_key": reality_public_key,
        }
        short_id = _pick_param(params, "sid", "shortid", "short_id")
        if short_id:
            reality["short_id"] = short_id
        tls["reality"] = reality

    return tls


def _build_transport_settings(transport_token: str | None, params: dict[str, str]) -> dict[str, Any] | None:
    transport_type = _normalize_transport_type(transport_token)
    if transport_type is None:
        return None

    if transport_type == "ws":
        transport: dict[str, Any] = {"type": "ws"}
        path = _normalize_path(_pick_param(params, "path"))
        if path:
            transport["path"] = path
        host = _pick_param(params, "host")
        if host:
            transport["headers"] = {"Host": host}
        return transport

    if transport_type == "grpc":
        transport = {"type": "grpc"}
        service_name = _pick_param(params, "servicename", "service_name")
        if service_name:
            transport["service_name"] = service_name
        return transport

    if transport_type == "http":
        transport = {"type": "http"}
        path = _normalize_path(_pick_param(params, "path"))
        if path:
            transport["path"] = path
        host = _pick_param(params, "host")
        if host:
            host_values = [part.strip() for part in host.split(",") if part.strip()]
            if host_values:
                transport["host"] = host_values
        return transport

    if transport_type == "httpupgrade":
        transport = {"type": "httpupgrade"}
        path = _normalize_path(_pick_param(params, "path"))
        if path:
            transport["path"] = path
        host = _pick_param(params, "host")
        if host:
            transport["host"] = host
        return transport

    if transport_type == "quic":
        return {"type": "quic"}

    raise ControlledProbeError(
        code=ProbeErrorCode.PROBE_FAILED,
        text=f"Unsupported transport type '{transport_token}'",
    )


def _flatten_query(query: str) -> dict[str, str]:
    values = parse_qs(query, keep_blank_values=False)
    flattened: dict[str, str] = {}
    for key, raw_values in values.items():
        if not raw_values:
            continue
        value = raw_values[0].strip()
        if value:
            flattened[key.lower()] = unquote(value)
    return flattened


def _flatten_payload(payload: dict[str, Any]) -> dict[str, str]:
    flattened: dict[str, str] = {}
    for key, value in payload.items():
        text = _as_text(value)
        if not text:
            continue
        flattened[key.lower()] = text
    return flattened


def _pick_param(params: dict[str, str], *keys: str) -> str | None:
    for key in keys:
        value = params.get(key.lower())
        if value:
            return value
    return None


def _pick_payload(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        if key not in payload:
            continue
        text = _as_text(payload.get(key))
        if text:
            return text
    return None


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, (int, float)):
        return str(value)
    return None


def _safe_split_port(split: Any) -> int | None:
    try:
        return split.port
    except ValueError:
        return None


def _parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _parse_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    return None


def _parse_csv(value: str | None) -> list[str] | None:
    if not value:
        return None
    items = [part.strip() for part in value.split(",") if part.strip()]
    return items or None


def _normalize_transport_type(value: str | None) -> str | None:
    if not value:
        return None

    lowered = value.strip().lower()
    alias_map = {
        "tcp": None,
        "none": None,
        "ws": "ws",
        "websocket": "ws",
        "grpc": "grpc",
        "gun": "grpc",
        "http": "http",
        "h2": "http",
        "http2": "http",
        "httpupgrade": "httpupgrade",
        "http-upgrade": "httpupgrade",
        "upgrade": "httpupgrade",
        "quic": "quic",
    }

    if lowered not in alias_map:
        return lowered
    return alias_map[lowered]


def _normalize_path(value: str | None) -> str | None:
    if not value:
        return None
    path = value.strip()
    if not path:
        return None
    if not path.startswith("/"):
        path = f"/{path}"
    return path


def _extract_ss_payload_token(raw_config: str) -> str:
    token = raw_config.partition("://")[2]
    token = token.split("#", 1)[0].split("?", 1)[0].strip()
    return token.lstrip("/")


def _split_host_port(value: str) -> tuple[str | None, str | None]:
    endpoint = value.strip()
    if not endpoint:
        return None, None

    if endpoint.startswith("[") and "]" in endpoint:
        right = endpoint.find("]")
        host = endpoint[1:right]
        rest = endpoint[right + 1 :]
        if rest.startswith(":"):
            return host, rest[1:]
        return host, None

    if ":" not in endpoint:
        return endpoint, None

    host, port = endpoint.rsplit(":", 1)
    return host, port


def _is_tls_hint_enabled(value: str | None) -> bool:
    if not value:
        return False
    return value.strip().lower() in {"tls", "xtls", "reality", "1", "true"}
