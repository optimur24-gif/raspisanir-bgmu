"""Adapter for the API used by bgmu-time.ru/assets/app.js."""

from collections import OrderedDict
import asyncio
from datetime import date, datetime, timedelta, timezone
import re
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from .cache import ResponseCache
from .source import Choice, GroupMatch, GroupUnavailable, Menu, Schedule, Section, SourceError
from .website import WebsiteClient


TYPES = {"group": "Группы", "teacher": "Преподаватели", "room": "Аудитории"}
SEMESTERS = {"1": "Осенний семестр", "2": "Весенний семестр"}


def natural_key(value):
    return tuple((0, int(part)) if part.isdigit() else (1, part.casefold())
                 for part in re.split(r"(\d+)", str(value)))


def course_key(item):
    value = item.get("course")
    return str(value) if value not in (None, "", 0) else "_"


def readable_details(details):
    """Combine subgroup rows only when teacher and location match as on site."""
    combined = OrderedDict()
    for detail in details:
        identity = lambda key, label: str(detail.get(key) or detail.get(label) or "")
        key = (identity("groupId", "group"), identity("teacherId", "teacher"),
               identity("roomId", "room"), str(detail.get("building") or ""))
        if key not in combined:
            combined[key] = {"detail": detail, "subgroups": set(), "whole": False}
        row = combined[key]
        subgroup = str(detail.get("subgroup") or "").strip()
        if subgroup:
            row["subgroups"].add(subgroup)
        else:
            row["whole"] = True
    lines = []
    for row in combined.values():
        detail = row["detail"]
        group = str(detail.get("group") or "")
        subgroups = sorted(row["subgroups"], key=natural_key) if not row["whole"] else []
        audience = " · ".join(part for part in (
            group, "Подгруппа " + ", ".join(subgroups) if subgroups else "",
        ) if part)
        teacher = str(detail.get("teacher") or "Преподаватель уточняется")
        places = list(dict.fromkeys(str(detail.get(key) or "").strip() for key in ("room", "building")))
        places = [place for place in places if place and not any(place != other and place in other for other in places)]
        place = ", ".join(places) or "Место уточняется"
        lines.append("\n".join(part for part in ("👥 " + audience if audience else "", "👨‍🏫 " + teacher, "📍 " + place) if part))
    return lines


