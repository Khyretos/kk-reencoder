"""Settings for kk-reencoder that can be changed on the web page.

They are stored in the SQLite state table as 'setting.<name>' and applied to the live cfg dict in place,
so the running service uses them at once and they survive a restart (load() at start-up).
"""

from typing import Any, Callable, Dict, Tuple

import rx_db

# name -> (type, range check)
FIELDS: Dict[str, Tuple[type, Callable[[Any], bool]]] = {
    "mode": (str, lambda x: x in ("report", "manual", "stage")),
    "parallel": (int, lambda x: 1 <= x <= 5),
    "rescan_s": (int, lambda x: 3600 <= x <= 604800),
    "check_every_episode": (bool, lambda x: True),
    "target_vmaf": (float, lambda x: 80.0 <= x <= 100.0),
    "max_ratio": (float, lambda x: 0.3 <= x <= 0.99),
}


def current(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """The value of every field as the service uses it now."""
    out = {name: cfg.get(name) for name in FIELDS}
    if out["check_every_episode"] is None:
        out["check_every_episode"] = True
    return out


def parse(name: str, value: Any) -> Any:
    """A JSON (or stored string) value as the field's type; ValueError when unknown, wrong or out of range."""
    if name not in FIELDS:
        raise ValueError(f"unknown setting: {name}")
    kind, ok = FIELDS[name]
    if kind is bool:
        if isinstance(value, bool):
            v = value
        elif isinstance(value, str) and value.lower() in ("true", "false", "1", "0"):
            v = value.lower() in ("true", "1")
        else:
            raise ValueError(f"{name}: expected true or false")
    elif kind is str:
        if not isinstance(value, str):
            raise ValueError(f"{name}: expected text")
        v = value
    else:
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise ValueError(f"{name}: expected a number")
        try:
            v = kind(float(value)) if kind is int else float(value)
        except ValueError:
            raise ValueError(f"{name}: expected a number")
        if kind is int and float(value) != int(float(value)):
            raise ValueError(f"{name}: expected a whole number")
    if not ok(v):
        raise ValueError(f"{name}: {value!r} is out of range")
    return v


def _store(v: Any) -> str:
    return ("1" if v else "0") if isinstance(v, bool) else str(v)


def save(conn, cfg: Dict[str, Any], changes: Dict[str, Any]) -> Dict[str, Any]:
    """Check every change first (nothing is stored on an error), then store and apply them."""
    parsed = {name: parse(name, value) for name, value in changes.items()}
    before = current(cfg)
    for name, v in parsed.items():
        rx_db.set_state(conn, f"setting.{name}", _store(v))
        cfg[name] = v
    if "parallel" in parsed:
        rx_db.set_state(conn, "parallel", str(parsed["parallel"]))  # the pool reads this key
    if any(name in parsed and parsed[name] != before[name] for name in ("target_vmaf", "max_ratio")):
        # files skipped as "too big at this quality" may qualify now: measure them again
        rx_db.requeue_quality_skips(conn)
    return current(cfg)


def load(conn, cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Apply the stored settings to cfg (a stored value that no longer parses is ignored)."""
    for name in FIELDS:
        raw = rx_db.get_state(conn, f"setting.{name}", "")
        if raw == "":
            continue
        try:
            cfg[name] = parse(name, raw)
        except ValueError:
            continue
    return cfg
