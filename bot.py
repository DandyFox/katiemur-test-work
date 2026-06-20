import csv
import os
from collections import Counter

import httpx
from dotenv import load_dotenv


class TGBotClient:
    def __init__(self):
        self.TIMEOUT = 30
        self.DELAY = 2

        load_dotenv()

        self.base_url = os.getenv("TG_BASE_URL")
        self._bot_token = os.getenv("TG_BOT_TOKEN")
        self.chat_id = os.getenv("TG_CHAT_ID")
        self.proxy = os.getenv("TG_PROXY") or None

    def read_csv(self, file_path: str):
        with open(file_path, mode="r", encoding="utf-8") as file:
            reader = csv.DictReader(file)
            return list(reader)

    def send_message(self, message: str):
        params = {
            "chat_id": f"-100{self.chat_id}",
            "text": message,
        }

        try:
            with httpx.Client(
                timeout=self.TIMEOUT,
                proxy=self.proxy,
            ) as client:
                client.get(
                    f"{self.base_url}{self._bot_token}/sendMessage",
                    params=params,
                ).raise_for_status()
        
        except Exception as error:
            raise RuntimeError("Ошибка отправки сообщения в Телеграм") from error

    def get_top_articles(self, orders: list[dict], top_n: int = 3):
        counter = Counter(order.get("article") for order in orders)
        return counter.most_common(top_n)

    def build_top_articles_message(self, top_articles):
        lines = ["📊 Топ-3 артикулов по количеству заказов за вчерашний день:\n"]

        for i, (article, count) in enumerate(top_articles, 1):
            lines.append(f"{i}. {article} - {count} заказов/а")

        return "\n".join(lines)

    def create_report(self, file_path: str):
        orders = self.read_csv(file_path)
        top_articles = self.get_top_articles(orders, top_n=3)
        message = self.build_top_articles_message(top_articles)
        self.send_message(message)