class BGMUSource:
    def __init__(self, website: WebsiteClient, *, cache_ttl=0):
        self.website = website
        self.cache = ResponseCache(ttl=cache_ttl)
        self.refresh_lock = asyncio.Lock()

    async def _api(self, **params):
        return await self.cache.get(self._cache_key(params), lambda: self._request_api(**params))

    @staticmethod
    def _cache_key(params):
        return tuple(sorted((key, str(value)) for key, value in params.items()))

    async def _request_api(self, **params):
        response = await self.website.get("/api.php", params=params)
        try:
            data = response.json()
        except ValueError:
            raise SourceError("Сайт вернул ответ неподходящего формата.") from None
        if not isinstance(data, dict) or data.get("success") is not True:
            raise SourceError("Источник не смог получить расписание. Попробуйте позже.")
        return data

    async def _catalog(self, path):
        data = await self._api(action="catalog", type=path[0], year=path[1], semester=path[2])
        return self._catalog_items(data)

    @staticmethod
    def _catalog_items(data):
        items = data.get("items")
        if not isinstance(items, list) or any(
            not isinstance(item, dict) or not str(item.get("id", "")).isdigit() or not isinstance(item.get("name"), str)
            for item in items
        ):
            raise SourceError("Изменился формат каталога сайта.")
        return sorted(items, key=lambda item: natural_key(item["name"]))

    @staticmethod
    def _validate_path(path):
        if len(path) > 7 or (path and path[0] not in TYPES):
            raise SourceError("Выберите раздел заново через /start.")
        if len(path) > 1 and not path[1].isdigit():
            raise SourceError("Некорректный учебный год.")
        if len(path) > 2 and path[2] not in SEMESTERS:
            raise SourceError("Некорректный семестр.")
        if path and path[0] != "group" and len(path) > 4:
            raise SourceError("Выберите раздел заново.")

    @staticmethod
    def _entity_id(path):
        if not path:
            raise SourceError("Сначала выберите раздел расписания.")
        index = 5 if path[0] == "group" else 3
        if len(path) <= index or not path[index].isdigit() or int(path[index]) <= 0:
            raise SourceError("Выберите группу, преподавателя или аудиторию заново.")
        return path[index]

    async def _schedule_data(self, path):
        entity_id = self._entity_id(path)
        data = await self._api(action="schedule", type=path[0], id=entity_id, year=path[1], semester=path[2])
        self._validate_schedule(data)
        return data

    @staticmethod
    def _validate_schedule(data):
        events = data.get("events")
        if not isinstance(data.get("entity"), dict) or not isinstance(events, list):
            raise SourceError("Изменился формат расписания сайта.")
        for event in events:
            if not isinstance(event, dict) or not isinstance(event.get("date"), str) or not isinstance(event.get("details"), list):
                raise SourceError("Изменился формат занятия в источнике.")
            if any(not isinstance(detail, dict) for detail in event["details"]):
                raise SourceError("Изменился формат подгрупп в источнике.")

    async def menu(self, path):
        self._validate_path(path)
        if not path:
            return Menu("Выберите раздел расписания", tuple(Choice(key, label) for key, label in TYPES.items()))
        if len(path) == 1:
            data = await self._api(action="period")
            years = data.get("years")
            if not isinstance(years, dict) or not years:
                raise SourceError("Сайт не вернул доступные учебные годы.")
            return Menu("Выберите учебный год", tuple(
                Choice(str(key), str(label)) for key, label in sorted(years.items(), key=lambda pair: natural_key(pair[0]), reverse=True)
            ))
        if len(path) == 2:
            return Menu("Выберите семестр", tuple(Choice(key, label) for key, label in SEMESTERS.items()))
        if path[0] != "group":
            if len(path) == 3:
                items = await self._catalog(path)
                return Menu("Выберите преподавателя" if path[0] == "teacher" else "Выберите аудиторию", tuple(
                    Choice(str(item["id"]), item["name"] + (" — " + str(item["subtitle"]) if path[0] == "room" and item.get("subtitle") else "")) for item in items
                ))
            if len(path) == 4:
                return None
            raise SourceError("Выберите раздел заново.")
        if len(path) >= 6:
            if len(path) == 7:
                return None
            data = await self._schedule_data(path)
            subgroups = sorted({str(detail["subgroup"]) for event in data["events"]
                                for detail in event["details"] if detail.get("subgroup")}, key=natural_key)
            if not subgroups:
                return None
            return Menu("Выберите подгруппу", (Choice("*", "Все подгруппы"),) + tuple(
                Choice(subgroup, "Подгруппа " + subgroup) for subgroup in subgroups
            ))
        items = await self._catalog(path)
        if len(path) == 3:
            faculties = sorted({str(item.get("faculty") or "") for item in items}, key=natural_key)
            return Menu("Выберите факультет", tuple(Choice(value, value or "Факультет не указан") for value in faculties))
        items = [item for item in items if str(item.get("faculty") or "") == path[3]]
        if len(path) == 4:
            courses = sorted({course_key(item) for item in items}, key=natural_key)
            return Menu("Выберите курс", tuple(Choice(value, value + " курс" if value != "_" else "Курс не указан") for value in courses))
        items = [item for item in items if course_key(item) == path[4]]
        return Menu("Выберите группу", tuple(Choice(str(item["id"]), item["name"]) for item in items))

    async def schedule(self, path, day: date):
        self._validate_path(path)
        data = await self._schedule_data(path)
        return self._format_schedule(data, path, day)

    def _format_schedule(self, data, path, day):
        subgroup = path[6] if path[0] == "group" and len(path) == 7 and path[6] != "*" else ""
        events = sorted(
            (event for event in data["events"] if event["date"] == day.isoformat()),
            key=lambda event: (str(event.get("start") or "99:99"), str(event.get("end") or "")),
        )
        sections = []
        for event in events:
            details = event["details"]
            if subgroup and details:
                details = [detail for detail in details if not detail.get("subgroup") or str(detail["subgroup"]) == subgroup]
                if not details:
                    continue
            time = "–".join(str(event.get(key)) for key in ("start", "end") if event.get(key)) or "Время уточняется"
            kind = str(event.get("kind") or "Занятие")
            heading = f"{time} · {kind}" + (" · Цикл" if event.get("cycle") else "")
            entries = [str(event.get("subject") or "Предмет уточняется"), *readable_details(details)]
            groups = [str(group["name"]) for group in event.get("groups", []) if isinstance(group, dict) and group.get("name")]
            if groups:
                entries.insert(1, "Группы: " + ", ".join(dict.fromkeys(groups)))
            if event.get("comment"):
                entries.append(str(event["comment"]))
            sections.append(Section(heading, tuple(entries)))
        year = int(path[1])
        year_label = f"{2000 + year}/{2001 + year}" if year < 100 else str(year)
        notices = [f"{year_label} · {SEMESTERS[path[2]]}", "Время занятий: Уфа, UTC+5."]
        if data["entity"].get("subtitle"):
            notices.append(str(data["entity"]["subtitle"]))
        meta = data.get("meta") or {}
        if isinstance(meta, dict):
            if meta.get("stale"):
                notices.append("Сайт вернул сохранённую копию: возможны изменения расписания.")
            if meta.get("warning"):
                notices.append(str(meta["warning"]))
            if meta.get("updatedAt"):
                updated = str(meta["updatedAt"])
                try:
                    timestamp = datetime.fromisoformat(updated)
                    if timestamp.tzinfo is not None:
                        updated = timestamp.astimezone(ZoneInfo("Asia/Yekaterinburg")).strftime("%d.%m.%Y %H:%M") + " (Уфа)"
                except ValueError:
                    pass
                notices.append("Обновление на сайте: " + updated)
        if data.get("undatedRecords"):
            notices.append("В источнике есть записи без точной даты. Проверьте их в ИСУ БГМУ.")
        params = dict(type=path[0], id=self._entity_id(path), year=path[1], semester=path[2], date=day.isoformat(), view="day")
        if subgroup:
            params["subgroup"] = subgroup
        title = str(data["entity"].get("name") or TYPES[path[0]])
        if subgroup:
            title += " · Подгруппа " + subgroup
        return Schedule(title, day, tuple(sections), tuple(notices), "https://bgmu-time.ru/?" + urlencode(params))

    async def week(self, path, day):
        self._validate_path(path)
        data = await self._schedule_data(path)
        monday = day - timedelta(days=day.weekday())
        if monday > date.max - timedelta(days=6):
            raise SourceError("Неделя выходит за пределы допустимых дат.")
        return tuple(self._format_schedule(data, path, monday + timedelta(days=index)) for index in range(7))

    async def current_group_start(self):
        data = await self._api(action="period")
        period = data.get("period") or {}
        year, semester = str(period.get("year", "")), str(period.get("semester", ""))
        path = ("group", year, semester)
        self._validate_path(path)
        label = str((data.get("years") or {}).get(year, period.get("label") or year))
        return GroupMatch(path, ("Группы", label, SEMESTERS[semester]))

    @staticmethod
    def _group_match(start, item, subgroup=None):
        faculty = str(item.get("faculty") or "")
        course = course_key(item)
        path = start.path + (faculty, course, str(item["id"]))
        labels = start.labels + (faculty or "Факультет не указан", course + " курс" if course != "_" else "Курс не указан", item["name"])
        if subgroup is not None:
            path += (subgroup,)
            labels += ("Все подгруппы" if subgroup == "*" else "Подгруппа " + subgroup,)
        return GroupMatch(path, labels)

    async def search_groups(self, query):
        start = await self.current_group_start()
        items = await self._catalog(start.path)
        normalize = lambda value: re.sub(r"[^\w]", "", str(value).casefold().replace("ё", "е"))
        needle = normalize(query)
        return tuple(self._group_match(start, item) for item in items if needle in normalize(item["name"]))

    async def resolve_group(self, path):
        start = await self.current_group_start()
        items = await self._catalog(start.path)
        selected = next((item for item in items if str(item["id"]) == path[5]), None)
        if selected is None:
            raise GroupUnavailable("Группа отсутствует в текущем периоде. Выберите её заново.")
        return self._group_match(start, selected, path[6] if len(path) == 7 else None)

    async def refresh(self, paths=()):
        async with self.refresh_lock:
            # Build the next generation before discarding any current values.
            # A failed upstream request leaves the previous generation intact.
            period = await self._request_api(action="period")
            current = period.get("period") or {}
            year, semester = str(current.get("year", "")), str(current.get("semester", ""))
            self._validate_path(("group", year, semester))
            values = {self._cache_key({"action": "period"}): period}
            valid_groups = set()
            for entity_type in TYPES:
                params = dict(action="catalog", type=entity_type, year=year, semester=semester)
                catalog = await self._request_api(**params)
                self._catalog_items(catalog)
                values[self._cache_key(params)] = catalog
                if entity_type == "group":
                    valid_groups = {str(item["id"]) for item in catalog["items"]}
            group_ids = list(dict.fromkeys(path[5] for path in paths if len(path) >= 6 and path[0] == "group" and path[5] in valid_groups))
            limit = max(0, self.cache.max_entries - len(values))
            for group_id in group_ids[:limit]:
                params = dict(action="schedule", type="group", id=group_id, year=year, semester=semester)
                payload = await self._request_api(**params)
                self._validate_schedule(payload)
                values[self._cache_key(params)] = payload
            self.cache.replace(values)
            return {"refreshed_at": datetime.now(timezone.utc).isoformat(), "catalogs": 3,
                    "groups": min(len(group_ids), limit), "on_demand_groups": max(0, len(group_ids) - limit)}

    async def aclose(self):
        await self.website.aclose()
