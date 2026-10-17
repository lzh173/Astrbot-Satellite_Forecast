"""SQLite persistence for satellite forecast user data and catalog entries."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Iterable

from .models import (
    FollowedSatellite,
    PendingInteraction,
    SatelliteRecord,
    SatelliteTransmitter,
    UserLocation,
    UserSettings,
    utc_now,
)


def _datetime_to_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


def _text_to_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(timezone.utc)


class Storage:
    """Small transactional SQLite repository.

    The caller controls the path, so the plugin can pass AstrBot's plugin data
    directory at runtime. ``:memory:`` is supported for tests.
    """

    def __init__(self, database_path: str | Path = ":memory:") -> None:
        self.database_path = str(database_path)
        if self.database_path != ":memory:":
            Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._connection = sqlite3.connect(
            self.database_path, check_same_thread=False
        )
        self._connection.row_factory = sqlite3.Row
        with self._lock:
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._create_schema()

    @classmethod
    def from_data_dir(cls, data_dir: str | Path, filename: str = "satellite_forecast.db") -> "Storage":
        """Create storage beneath an application-managed data directory."""

        return cls(Path(data_dir) / filename)

    def _create_schema(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS user_locations (
                user_id TEXT PRIMARY KEY,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                name TEXT NOT NULL,
                altitude_m REAL NOT NULL,
                maidenhead TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id TEXT PRIMARY KEY,
                min_elevation_deg REAL NOT NULL,
                reminder_minutes INTEGER NOT NULL,
                reminder_count INTEGER NOT NULL,
                notifications_enabled INTEGER NOT NULL,
                tle_storage_path TEXT,
                timezone TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS followed_satellites (
                user_id TEXT NOT NULL,
                norad_id TEXT NOT NULL,
                display_name TEXT NOT NULL,
                source TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (user_id, norad_id)
            );
            CREATE TABLE IF NOT EXISTS pending_interactions (
                user_id TEXT NOT NULL,
                interaction_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                expires_at TEXT,
                created_at TEXT NOT NULL,
                PRIMARY KEY (user_id, interaction_type)
            );
            CREATE TABLE IF NOT EXISTS satellites (
                norad_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                aliases TEXT NOT NULL,
                source TEXT NOT NULL,
                tle_line1 TEXT,
                tle_line2 TEXT,
                omm TEXT,
                transmitters TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS notification_targets (
                user_id TEXT PRIMARY KEY,
                origin TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        columns = {row["name"] for row in self._connection.execute("PRAGMA table_info(satellites)")}
        if "omm" not in columns:
            self._connection.execute("ALTER TABLE satellites ADD COLUMN omm TEXT")
        self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def set_notification_target(self, user_id: str, origin: str) -> None:
        with self._lock:
            self._connection.execute(
                "INSERT INTO notification_targets(user_id, origin, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET origin=excluded.origin, updated_at=excluded.updated_at",
                (user_id, origin, _datetime_to_text(utc_now())),
            )
            self._connection.commit()

    def list_notification_targets(self) -> list[tuple[str, str]]:
        with self._lock:
            rows = self._connection.execute("SELECT user_id, origin FROM notification_targets").fetchall()
        return [(row["user_id"], row["origin"]) for row in rows]

    def catalog_stats(self) -> tuple[int, int, datetime | None]:
        """Return satellite count, transmitter count, and newest catalog time."""
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) AS satellites, "
                "COALESCE(SUM(json_array_length(transmitters)), 0) AS transmitters, "
                "MAX(updated_at) AS updated_at FROM satellites"
            ).fetchone()
        updated = _text_to_datetime(row["updated_at"]) if row["updated_at"] else None
        return int(row["satellites"]), int(row["transmitters"]), updated

    def __enter__(self) -> "Storage":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def set_location(self, location: UserLocation) -> UserLocation:
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO user_locations
                    (user_id, latitude, longitude, name, altitude_m, maidenhead, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    latitude=excluded.latitude, longitude=excluded.longitude,
                    name=excluded.name, altitude_m=excluded.altitude_m,
                    maidenhead=excluded.maidenhead, updated_at=excluded.updated_at
                """,
                (
                    location.user_id,
                    location.latitude,
                    location.longitude,
                    location.name,
                    location.altitude_m,
                    location.maidenhead,
                    _datetime_to_text(location.updated_at),
                ),
            )
            self._connection.commit()
        return location

    def get_location(self, user_id: str) -> UserLocation | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM user_locations WHERE user_id = ?", (user_id,)
            ).fetchone()
        if row is None:
            return None
        return UserLocation(
            user_id=row["user_id"],
            latitude=row["latitude"],
            longitude=row["longitude"],
            name=row["name"],
            altitude_m=row["altitude_m"],
            maidenhead=row["maidenhead"],
            updated_at=_text_to_datetime(row["updated_at"]),
        )

    def delete_location(self, user_id: str) -> bool:
        with self._lock:
            cursor = self._connection.execute(
                "DELETE FROM user_locations WHERE user_id = ?", (user_id,)
            )
            self._connection.commit()
        return cursor.rowcount > 0

    def set_settings(self, settings: UserSettings) -> UserSettings:
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO user_settings
                    (user_id, min_elevation_deg, reminder_minutes, reminder_count,
                     notifications_enabled, tle_storage_path, timezone, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    min_elevation_deg=excluded.min_elevation_deg,
                    reminder_minutes=excluded.reminder_minutes,
                    reminder_count=excluded.reminder_count,
                    notifications_enabled=excluded.notifications_enabled,
                    tle_storage_path=excluded.tle_storage_path,
                    timezone=excluded.timezone, updated_at=excluded.updated_at
                """,
                (
                    settings.user_id,
                    settings.min_elevation_deg,
                    settings.reminder_minutes,
                    settings.reminder_count,
                    int(settings.notifications_enabled),
                    settings.tle_storage_path,
                    settings.timezone,
                    _datetime_to_text(settings.updated_at),
                ),
            )
            self._connection.commit()
        return settings

    def get_settings(self, user_id: str) -> UserSettings:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM user_settings WHERE user_id = ?", (user_id,)
            ).fetchone()
        if row is None:
            return UserSettings(user_id=user_id)
        timezone_name = row["timezone"] or "Asia/Shanghai"
        return UserSettings(
            user_id=row["user_id"],
            min_elevation_deg=row["min_elevation_deg"],
            reminder_minutes=row["reminder_minutes"],
            reminder_count=row["reminder_count"],
            notifications_enabled=bool(row["notifications_enabled"]),
            tle_storage_path=row["tle_storage_path"],
            timezone=timezone_name,
            updated_at=_text_to_datetime(row["updated_at"]),
        )

    def add_followed_satellite(self, followed: FollowedSatellite) -> FollowedSatellite:
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO followed_satellites
                    (user_id, norad_id, display_name, source, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, norad_id) DO UPDATE SET
                    display_name=excluded.display_name, source=excluded.source
                """,
                (
                    followed.user_id,
                    followed.norad_id,
                    followed.display_name,
                    followed.source,
                    _datetime_to_text(followed.created_at),
                ),
            )
            self._connection.commit()
        return followed

    def remove_followed_satellite(self, user_id: str, norad_id: str) -> bool:
        with self._lock:
            cursor = self._connection.execute(
                "DELETE FROM followed_satellites WHERE user_id = ? AND norad_id = ?",
                (user_id, norad_id),
            )
            self._connection.commit()
        return cursor.rowcount > 0

    def list_followed_satellites(self, user_id: str) -> list[FollowedSatellite]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM followed_satellites WHERE user_id = ? ORDER BY created_at, norad_id",
                (user_id,),
            ).fetchall()
        return [
            FollowedSatellite(
                user_id=row["user_id"],
                norad_id=row["norad_id"],
                display_name=row["display_name"],
                source=row["source"],
                created_at=_text_to_datetime(row["created_at"]),
            )
            for row in rows
        ]

    def set_pending_interaction(self, pending: PendingInteraction) -> PendingInteraction:
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO pending_interactions
                    (user_id, interaction_type, payload, expires_at, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, interaction_type) DO UPDATE SET
                    payload=excluded.payload, expires_at=excluded.expires_at,
                    created_at=excluded.created_at
                """,
                (
                    pending.user_id,
                    pending.interaction_type,
                    json.dumps(pending.payload, ensure_ascii=True, sort_keys=True),
                    _datetime_to_text(pending.expires_at) if pending.expires_at else None,
                    _datetime_to_text(pending.created_at),
                ),
            )
            self._connection.commit()
        return pending

    def get_pending_interaction(
        self, user_id: str, interaction_type: str
    ) -> PendingInteraction | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM pending_interactions
                WHERE user_id = ? AND interaction_type = ?
                """,
                (user_id, interaction_type),
            ).fetchone()
        if row is None:
            return None
        pending = PendingInteraction(
            user_id=row["user_id"],
            interaction_type=row["interaction_type"],
            payload=json.loads(row["payload"]),
            expires_at=(
                _text_to_datetime(row["expires_at"]) if row["expires_at"] else None
            ),
            created_at=_text_to_datetime(row["created_at"]),
        )
        if pending.expires_at is not None and pending.expires_at <= utc_now():
            self.clear_pending_interaction(user_id, interaction_type)
            return None
        return pending

    def clear_pending_interaction(self, user_id: str, interaction_type: str) -> bool:
        with self._lock:
            cursor = self._connection.execute(
                "DELETE FROM pending_interactions WHERE user_id = ? AND interaction_type = ?",
                (user_id, interaction_type),
            )
            self._connection.commit()
        return cursor.rowcount > 0

    def purge_expired_interactions(self, now: datetime | None = None) -> int:
        now = now or utc_now()
        with self._lock:
            cursor = self._connection.execute(
                "DELETE FROM pending_interactions WHERE expires_at IS NOT NULL AND expires_at <= ?",
                (_datetime_to_text(now),),
            )
            self._connection.commit()
        return cursor.rowcount

    def upsert_satellite(self, satellite: SatelliteRecord) -> SatelliteRecord:
        transmitters = [
            {
                "description": item.description,
                "mode": item.mode,
                "uplink": item.uplink,
                "downlink": item.downlink,
                "status": item.status,
                "service": item.service,
                "uplink_low_hz": item.uplink_low_hz,
                "uplink_high_hz": item.uplink_high_hz,
                "downlink_low_hz": item.downlink_low_hz,
                "downlink_high_hz": item.downlink_high_hz,
            }
            for item in satellite.transmitters
        ]
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO satellites
                    (norad_id, name, aliases, source, tle_line1, tle_line2,
                     omm, transmitters, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(norad_id) DO UPDATE SET
                    name=excluded.name, aliases=excluded.aliases, source=excluded.source,
                    tle_line1=excluded.tle_line1, tle_line2=excluded.tle_line2,
                    omm=excluded.omm,
                    transmitters=excluded.transmitters, updated_at=excluded.updated_at
                """,
                (
                    satellite.norad_id,
                    satellite.name,
                    json.dumps(list(satellite.aliases), ensure_ascii=True),
                    satellite.source,
                    satellite.tle_line1,
                    satellite.tle_line2,
                    json.dumps(satellite.omm, ensure_ascii=True, sort_keys=True) if satellite.omm else None,
                    json.dumps(transmitters, ensure_ascii=True, sort_keys=True),
                    _datetime_to_text(satellite.updated_at),
                ),
            )
            self._connection.commit()
        return satellite

    def get_satellite(self, norad_id: str) -> SatelliteRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM satellites WHERE norad_id = ?", (norad_id,)
            ).fetchone()
        return self._satellite_from_row(row) if row else None

    def list_satellites(self, source: str | None = None) -> list[SatelliteRecord]:
        query = "SELECT * FROM satellites"
        parameters: tuple[str, ...] = ()
        if source is not None:
            query += " WHERE source = ?"
            parameters = (source,)
        query += " ORDER BY name, norad_id"
        with self._lock:
            rows = self._connection.execute(query, parameters).fetchall()
        return [self._satellite_from_row(row) for row in rows]

    @staticmethod
    def _satellite_from_row(row: sqlite3.Row) -> SatelliteRecord:
        transmitters = tuple(
            SatelliteTransmitter(**item) for item in json.loads(row["transmitters"])
        )
        return SatelliteRecord(
            norad_id=row["norad_id"],
            name=row["name"],
            aliases=tuple(json.loads(row["aliases"])),
            source=row["source"],
            tle_line1=row["tle_line1"],
            tle_line2=row["tle_line2"],
            omm=json.loads(row["omm"]) if row["omm"] else None,
            transmitters=transmitters,
            updated_at=_text_to_datetime(row["updated_at"]),
        )

    def save_satellites(self, satellites: Iterable[SatelliteRecord]) -> int:
        count = 0
        for satellite in satellites:
            self.upsert_satellite(satellite)
            count += 1
        return count
