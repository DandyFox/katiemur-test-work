from loader import WBClient
from bot import TGBotClient


if __name__ == "__main__":
    wb = WBClient()
    file_path = wb.export_orders(days_ago=1)

    tg = TGBotClient()
    tg.create_report(file_path=file_path)