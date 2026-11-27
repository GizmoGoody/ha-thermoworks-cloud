"""Tracks how much of a ThermoWorks API key's daily request budget the integration uses.

The API reports only the per-minute budget, in the `X-RateLimit-*` headers, and nothing about
the daily one. So the integration counts every response it gets for the key, saves the count so
it survives restarts and reloads, and starts again at midnight UTC, when ThermoWorks resets it.
Every response counts toward the daily limit, including `304 Not Modified`. Requests from
anything else that uses the same key are not seen.

ThermoWorks counts requests per key, so the count is saved per key rather than per config
entry. Deleting an entry and adding the same key again the same day continues the count.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from datetime import date, datetime, timedelta
from http import HTTPStatus
import math
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from thermoworks_cloud.models import Account

from .const import DOMAIN, MAX_SCAN_INTERVAL_SECONDS, MIN_SCAN_INTERVAL_SECONDS

STORAGE_VERSION = 1
# Saving after every request would write to disk every few seconds. Unload and shutdown save
# straight away, so only a crash loses the requests of the last minute.
SAVE_DELAY_SECONDS = 60


@dataclass(frozen=True)
class Limits:
    """What `/v1/me` says about the key's plan."""

    per_day: int | None
    per_minute: int | None
    recommended_poll_seconds: int | None
    api_tier: str | None


def storage_key(api_key: str) -> str:
    """Return the storage key for an API key's count.

    Named after a hash of the key, so the key itself is never written to the file name.
    """
    return f"{DOMAIN}.requests.{hashlib.sha256(api_key.encode()).hexdigest()[:16]}"


def next_reset(now: datetime) -> datetime:
    """Return the next midnight UTC after `now`, when ThermoWorks resets the daily count."""
    today = dt_util.as_utc(now).date()
    return datetime.combine(today + timedelta(days=1), datetime.min.time(), dt_util.UTC)


class RequestBudget:
    """Counts the requests made with an API key against its daily limit."""

    def __init__(self, hass: HomeAssistant, api_key: str) -> None:
        """Create the budget. Call `async_load` before the first request."""
        self._hass = hass
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, storage_key(api_key))
        self._listeners: list[CALLBACK_TYPE] = []
        self._cancel_reset: CALLBACK_TYPE | None = None
        # Nothing is saved before the saved count is loaded, so it is never overwritten
        self._loaded = False
        self._day: date = dt_util.utcnow().date()
        self.requests_today = 0
        self.last_request: datetime | None = None
        self.limits: Limits | None = None
        self._limits_day: date | None = None
        self.rate_limited = False
        self.retry_after: int | None = None

    async def async_load(self) -> None:
        """Load the saved count, and start resetting it at midnight UTC."""
        if (stored := await self._store.async_load()) is not None:
            self._day = date.fromisoformat(stored["day"])
            self.requests_today = stored["requests_today"]
            if stored.get("last_request"):
                self.last_request = datetime.fromisoformat(stored["last_request"])
            if stored.get("limits"):
                self.limits = Limits(**stored["limits"])
                self._limits_day = date.fromisoformat(stored["limits_day"])
        self._loaded = True
        self._roll_over(dt_util.utcnow())
        self._schedule_reset()

    @property
    def limits_current(self) -> bool:
        """Whether the limits were read from `/v1/me` today (UTC).

        They are read once a day rather than on every reload, because each read is a request.
        """
        return self.limits is not None and self._limits_day == dt_util.utcnow().date()

    @callback
    def set_account(self, account: Account) -> None:
        """Keep the limits from a `/v1/me` response."""
        rate_limit = account.rate_limit
        self.limits = Limits(
            per_day=rate_limit.per_day if rate_limit else None,
            per_minute=rate_limit.per_minute if rate_limit else None,
            recommended_poll_seconds=account.recommended_poll_seconds,
            api_tier=account.plan.api_tier if account.plan else None,
        )
        self._limits_day = dt_util.utcnow().date()
        self._changed()

    @callback
    def record_response(self, status: int) -> None:
        """Count a response from the API, whatever its status."""
        now = dt_util.utcnow()
        self._roll_over(now)
        self.requests_today += 1
        self.last_request = now
        self.rate_limited = status == HTTPStatus.TOO_MANY_REQUESTS
        if not self.rate_limited:
            self.retry_after = None
        self._changed()

    @callback
    def set_retry_after(self, retry_after: int | None) -> None:
        """Keep how long ThermoWorks asked to wait after the latest rate limited response."""
        self.retry_after = retry_after
        self._changed()

    @property
    def requests_remaining(self) -> int | None:
        """Requests left until the reset, or None while the daily limit is unknown."""
        if self.limits is None or self.limits.per_day is None:
            return None
        return max(0, self.limits.per_day - self.requests_today)

    @property
    def reset(self) -> datetime:
        """When ThermoWorks resets the daily count."""
        return next_reset(dt_util.utcnow())

    def exhausted(self, scan_interval: int) -> datetime | None:
        """When the remaining requests run out at `scan_interval`, or the reset if they last.

        Each poll is one request. None while the daily limit is unknown.
        """
        if (remaining := self.requests_remaining) is None:
            return None
        now = dt_util.utcnow()
        return min(now + timedelta(seconds=remaining * scan_interval), next_reset(now))

    @property
    def sustainable_interval(self) -> int | None:
        """The shortest scan interval whose requests last until the reset.

        None while the daily limit is unknown.
        """
        if (remaining := self.requests_remaining) is None:
            return None
        now = dt_util.utcnow()
        seconds_left = (next_reset(now) - now).total_seconds()
        interval = math.ceil(seconds_left / remaining) if remaining else math.ceil(seconds_left)
        return min(max(MIN_SCAN_INTERVAL_SECONDS, interval), MAX_SCAN_INTERVAL_SECONDS)

    @callback
    def async_add_listener(self, update_callback: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """Call `update_callback` whenever the budget changes. Returns a function to stop."""
        self._listeners.append(update_callback)
        return lambda: self._listeners.remove(update_callback)

    async def async_unload(self) -> None:
        """Stop the midnight reset and save the count now."""
        if self._cancel_reset is not None:
            self._cancel_reset()
            self._cancel_reset = None
        if self._loaded:
            await self._store.async_save(self._data_to_save())

    def _roll_over(self, now: datetime) -> None:
        """Start a new count when a new UTC day has begun."""
        if (today := now.date()) != self._day:
            self._day = today
            self.requests_today = 0

    @callback
    def _schedule_reset(self) -> None:
        self._cancel_reset = async_track_point_in_utc_time(
            self._hass, self._async_reset, next_reset(dt_util.utcnow())
        )

    @callback
    def _async_reset(self, now: datetime) -> None:
        """Reset the count at midnight UTC, so the entities show it without waiting for a poll."""
        self._roll_over(now)
        self._changed()
        self._schedule_reset()

    @callback
    def _changed(self) -> None:
        if self._loaded:
            self._store.async_delay_save(self._data_to_save, SAVE_DELAY_SECONDS)
        for update_callback in list(self._listeners):
            update_callback()

    def _data_to_save(self) -> dict[str, Any]:
        return {
            "day": self._day.isoformat(),
            "requests_today": self.requests_today,
            "last_request": self.last_request.isoformat() if self.last_request else None,
            "limits": asdict(self.limits) if self.limits else None,
            "limits_day": self._limits_day.isoformat() if self._limits_day else None,
        }
