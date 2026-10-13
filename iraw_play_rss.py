import hashlib
import os
import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime, parsedate_to_datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET


SOURCE_URL = "https://iraw.rcc.jp/play/"
OUTPUT_FILE = "iraw_play.xml"

JST = timezone(timedelta(hours=9))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/154.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,image/avif,"
        "image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}

DATE_PATTERNS = [
    re.compile(
        r"(?P<year>20\d{2})年"
        r"(?P<month>\d{1,2})月"
        r"(?P<day>\d{1,2})日"
    ),
    re.compile(
        r"(?P<year>20\d{2})/"
        r"(?P<month>\d{1,2})/"
        r"(?P<day>\d{1,2})"
    ),
]


def clean_text(text):
    return re.sub(
        r"\s+",
        " ",
        text,
    ).strip()


def make_guid(link):
    return hashlib.sha256(
        link.encode("utf-8")
    ).hexdigest()


def fetch_page(url):
    for attempt in range(1, 4):
        try:
            print(
                f"取得 {attempt}/3: {url}"
            )

            response = requests.get(
                url,
                headers=HEADERS,
                timeout=30,
            )

            print(
                f"HTTP: {response.status_code}"
            )

            response.raise_for_status()

            return response.text

        except requests.RequestException as e:
            print(
                f"取得失敗: {e}"
            )

            if attempt < 3:
                print(
                    "10秒待って再試行します。"
                )
                time.sleep(10)

    return None


def parse_date(text):
    text = clean_text(text)

    for pattern in DATE_PATTERNS:
        match = pattern.search(text)

        if not match:
            continue

        try:
            return datetime(
                int(match.group("year")),
                int(match.group("month")),
                int(match.group("day")),
                12,
                0,
                0,
                tzinfo=JST,
            )

        except ValueError:
            continue

    return None


def load_existing_items():
    items = {}

    if not os.path.exists(
        OUTPUT_FILE
    ):
        return items

    try:
        tree = ET.parse(
            OUTPUT_FILE
        )

        root = tree.getroot()

        for item in root.findall(
            "./channel/item"
        ):
            link = (
                item.findtext(
                    "link",
                    "",
                ).strip()
            )

            guid = (
                item.findtext(
                    "guid",
                    "",
                ).strip()
            )

            if not guid and link:
                guid = make_guid(link)

            if not guid:
                continue

            items[guid] = {
                "title": item.findtext(
                    "title",
                    "",
                ),
                "link": link,
                "description": item.findtext(
                    "description",
                    "",
                ),
                "pubDate": item.findtext(
                    "pubDate",
                    "",
                ),
                "guid": guid,
            }

    except Exception as e:
        print(
            f"既存RSS読込失敗: {e}"
        )

    return items


def is_video_url(url):
    parsed = urlparse(url)

    if parsed.netloc != "iraw.rcc.jp":
        return False

    path = parsed.path.rstrip("/")

    return bool(
        re.fullmatch(
            r"/play/videos/\d+",
            path,
        )
    )


def extract_articles(html):
    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    articles = {}
    category = ""

    for element in soup.find_all(
        ["h2", "h3", "a"]
    ):
        if element.name in (
            "h2",
            "h3",
        ):
            heading = clean_text(
                element.get_text(
                    " ",
                    strip=True,
                )
            )

            if heading:
                category = heading

            continue

        href = element.get(
            "href",
            "",
        ).strip()

        if not href:
            continue

        link = urljoin(
            SOURCE_URL,
            href,
        )

        if not is_video_url(link):
            continue

        text = clean_text(
            element.get_text(
                " ",
                strip=True,
            )
        )

        if not text:
            continue

        date = parse_date(text)

        if date is None:
            continue

        title = text

        title = re.sub(
            r"\s*20\d{2}年"
            r"\d{1,2}月"
            r"\d{1,2}日.*$",
            "",
            title,
        )

        title = re.sub(
            r"\s*20\d{2}/"
            r"\d{1,2}/"
            r"\d{1,2}.*$",
            "",
            title,
        )

        title = clean_text(title)

        if not title:
            continue

        if link in articles:
            continue

        articles[link] = {
            "title": title,
            "link": link,
            "date": date,
            "category": category,
        }

    result = list(
        articles.values()
    )

    result.sort(
        key=lambda x: x["date"],
        reverse=True,
    )

    return result


def remove_future_existing_items(
    existing_items,
    today,
):
    cleaned = {}
    removed_count = 0

    for guid, item in existing_items.items():
        try:
            dt = parsedate_to_datetime(
                item["pubDate"]
            )

            if dt.tzinfo is None:
                dt = dt.replace(
                    tzinfo=JST
                )

            local_date = (
                dt.astimezone(JST).date()
            )

            if local_date > today:
                removed_count += 1
                continue

        except Exception:
            pass

        cleaned[guid] = item

    return cleaned, removed_count


