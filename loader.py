import csv
import os
from datetime import datetime, timedelta, time
from time import sleep
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv


class TimeFormatter:
    def __init__(self, timezone: str):
        self.tz = ZoneInfo(timezone)

    def _target_date(self, days_ago: int = 0):
        return datetime.now(self.tz).date() - timedelta(days=days_ago)

    def get_period(self, days_ago: int):
        target = self._target_date(days_ago)

        start = datetime.combine(target, time.min, tzinfo=self.tz)
        end = datetime.combine(target, time.max, tzinfo=self.tz)

        return {
            "date_from": int(start.timestamp()),
            "date_to": int(end.timestamp()),
        }

    def get_date_str(self, days_ago: int = 0, fmt: str = "%Y-%m-%d") -> str:
        return self._target_date(days_ago).strftime(fmt)

    def iso_to_local_str(self, iso_value: str, fmt: str = "%d-%m-%Y") -> str:
        dt_utc = datetime.fromisoformat(iso_value.replace("Z", "+00:00"))
        return dt_utc.astimezone(self.tz).strftime(fmt)

    @staticmethod
    def time_now(fmt: str = "%d-%m-%Y %H:%M:%S"):
        return datetime.now().strftime(fmt)

class WBClient:
    def __init__(self):
        self.TIMEOUT = 30
        self.DELAY = 2

        load_dotenv()
        
        self.timezone = os.getenv("WB_TIMEZONE") or "UTC"
        self.base_market_url = os.getenv("WB_BASE_MARKET_URL")
        self.base_content_url = os.getenv("WB_BASE_CONTENT_URL")
        self._token = os.getenv("WB_TOKEN")

        self.time_formatter = TimeFormatter(self.timezone)

        self.headers = { 
            "Authorization": self._token,
            "Content-Type": "application/json",
        }

    def get_cards(self, limit: int = 100):
        cursor = {"limit": limit}
        cards = []

        with httpx.Client(timeout=self.TIMEOUT) as client:
            while True:
                payload = {
                    "settings": {
                        "sort": {
                            "ascending": True
                        },
                        "cursor": cursor,
                        "filter": {
                            "withPhoto": -1,
                            "allowedCategoriesOnly": False
                        }
                    }
                }

                try:
                    data = (
                        client.post(
                            f"{self.base_content_url}/content/v2/get/cards/list",
                            headers=self.headers,
                            json=payload,
                        )
                        .raise_for_status()
                        .json()
                    )

                    cards.extend(
                        {
                            "product_id": card.get("nmID"),
                            "product_name": card.get("title"),
                        }
                        for card in data.get("cards", [])
                    )

                    cursor_response = data.get("cursor", {})

                    cursor = {
                        "limit": limit,
                        "updatedAt": cursor_response.get("updatedAt"),
                        "nmID": cursor_response.get("nmID"),
                    }

                    if not cursor["updatedAt"] or not cursor["nmID"]:
                        break

                    sleep(self.DELAY)

                except Exception as error:
                    raise RuntimeError("Ошибка получения карточек") from error

        return cards

    def get_orders(self, limit: int = 1000, days_ago: int = 0):
        next_ = 0
        period = self.time_formatter.get_period(days_ago)

        params = {
            "limit": limit,
            "next": next_,
            "dateFrom": period["date_from"],
            "dateTo": period["date_to"],
        }

        with httpx.Client(timeout=self.TIMEOUT) as client:
            while True:
                params["next"] = next_

                try:
                    data = (
                        client.get(
                            f"{self.base_market_url}/api/v3/orders",
                            headers=self.headers,
                            params=params,
                        )
                        .raise_for_status()
                        .json()
                    )

                    yield [
                        {
                            "order_id": order.get("id"),
                            "order_date": self.time_formatter.iso_to_local_str(order.get("createdAt")),
                            "article": order.get("article"),
                            "product_id": order.get("nmId"),
                            "price": order.get("price", 0) / 100,
                        }
                        for order in data.get("orders", [])
                    ]

                    next_ = data.get("next", 0)

                    if next_ == 0:
                        break

                    sleep(self.DELAY)

                except Exception as error:
                    raise RuntimeError("Ошибка получения заказов") from error

    def get_orders_status(self, order_ids: list[int]):
        if not order_ids:
            return []

        with httpx.Client(timeout=self.TIMEOUT) as client:
            try:
                data = (
                    client.post(
                        f"{self.base_market_url}/api/v3/orders/status",
                        headers=self.headers,
                        json={"orders": order_ids},
                    )
                    .raise_for_status()
                    .json()
                )

                sleep(self.DELAY)

                return [
                    {
                        "order_id": status.get("id"),
                        "supplier_status": status.get("supplierStatus"),
                        "wb_status": status.get("wbStatus"),
                    }
                    for status in data.get("orders", [])
                ]

            except Exception as error:
                raise RuntimeError("Ошибка получения статусов") from error

    def enrich_orders(self, orders, statuses, cards):
        status_map = {s["order_id"]: s for s in statuses}

        enriched = []

        for order in orders:
            status = status_map.get(order["order_id"], {})

            enriched.append({
                "order_date": order["order_date"],
                "article": order["article"],
                "product_name": cards.get(order["product_id"], {}).get("product_name"),
                "status": WBMapper.resolve_state(
                    status.get("wb_status"),
                    status.get("supplier_status"),
                ),
                "price": order["price"],
            })

        return enriched

    def export_orders(self, batch_size: int = 500, days_ago: int = 0):
            print(f"[{self.time_formatter.time_now()}] Получение данных из WB")

            buffer = []
            os.makedirs("files", exist_ok=True)

            file_path = os.path.join(
                "files",
                f"orders-{self.time_formatter.get_date_str(days_ago)}.csv",
            )

            with open(file_path, mode="w", newline="", encoding="utf-8") as file:
                writer = csv.DictWriter(
                    file,
                    fieldnames=[
                        "order_date",
                        "article",
                        "product_name",
                        "status",
                        "price",
                    ],
                )
                writer.writeheader()

                cards_map = {
                    card["product_id"]: card
                    for card in self.get_cards()
                }

                for orders_batch in self.get_orders(
                    limit=batch_size,
                    days_ago=days_ago,
                ):
                    statuses = self.get_orders_status(
                        [order["order_id"] for order in orders_batch]
                    )

                    buffer.extend(
                        self.enrich_orders(
                            orders_batch,
                            statuses,
                            cards_map,
                        )
                    )

                    if len(buffer) >= batch_size:
                        writer.writerows(buffer)
                        buffer.clear()

                if buffer:
                    writer.writerows(buffer)

            print(f"[{self.time_formatter.time_now()}] Файл сохранен: {file_path}")
            
            return file_path


class WBMapper:
    WB_STATUS_MAP = {
        "canceled": "cancelled",
        "canceled_by_client": "cancelled",
        "defect": "cancelled",
        "sold": "done",
        "ready_for_pickup": "in_delivery",
    }

    SUPPLIER_STATUS_MAP = {
        "new": "new",
        "confirm": "in_progress",
        "complete": "in_delivery",
        "cancel": "cancelled",
        "cancel_carrier": "cancelled",
    }

    DEFAULT = "in_progress"

    @staticmethod
    def resolve_state(wb_status: str, supplier_status: str | None):
        if wb_status in WBMapper.WB_STATUS_MAP:
            return WBMapper.WB_STATUS_MAP[wb_status]

        if supplier_status in WBMapper.SUPPLIER_STATUS_MAP:
            return WBMapper.SUPPLIER_STATUS_MAP[supplier_status]

        return WBMapper.DEFAULT

