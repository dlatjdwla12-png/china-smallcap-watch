

import os

import re

import json

import time

import random

from datetime import datetime, timezone

import requests

# ===== 기본 설정 =====

PRICE_MAX = 20.0

VOLUME_MIN = 100_000

VOLUME_MULTIPLE_MIN = 2.0

DOLLAR_VOLUME_MIN = 1_000_000

MAX_ALERTS = 15

NTFY_TOPIC = os.getenv("NTFY_TOPIC", "").strip()

SEC_USER_EMAIL = os.getenv("SEC_USER_EMAIL", "").strip()

YAHOO_URL = "https://query1.finance.yahoo.com/v1/finance/screener"

CHINA_TERMS = [

    "china", "chinese", "hong kong", "macau", "macao",

    "beijing", "shanghai", "shenzhen", "guangzhou",

    "people's republic of china", "variable interest entity",

    "v.i.e.", "cayman", "hongkong"

]

DILUTION_TERMS = [

    "at-the-market", "at the market offering",

    "equity distribution agreement", "sales agreement",

    "shelf registration", "s-1", "s-3", "424b5",

    "registered direct offering", "public offering",

    "private placement", "convertible note",

    "convertible debenture", "warrant",

    "reverse stock split", "dilutive", "dilution",

    "securities purchase agreement", "prospectus supplement",

    "equity line of credit"

]

session = requests.Session()

session.headers.update({

    "User-Agent": "Mozilla/5.0 (compatible; SmallcapResearch/1.0)",

    "Accept": "application/json,text/plain,*/*",

    "Accept-Language": "en-US,en;q=0.9",

})

def get_json(url, params=None, attempts=3):

    """429 등 일시적인 오류는 대기 후 재시도한다."""

    for attempt in range(attempts):

        try:

            response = session.get(

                url, params=params, timeout=20

            )

            if response.status_code == 429:

                wait = min(60, 10 * (attempt + 1))

                print(f"요청 제한(429). {wait}초 대기")

                time.sleep(wait)

                continue

            response.raise_for_status()

            return response.json()

        except (requests.RequestException, ValueError) as exc:

            print(f"요청 실패 ({attempt + 1}/{attempts}): {exc}")

            if attempt < attempts - 1:

                time.sleep(3 * (attempt + 1))

    return None

def yahoo_candidates(scr_id):

    params = {

        "scrIds": scr_id,

        "count": 100,

        "start": 0,

        "formatted": "false",

        "lang": "en-US",

        "region": "US",

    }

    data = get_json(YAHOO_URL, params=params)

    if not data:

        return None

    try:

        quotes = data["finance"]["result"][0]["quotes"]

        return quotes

    except (KeyError, IndexError, TypeError):

        print(f"Yahoo 응답 형식 확인 필요: {scr_id}")

        return None

def get_text(item):

    fields = [

        item.get("shortName"),

        item.get("longName"),

        item.get("displayName"),

        item.get("symbol"),

    ]

    return " ".join(str(x) for x in fields if x).lower()

def company_sec_lookup():

    urls = [

        "https://www.sec.gov/files/company_tickers.json",

        "https://www.sec.gov/files/company_tickers_exchange.json",

    ]

    for url in urls:

        data = get_json(url, attempts=2)

        if data:

            return data

    return None

def sec_company_text(cik):

    if not cik or not SEC_USER_EMAIL:

        return ""

    url = (

        "https://data.sec.gov/submissions/"

        f"CIK{int(cik):010d}.json"

    )

    headers = {

        "User-Agent": f"SmallcapResearch {SEC_USER_EMAIL}",

        "Accept-Encoding": "gzip, deflate",

    }

    try:

        response = session.get(url, headers=headers, timeout=20)

        if response.status_code == 429:

            print("SEC 요청 제한(429)")

            return ""

        response.raise_for_status()

        data = response.json()

        return " ".join([

            str(data.get("name", "")),

            str(data.get("tickers", "")),

            str(data.get("exchanges", "")),

            str(data.get("addresses", "")),

        ]).lower()

    except (requests.RequestException, ValueError) as exc:

        print(f"SEC 조회 실패: {exc}")

        return ""

