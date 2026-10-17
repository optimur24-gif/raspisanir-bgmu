"""The website adapter supplies its actual navigation tree to the bot."""

from dataclasses import dataclass
from datetime import date
from typing import Protocol


class SourceError(Exception):
    """A safe boundary for upstream connection or website format errors."""


@dataclass(frozen=True)
class Choice:
    key: str
    label: str


@dataclass(frozen=True)
class Menu:
    title: str
    choices: tuple[Choice, ...]


@dataclass(frozen=True)
class Section:
    title: str
    entries: tuple[str, ...]


@dataclass(frozen=True)
class Schedule:
    title: str
    day: date
    sections: tuple[Section, ...]
    notices: tuple[str, ...] = ()
    source_url: str = "https://bgmu-time.ru/"


class ScheduleSource(Protocol):
    async def menu(self, path: tuple[str, ...]) -> Menu | None:
        """Read available website sections; None means a date can be selected."""
        ...

    async def schedule(self, path: tuple[str, ...], day: date) -> Schedule:
        """Fetch a schedule directly from the website, without a local cache."""
        ...
