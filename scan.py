import os

import re

import json

import time

import datetime

import requests

from urllib.parse import quote

# =========================

# 설정

# =========================

PRICE_MAX = 20.0

VOLUME_MIN = 100_000

VOLUME_MULTIPLE_MIN = 2.0

DOLLAR_VOLUME_MIN = 1_000_000

MAX_ALERTS = 15

NTFY_TOPIC = os.getenv("NTFY_TOPIC", "").strip()

SEC_USER_EMAIL = os.getenv(

    "SEC_USER_EMAIL", "scanner@example.com"

).strip()

SESSION = requests.Session()

SESSION.headers.update({

    "User-Agent": f"ChinaSmallcapWatch {SEC_USER_EMAIL}",

    "Accept": "application/json,text/plain,*/*",

    "Accept-Language": "en-US,en;q=0.9",

})

# 중국 본토·홍콩·마카오 관련 단서

CHINA_TERMS = [

    "china", "chinese", "hong kong", "macau", "macao",

    "beijing", "shanghai", "shenzhen", "guangzhou",

    "people's republic of china", "variable interest entity",

    "cayman", "hongkong"

]

# 희석 및 자금 조달 위험 단서

DILUTION_TERMS = [

    "at-the-market", "at the market offering",

    "equity distribution agreement", "sales agreement",

    "shelf registration", "form s-1", "form s-3",

    "424b5", "registered direct offering",

    "public offering", "private placement",

    "convertible note", "convertible debenture",

    "warrant", "securities purchase agreement",

    "equity line of credit", "reverse stock split",

    "dilutive", "prospectus supplement"

]

# 거래량·가격 조건을 만족하는 종목을 찾을 때 사용할 거래소

EXCHANGES = ["nasdaq", "nyse", "amex"]

def get_json(url, params=None, retries=2):

    """일시적인 네트워크 오류에 대비해 재시도한다."""

    for attempt in range(retries + 1):

        try:

            response = SESSION.get(

                url, params=params, timeout=20

            )

            response.raise_for_status()

            return response.json()

        except Exception as exc:

            print(

                f"요청 실패 ({attempt + 1}/{retries + 1}): "

                f"{url} - {exc}"

            )

            if attempt < retries:

                time.sleep(2 + attempt)

    return None

def get_nasdaq_candidates(exchange):

    """

    Nasdaq 공개 스크리너에서 가격·거래량 정보를 가져온다.

    데이터 제공처의 제한으로 실패할 수 있다.

    """

    url = "https://api.nasdaq.com/api/screener/stocks"

    params = {

        "tableonly": "true",

        "limit": "5000",

        "offset": "0",

        "exchange": exchange,

    }

    headers = {

        "User-Agent": (

            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "

            "AppleWebKit/605.1.15 Version/17.0 Mobile Safari/604.1"

        ),

        "Accept": "application/json, text/plain, */*",

        "Origin": "https://www.nasdaq.com",

        "Referer": "https://www.nasdaq.com/",

    }

    try:

        response = SESSION.get(

            url, params=params, headers=headers, timeout=25

        )

        response.raise_for_status()

        payload = response.json()

        rows = payload.get("data", {}).get("table", {}).get("rows", [])

        if not isinstance(rows, list):

            return []

        return rows

    except Exception as exc:

        print(f"{exchange} 거래소 데이터 수집 실패: {exc}")

        return []

def number(value):

    """'$1.25', '1,234,567', '12.5%' 등의 문자열을 숫자로 변환한다."""

    if value is None:

        return None

    cleaned = str(value).replace(",", "").replace("$", "")

    cleaned = cleaned.replace("%", "").strip()

    if cleaned in ("", "N/A", "--", "NA"):

        return None

    try:

        return float(cleaned)

    except (ValueError, TypeError):

        return None

def get_row_value(row, *keys):

    """거래소 응답의 필드명 차이에 대응한다."""

    lowered = {str(k).lower(): v for k, v in row.items()}

    for key in keys:

        value = lowered.get(key.lower())

        if value not in (None, ""):

            return value

    return None

