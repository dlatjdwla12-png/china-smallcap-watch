import os

import re

import json

import requests

from datetime import datetime, timezone

PRICE_MAX = 20.0

VOLUME_MIN = 100_000

VOLUME_MULTIPLE_MIN = 2.0

MAX_ALERTS = 10

NTFY_TOPIC = os.getenv("NTFY_TOPIC", "").strip()

SEC_USER_EMAIL = os.getenv("SEC_USER_EMAIL", "scanner@example.com")

CHINA_TERMS = [

    "china", "chinese", "hong kong", "beijing", "shanghai",

    "shenzhen", "guangzhou", "macau", "macao",

    "people's republic of china", "variable interest entity", "v.i.e."

]

DILUTION_TERMS = [

    "at-the-market", "at the market offering",

    "equity distribution agreement", "shelf registration",

    "registered direct offering", "public offering",

    "private placement", "convertible note",

    "convertible debenture", "warrant", "reverse stock split",

    "dilution", "securities purchase agreement",

    "prospectus supplement", "equity line of credit", "424b5"

]

HEADERS = {

    "User-Agent": f"ChinaSmallcapWatch {SEC_USER_EMAIL}",

    "Accept": "application/json"

}

def get_json(url, headers=None, params=None):

    try:

        response = requests.get(

            url, headers=headers or {}, params=params, timeout=20

        )

        response.raise_for_status()

        return response.json()

    except Exception as exc:

        print(f"Request failed: {url} - {exc}")

        return None

def get_candidates():

    found = {}

    url = "https://query1.finance.yahoo.com/v1/finance/screener"

    for screen in ["small_cap_gainers", "most_actives"]:

        params = {

            "scrIds": screen,

            "count": 100,

            "start": 0,

            "formatted": "false"

        }

        data = get_json(url, params=params)

        if not data:

            continue

        try:

            quotes = data["finance"]["result"][0]["quotes"]

            for item in quotes:

                symbol = item.get("symbol", "")

                if symbol:

                    found[symbol] = item

        except (KeyError, IndexError, TypeError):

            print(f"Unexpected Yahoo response for {screen}")

    return list(found.values())

def get_company_tickers():

    urls = [

        "https://www.sec.gov/files/company_tickers.json",

        "https://www.sec.gov/files/company_tickers_exchange.json"

    ]

    for url in urls:

        data = get_json(url, HEADERS)

        if not data:

            continue

        mapping = {}

        values = data.values() if isinstance(data, dict) else data

        for item in values:

            if not isinstance(item, dict):

                continue

            ticker = str(item.get("ticker", "")).upper()

            cik = item.get("cik_str") or item.get("cik")

            if ticker and cik:

                mapping[ticker] = str(cik).zfill(10)

        if mapping:

            return mapping

    return {}

def get_sec_clues(ticker, cik_map):

    cik = cik_map.get(ticker.upper())

    if not cik:

        return [], []

    url = f"https://data.sec.gov/submissions/CIK{cik}.json"

    data = get_json(url, HEADERS)

    if not data:

        return [], []

    try:

        recent = data["filings"]["recent"]

        forms = recent.get("form", [])

        dates = recent.get("filingDate", [])

        descriptions = recent.get("primaryDocDescription", [])

    except (KeyError, TypeError):

        return [], []

    china_hits = []

    dilution_hits = []

    cutoff = datetime.now(timezone.utc).date()

    for i, form in enumerate(forms[:30]):

        date_text = dates[i] if i < len(dates) else ""

        try:

            age = (cutoff - datetime.strptime(

                date_text, "%Y-%m-%d"

            ).date()).days

        except ValueError:

            age = 999

        if age > 120:

            continue

        desc = descriptions[i] if i < len(descriptions) else ""

        text = f"{form} {desc}".lower()

        if form in {"S-1", "S-3", "424B3", "424B5", "F-1", "F-3", "8-K"}:

            dilution_hits.append(f"{form} ({date_text})")

        for term in DILUTION_TERMS:

            if term in text:

                dilution_hits.append(f"{term} ({date_text})")

                break

    # SEC filing descriptions alone do not reliably establish company origin.

    # Treat China clues as unconfirmed until company facts are reviewed.

    return china_hits, list(dict.fromkeys(dilution_hits))[:5]

def send_alert(message):

    if not NTFY_TOPIC:

        print("NTFY_TOPIC secret is missing; alert not sent.")

        print(message)

        return False

    url = f"https://ntfy.sh/{NTFY_TOPIC}"

    try:

        response = requests.post(

            url,

            data=message.encode("utf-8"),

            headers={

                "Title": "중국계 미국 소형주 감시",

                "Priority": "default",

                "Tags": "chart_with_upwards_trend"

            },

            timeout=20

        )

        response.raise_for_status()

        return True

    except Exception as exc:

        print(f"ntfy send failed: {exc}")

        return False

def main():

    print("Scanner started:", datetime.now(timezone.utc).isoformat())

    candidates = get_candidates()

    if not candidates:

        print("No candidates returned by Yahoo.")

        return

    cik_map = get_company_tickers()

    alerts = []

    for item in candidates:

        ticker = item.get("symbol", "")

        price = item.get("regularMarketPrice")

        change = item.get("regularMarketChangePercent", 0)

        volume = item.get("regularMarketVolume", 0)

        average = item.get("averageDailyVolume3Month", 0)

        name = item.get("shortName") or item.get("longName") or ticker

        try:

            price = float(price)

            volume = int(volume or 0)

            average = int(average or 0)

            change = float(change or 0)

        except (ValueError, TypeError):

            continue

        if price <= 0 or price > PRICE_MAX:

            continue

        if volume < VOLUME_MIN or average <= 0:

            continue

        multiple = volume / average

        if multiple < VOLUME_MULTIPLE_MIN:

            continue

        china_hits, dilution_hits = get_sec_clues(ticker, cik_map)

        dollar_volume = price * volume

        if dollar_volume < 1_000_000:

            continue

        if multiple >= 5:

            signal = "강한 거래량 급증"

        elif multiple >= 3:

            signal = "거래량 급증"

        else:

            signal = "거래량 증가"

        lines = [

            f"[{signal}] {ticker} - {name}",

            f"가격: ${price:.2f} / 변동률: {change:+.2f}%",

            f"거래량: {volume:,} / 3개월 평균: {average:,}",

            f"평균 대비 거래량: {multiple:.1f}배",

            f"거래대금 추정: ${dollar_volume:,.0f}",

            "중국계 확인: 미확인(회사 소재지 추가 검증 필요)",

            "희석 단서: " + (

                ", ".join(dilution_hits) if dilution_hits else "최근 SEC 제출 목록에서 명확한 단서 없음"

            ),

            "주의: 자동 탐지 신호이며 매수 추천이 아닙니다."

        ]

        alerts.append((multiple, "\n".join(lines)))

    alerts.sort(key=lambda x: x[0], reverse=True)

    for _, message in alerts[:MAX_ALERTS]:

        print(message)

        send_alert(message)

    print(f"Finished. Candidates alerted: {min(len(alerts), MAX_ALERTS)}")

if __name__ == "__main__":

    main()