def send_alert(message):

    if not NTFY_TOPIC:

        print("NTFY_TOPIC 비밀값이 설정되지 않았습니다.")

        return

    url = f"https://ntfy.sh/{NTFY_TOPIC}"

    try:

        response = requests.post(

            url,

            data=message.encode("utf-8"),

            headers={

                "Title": "중국계 미국 소형주 감지",

                "Priority": "high",

                "Tags": "chart_with_upwards_trend",

            },

            timeout=20,

        )

        response.raise_for_status()

        print("ntfy 알림 전송 성공")

    except requests.RequestException as exc:

        print(f"ntfy 전송 실패: {exc}")

def main():

    print("스캐너 시작:", datetime.now(timezone.utc).isoformat())

    all_quotes = {}

    data_errors = []

    # 요청을 연속해서 보내지 않고 간격을 둔다.

    for scr_id in ("small_cap_gainers", "most_actives"):

        quotes = yahoo_candidates(scr_id)

        if quotes is None:

            data_errors.append(scr_id)

        else:

            for item in quotes:

                symbol = item.get("symbol")

                if symbol:

                    all_quotes[symbol] = item

        time.sleep(random.uniform(4, 8))

    if not all_quotes:

        print(

            "데이터 수집 실패: 후보가 없다고 판단하지 않습니다. "

            f"실패한 목록: {data_errors}"

        )

        return

    print(f"고유 종목 수집: {len(all_quotes)}")

    sec_data = company_sec_lookup()

    sec_rows = []

    if isinstance(sec_data, dict):

        sec_rows = list(sec_data.values())

    elif isinstance(sec_data, list):

        sec_rows = sec_data

    # 티커별 CIK 매핑

    cik_by_ticker = {}

    for row in sec_rows:

        if not isinstance(row, dict):

            continue

        ticker = str(row.get("ticker", "")).upper()

        cik = row.get("cik_str") or row.get("cik")

        if ticker and cik:

            cik_by_ticker[ticker] = cik

    alerts = []

    sec_cache = {}

    for symbol, item in all_quotes.items():

        try:

            price = float(item.get("regularMarketPrice") or 0)

            volume = int(item.get("regularMarketVolume") or 0)

            avg_volume = int(

                item.get("averageDailyVolume3Month") or

                item.get("averageDailyVolume10Day") or 0

            )

            change = float(

                item.get("regularMarketChangePercent") or 0

            )

            name = str(

                item.get("longName") or

                item.get("shortName") or symbol

            )

        except (ValueError, TypeError):

            continue

        if price <= 0 or price > PRICE_MAX:

            continue

        if volume < VOLUME_MIN or avg_volume <= 0:

            continue

        multiple = volume / avg_volume

        dollar_volume = price * volume

        if multiple < VOLUME_MULTIPLE_MIN:

            continue

        if dollar_volume < DOLLAR_VOLUME_MIN:

            continue

        # 회사 이름·티커 및 SEC 제출 정보에서 중국 관련 단서 탐색

        description = get_text(item)

        cik = cik_by_ticker.get(symbol.upper())

        if cik:

            if cik not in sec_cache:

                sec_cache[cik] = sec_company_text(cik)

                time.sleep(0.2)

            description += " " + sec_cache[cik]

        china_hits = [

            term for term in CHINA_TERMS if term in description

        ]

        # 중국 관련 단서가 없는 종목은 제외한다.

        if not china_hits:

            continue

        dilution_hits = [

            term for term in DILUTION_TERMS if term in description

        ]

        lines = [

            f"종목: {symbol} ({name})",

            f"가격: ${price:.2f} / 등락률: {change:+.2f}%",

            f"거래량: {volume:,}",

            f"평균 거래량 대비: {multiple:.2f}배",

            f"추정 거래대금: ${dollar_volume:,.0f}",

            "중국계 단서: " + ", ".join(china_hits[:5]),

            "희석 단서: " + (

                ", ".join(dilution_hits[:8])

                if dilution_hits else "이번 검색에서 발견 못함"

            ),

            "주의: 자동 탐지 신호이며 매수 추천이 아닙니다.",

        ]

        alerts.append((multiple, "\n".join(lines)))

    alerts.sort(key=lambda x: x[0], reverse=True)

    for _, message in alerts[:MAX_ALERTS]:

        print("=" * 35)

        print(message)

        send_alert(message)

        time.sleep(1)

    print(

        f"완료: 알림 {min(len(alerts), MAX_ALERTS)}건, "

        f"수집 오류 {len(data_errors)}건"

    )

if __name__ == "__main__":

    main()