def collect_market_candidates():

    candidates = {}

    for exchange in EXCHANGES:

        rows = get_nasdaq_candidates(exchange)

        print(f"{exchange}: 스크리너에서 {len(rows)}개 행 수신")

        for row in rows:

            symbol = get_row_value(row, "symbol", "ticker")

            if not symbol:

                continue

            symbol = str(symbol).strip().upper()

            # 펀드·옵션 등에서 생길 수 있는 기호를 일부 제외한다.

            if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", symbol):

                continue

            price = number(

                get_row_value(row, "lastsale", "last sale", "price")

            )

            volume = number(

                get_row_value(row, "volume", "share volume")

            )

            change_pct = number(

                get_row_value(row, "pctchange", "percent change")

            )

            market_cap = number(

                get_row_value(row, "marketCap", "market cap")

            )

            if price is None or volume is None:

                continue

            if price <= 0 or price > PRICE_MAX:

                continue

            if volume < VOLUME_MIN:

                continue

            candidates[symbol] = {

                "symbol": symbol,

                "price": price,

                "volume": int(volume),

                "change_pct": change_pct,

                "market_cap": market_cap,

                "exchange": exchange.upper(),

            }

    return list(candidates.values())

def get_sec_ticker_map():

    """SEC의 공개 티커 목록에서 종목명과 CIK를 가져온다."""

    urls = [

        "https://www.sec.gov/files/company_tickers.json",

        "https://www.sec.gov/files/company_tickers_exchange.json",

    ]

    for url in urls:

        data = get_json(url)

        if not data:

            continue

        result = {}

        rows = data.values() if isinstance(data, dict) else data

        for item in rows:

            if not isinstance(item, dict):

                continue

            ticker = str(item.get("ticker", "")).upper().strip()

            cik = item.get("cik_str", item.get("cik"))

            title = item.get("title", item.get("name", ""))

            if ticker and cik:

                try:

                    result[ticker] = {

                        "cik": int(cik),

                        "title": str(title),

                    }

                except (ValueError, TypeError):

                    pass

        if result:

            print(f"SEC 티커 목록: {len(result)}개")

            return result

    print("SEC 티커 목록 수집 실패")

    return {}

def get_company_submissions(cik):

    """SEC 회사 제출 기록을 가져온다."""

    url = (

        "https://data.sec.gov/submissions/"

        f"CIK{int(cik):010d}.json"

    )

    return get_json(url)

def inspect_sec(cik):

    """

    최근 SEC 제출 기록의 회사명·주소·최근 제출 서식에서

    중국 관련 단서와 희석 위험 단서를 확인한다.

    이것은 공시 원문 전체를 검색하는 기능이 아니므로

    위험 단서가 누락될 수 있다.

    """

    data = get_company_submissions(cik)

    if not data:

        return [], []

    company_info = [

        str(data.get("name", "")),

        str(data.get("businessAddress", "")),

        str(data.get("mailingAddress", "")),

    ]

    for key in ("businessAddress", "mailingAddress"):

        address = data.get(key)

        if isinstance(address, dict):

            company_info.append(" ".join(

                str(value) for value in address.values()

            ))

    company_text = " ".join(company_info).lower()

    china_hits = [

        term for term in CHINA_TERMS if term in company_text

    ]

    recent = data.get("filings", {}).get("recent", {})

    forms = recent.get("form", [])

    dates = recent.get("filingDate", [])

    descriptions = recent.get("primaryDocDescription", [])

    files = recent.get("primaryDocument", [])

    dilution_hits = []

    for index, form in enumerate(forms[:40]):

        form_text = str(form).lower()

        date = dates[index] if index < len(dates) else ""

        desc = (

            descriptions[index]

            if index < len(descriptions) else ""

        )

        doc = files[index] if index < len(files) else ""

        text = f"{form_text} {desc} {doc}".lower()

        if any(term in text for term in DILUTION_TERMS):

            dilution_hits.append(

                f"{form} {date} {desc}".strip()

            )

    return china_hits, dilution_hits[:5]