def build_rss(
    existing_items,
    current_articles,
):
    now = datetime.now(JST)
    today = now.date()

    existing_items, removed_future = (
        remove_future_existing_items(
            existing_items,
            today,
        )
    )

    if removed_future:
        print(
            f"既存RSSから未来日付を除外: "
            f"{removed_future}件"
        )

    merged = dict(
        existing_items
    )

    new_count = 0
    future_count = 0

    for article in current_articles:
        # 公開日が今日より後なら、
        # 公開予定として今回はRSSに入れない。
        if article["date"].date() > today:
            future_count += 1

            print(
                "未来公開予定のため保留: "
                f"{article['date'].strftime('%Y-%m-%d')} "
                f"{article['title']}"
            )

            continue

        guid = make_guid(
            article["link"]
        )

        if guid not in merged:
            new_count += 1

        description_parts = []

        if article["category"]:
            description_parts.append(
                f"カテゴリ: "
                f"{article['category']}"
            )

        description_parts.append(
            "IRAW PLAY"
        )

        merged[guid] = {
            "title": article["title"],
            "link": article["link"],
            "description": " / ".join(
                description_parts
            ),
            "pubDate": format_datetime(
                article["date"]
            ),
            "guid": guid,
        }

    def get_pubdate(item):
        try:
            dt = parsedate_to_datetime(
                item["pubDate"]
            )

            if dt.tzinfo is None:
                dt = dt.replace(
                    tzinfo=timezone.utc
                )

            return dt

        except Exception:
            return datetime(
                1970,
                1,
                1,
                tzinfo=timezone.utc,
            )

    sorted_items = sorted(
        merged.values(),
        key=get_pubdate,
        reverse=True,
    )

    rss = ET.Element(
        "rss",
        version="2.0",
    )

    channel = ET.SubElement(
        rss,
        "channel",
    )

    ET.SubElement(
        channel,
        "title",
    ).text = "IRAW PLAY"

    ET.SubElement(
        channel,
        "link",
    ).text = SOURCE_URL

    ET.SubElement(
        channel,
        "description",
    ).text = (
        "IRAW by RCC「PLAY」の"
        "新着動画更新情報"
    )

    ET.SubElement(
        channel,
        "language",
    ).text = "ja"

    ET.SubElement(
        channel,
        "lastBuildDate",
    ).text = format_datetime(
        now
    )

    for data in sorted_items:
        item = ET.SubElement(
            channel,
            "item",
        )

        ET.SubElement(
            item,
            "title",
        ).text = data["title"]

        ET.SubElement(
            item,
            "link",
        ).text = data["link"]

        ET.SubElement(
            item,
            "description",
        ).text = data[
            "description"
        ]

        ET.SubElement(
            item,
            "pubDate",
        ).text = data[
            "pubDate"
        ]

        guid_element = ET.SubElement(
            item,
            "guid",
        )

        guid_element.set(
            "isPermaLink",
            "false",
        )

        guid_element.text = data[
            "guid"
        ]

    tree = ET.ElementTree(
        rss
    )

    ET.indent(
        tree,
        space="  ",
    )

    tree.write(
        OUTPUT_FILE,
        encoding="utf-8",
        xml_declaration=True,
    )

    return (
        len(sorted_items),
        new_count,
        future_count,
    )


def main():
    existing_items = (
        load_existing_items()
    )

    print(
        f"既存RSS件数: "
        f"{len(existing_items)}"
    )

    html = fetch_page(
        SOURCE_URL
    )

    if html is None:
        if existing_items:
            print(
                "取得に失敗したため、"
                "既存XMLを維持します。"
            )
            return

        raise RuntimeError(
            "IRAW PLAYの取得に失敗しました。"
        )

    articles = extract_articles(
        html
    )

    print()
    print(
        f"PLAY動画取得件数: "
        f"{len(articles)}"
    )

    print()
    print(
        "=== 取得動画 ==="
    )

    for i, article in enumerate(
        articles,
        1,
    ):
        print()
        print(
            f"{i:3d}. "
            f"{article['date'].strftime('%Y-%m-%d')} "
            f"[{article['category']}] "
            f"{article['title']}"
        )

        print(
            f"     {article['link']}"
        )

    if len(articles) < 20:
        if existing_items:
            print()
            print(
                "取得件数が異常に少ないため、"
                "既存XMLを維持します。"
            )
            return

        raise RuntimeError(
            f"取得件数が少なすぎます: "
            f"{len(articles)}"
        )

    total_count, new_count, future_count = (
        build_rss(
            existing_items,
            articles,
        )
    )

    print()
    print(
        f"未来公開予定で保留: "
        f"{future_count}"
    )

    print(
        f"今回の新規動画: "
        f"{new_count}"
    )

    print(
        f"RSS保存件数: "
        f"{total_count}"
    )

    print(
        f"保存先: "
        f"{os.path.abspath(OUTPUT_FILE)}"
    )


if __name__ == "__main__":
    main()