def get_chart_volume_history(symbol):

    """

    Yahoo 차트 API에서 최근 일봉 거래량을 가져온다.

    이전 스크리너 API와 다른 엔드포인트를 사용한다.

    """

    url = (

        "https://query1.finance.yahoo.com/v8/finance/chart/"

        f"{quote(symbol, safe='')}"

    )

    params = {

        "range": "1mo",

        "interval": "1d",

        "includePrePost": "true",

    }

    data = get_json(url, params=params)

    if not data:

        return None

    try:

        result = data["chart"]["result"][0]

        quote_data = result["indicators"]["quote"][0]

        volumes = [

            int(v) for v in quote_data.get("volume", [])

            if v is not None

        ]

        prices = [

            float(v) for v in quote_data.get("close", [])

            if v is not None

        ]

        if not volumes:

            return None

        # 가장 최근 일봉은 아직 진행 중일 수 있으므로

        # 가능한 경우 직전 거래일의 거래량 평균을 사용한다.

        historical = volumes[:-1] if len(volumes) > 1 else volumes

        avg_volume = (

            sum(historical[-20:]) / len(historical[-20:])

            if historical else 0

        )

        return {

            "avg_volume": avg_volume,

            "recent_close": prices[-1] if prices else None,

        }

    except (KeyError, IndexError, TypeError, ValueError):

        return None

def send_alert(message):

    if not NTFY_TOPIC:

        print("NTFY_TOPIC이 설정되지 않아 푸시를 보내지 않았습니다.")

        return False

    url = f"https://ntfy.sh/{quote(NTFY_TOPIC, safe='')}"

    try:

        response = requests.post(

            url,

            data=message.encode("utf-8"),

            headers={

                "Title": "중국계 미국 소형주 감시",

                "Priority": "high",

                "Tags": "chart_with_upwards_trend",

            },

            timeout=15,

        )

        response.raise_for_status()

        print("ntfy 알림 전송 성공")

        return True

    except Exception as exc:

        print(f"ntfy 알림 전송 실패: {exc}")

        return False

def main():

    started = datetime.datetime.now(

        datetime.timezone.utc

    ).isoformat()

    print(f"스캐너 시작: {started}")

    market_candidates = collect_market_candidates()

    print(f"시장 후보 수: {len(market_candidates)}")

    if not market_candidates:

        print(

            "후보가 없습니다. 데이터 제공처 제한 또는 "

            "시장 데이터 수집 실패일 수 있습니다."

        )

        return

    sec_map = get_sec_ticker_map()

    alerts = []

    for item in market_candidates:

        symbol = item["symbol"]

        sec_item = sec_map.get(symbol)

        if not sec_item:

            # SEC 티커 목록에 없는 종목은 중국계 검증 불가

            continue

        china_hits, dilution_hits = inspect_sec(sec_item["cik"])

        # SEC 회사 정보에서 중국 관련 단서가 확인된 후보만 알림

        if not china_hits:

            continue

        history = get_chart_volume_history(symbol)

        avg_volume = (

            history["avg_volume"]

            if history else 0

        )

        multiple = (

            item["volume"] / avg_volume

            if avg_volume > 0 else 0

        )

        dollar_volume = item["price"] * item["volume"]

        # 거래량 배수 데이터가 없으면 강한 거래량 신호로 간주하지 않는다.

        if multiple < VOLUME_MULTIPLE_MIN:

            continue

        if dollar_volume < DOLLAR_VOLUME_MIN:

            continue

        change = item["change_pct"]

        change_text = (

            f"{change:+.2f}%"

            if change is not None else "확인 불가"

        )

        lines = [

            f"종목: {symbol} ({sec_item['title']})",

            f"거래소: {item['exchange']}",

            f"현재가: ${item['price']:.2f}",

            f"등락률: {change_text}",

            f"거래량: {item['volume']:,}",

            f"평균 거래량 추정: {avg_volume:,.0f}",

            f"거래량 배수: {multiple:.2f}배",

            f"거래대금 추정: ${dollar_volume:,.0f}",

            f"중국 관련 단서: {', '.join(china_hits)}",

            "희석 단서: " + (

                "; ".join(dilution_hits)

                if dilution_hits else "최근 제출 서식에서 뚜렷한 단서 없음"

            ),

            "주의: 자동 탐지 신호이며 매수 추천이 아닙니다.",

        ]

        alerts.append((multiple, "\n".join(lines)))

        time.sleep(0.2)

    alerts.sort(key=lambda x: x[0], reverse=True)

    print(f"알림 조건을 통과한 후보: {len(alerts)}")

    for _, message in alerts[:MAX_ALERTS]:

        print("\n" + message + "\n")

        send_alert(message)

        time.sleep(0.5)

    print("스캔 종료")

if __name__ == "__main__":

    main()
