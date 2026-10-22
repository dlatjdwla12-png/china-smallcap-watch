import os
import re
import requests
from datetime import datetime, timezone
from urllib.parse import quote

TOPIC = os.environ.get('NTFY_TOPIC', '').strip()
EMAIL = os.environ.get('SEC_USER_EMAIL', '').strip()
PRICE_MAX = 20.0
VOLUME_MIN = 100000
RVOL_MIN = 2.0
DOLLAR_INTEREST = 1000000
DOLLAR_STRONG = 5000000
MAX_ALERTS = 15
S = requests.Session()
S.headers.update({'User-Agent': 'ChinaSmallcapWatch/2.0', 'Accept': 'application/json,text/plain,*/*'})
CHINA = ['china', 'chinese', 'hong kong', 'beijing', 'shanghai', 'shenzhen', 'guangzhou', 'variable interest entity', 'mainland china']
DILUTION = ['at-the-market', 'at the market offering', 'equity distribution agreement', 'shelf registration', 'registered direct offering', 'public offering', 'private placement', 'convertible note', 'convertible debenture', 'warrant', 'reverse stock split', 'dilution', 'prospectus supplement', 'equity line of credit', '424b5', '424b3', 'form s-1', 'form s-3']

def num(x):
    try:
        return float(x or 0)
    except (ValueError, TypeError):
        return 0.0

def yahoo_screen(name):
    try:
        r = S.get('https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved', params={'formatted':'false','lang':'en-US','region':'US','scrIds':name,'count':250,'start':0}, timeout=15)
        r.raise_for_status()
        data = r.json().get('finance', {}).get('result', [])
        return data[0].get('quotes', []) if data else []
    except Exception as e:
        print('Yahoo screen error:', name, e)
        return []

def collect_candidates():
    found = {}
    for screen in ('small_cap_gainers', 'most_actives'):
        rows = yahoo_screen(screen)
        print('Yahoo screen', screen, 'rows:', len(rows))
        for q in rows:
            t = str(q.get('symbol', '')).upper().strip()
            p = num(q.get('regularMarketPrice'))
            v = num(q.get('regularMarketVolume'))
            av = num(q.get('averageDailyVolume10Day') or q.get('averageDailyVolume3Month'))
            if not re.fullmatch(r'[A-Z][A-Z0-9.\\-]{0,9}', t) or p <= 0 or p > PRICE_MAX or v < VOLUME_MIN or av <= 0:
                continue
            rvol = v / av
            if rvol < RVOL_MIN:
                continue
            item = {'ticker':t, 'name':q.get('shortName') or q.get('longName') or t, 'price':p, 'volume':int(v), 'average':int(av), 'rvol':rvol, 'dollar':p*v, 'change':num(q.get('regularMarketChangePercent'))}
            if t not in found or rvol > found[t]['rvol']:
                found[t] = item
    return sorted(found.values(), key=lambda x:(x['rvol'], x['dollar']), reverse=True)

def sec_headers():
    return {'User-Agent':'ChinaSmallcapWatch/2.0 (' + (EMAIL or 'contact@example.com') + ')', 'Accept-Encoding':'gzip, deflate'}

def load_ciks():
    urls = ['https://www.sec.gov/files/company_tickers.json', 'https://raw.githubusercontent.com/jadchaar/sec-cik-mapper/main/mappings/stocks/ticker_to_cik.json']
    for url in urls:
        try:
            r = S.get(url, headers=sec_headers(), timeout=15)
            r.raise_for_status()
            data = r.json()
            mapping = {}
            for k, v in data.items():
                ticker = str(v.get('ticker', k) if isinstance(v, dict) else k).upper().strip()
                cik = (v.get('cik_str') or v.get('cik')) if isinstance(v, dict) else v
                match = re.search(r'\\d+', str(cik)) if cik is not None else None
                if match and re.fullmatch(r'[A-Z][A-Z0-9.\\-]{0,9}', ticker):
                    mapping[ticker] = match.group().zfill(10)
            if mapping:
                print('CIK mapping loaded:', len(mapping))
                return mapping
        except Exception as e:
            print('CIK mapping error:', e)
    return {}

def sec_check(ticker, mapping):
    result = {'china':'확인 불가', 'dilution':'확인 불가', 'status':'공시 조회 실패', 'url':'https://www.sec.gov/edgar/search/#/q=' + quote(ticker)}
    cik = mapping.get(ticker)
    if not cik:
        result['status'] = 'CIK 매핑 없음'
        return result
    try:
        r = S.get('https://data.sec.gov/submissions/CIK' + cik + '.json', headers=sec_headers(), timeout=12)
        if not r.ok:
            result['status'] = 'SEC 접근 실패 (' + str(r.status_code) + ')'
            return result
        data = r.json()
        result['url'] = 'https://www.sec.gov/edgar/browse/?CIK=' + cik
        recent = data.get('filings', {}).get('recent', {})
        forms = recent.get('form', [])
        docs = recent.get('primaryDocument', [])
        accessions = recent.get('accessionNumber', [])
        dates = recent.get('filingDate', [])
        china_hits, dilution_hits, checked = set(), set(), 0
        allowed = {'10-K','10-Q','8-K','S-1','S-3','424B3','424B5','6-K','20-F','F-1','F-3'}
        for i, form in enumerate(forms):
            if form not in allowed or i >= len(docs) or i >= len(accessions):
                continue
            accession = accessions[i]
            base = 'https://www.sec.gov/Archives/edgar/data/' + str(int(cik)) + '/' + accession.replace('-', '') + '/'
            urls = [base + docs[i], base + accession.replace('-', '') + '.txt']
            body = ''
            for url in urls:
                try:
                    doc = S.get(url, headers=sec_headers(), timeout=10)
                    if doc.ok and doc.text.strip():
                        body = doc.text
                        print(ticker, 'SEC filing', dates[i] if i < len(dates) else '', url)
                        break
                except Exception as e:
                    print('SEC document error:', e)
            if not body:
                continue
            body = re.sub(r'<[^>]+>', ' ', body).lower()
            checked += 1
            china_hits.update(term for term in CHINA if term in body)
            dilution_hits.update(term for term in DILUTION if term in body)
            if checked >= 3:
                break
        if not checked:
            result['status'] = '공시 본문 확인 실패'
            return result
        result['status'] = '공시 본문 ' + str(checked) + '건 검색'
        result['china'] = ('키워드 발견(추가 검증 필요): ' + ', '.join(sorted(china_hits)[:5])) if china_hits else '검색한 공시에서 관련 키워드 미발견'
        result['dilution'] = ('희석 키워드 발견(추가 검증 필요): ' + ', '.join(sorted(dilution_hits)[:6])) if dilution_hits else '검색한 공시에서 관련 키워드 미발견'
    except Exception as e:
        print('SEC check error:', ticker, e)
        result['status'] = 'SEC 조회 오류'
    return result

def signal(x):
    if x['rvol'] >= 5 and x['change'] >= 3 and x['dollar'] >= DOLLAR_STRONG:
        return '강한 움직임 감지 — 추격 위험 확인'
    if x['rvol'] >= 3 and x['dollar'] >= DOLLAR_INTEREST and x['change'] >= 0:
        return '폭등 전 후보 — 집중 관찰'
    if x['rvol'] >= 2 and x['dollar'] >= DOLLAR_INTEREST:
        return '조기 감지 — 관심 종목'
    return '거래량 증가 — 추가 확인'

def main():
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    print('Scan started:', now)
    items = collect_candidates()
    print('Market candidates:', len(items))
    if not items:
        print('No candidates; no notification sent.')
        return
    mapping = load_ciks()
    lines = ['미국 소형주 조기 감지', '스캔 시각: ' + now, '거래량: 평균 대비 2배 이상 · 거래대금 관심 $1M / 강한 신호 $5M', '주의: Yahoo 스크리너 후보만 감시하므로 전체 시장을 빠짐없이 포착하지 못합니다.', '평균 거래량 배수는 일일 누적 기준이며 시간대 보정 RVOL이 아닙니다.', '중국 키워드는 중국계 확정 증거가 아니며, 공시 접근 실패는 확인 불가입니다.', '']
    for x in items[:MAX_ALERTS]:
        sec = sec_check(x['ticker'], mapping)
        lines += ['━━ ' + signal(x) + ' ━━', '[' + x['ticker'] + '] ' + str(x['name']), '현재가 $%.2f | 등락률 %+.1f%%' % (x['price'], x['change']), '거래량 %s | 평균 %s | 평균 대비 %.1f배' % (format(x['volume'], ','), format(x['average'], ','), x['rvol']), '거래대금 약 $' + format(x['dollar'], ',.0f'), '중국 관련성: ' + sec['china'], '희석 위험: ' + sec['dilution'], '공시 상태: ' + sec['status'], 'SEC: ' + sec['url'], '차트: https://finance.yahoo.com/quote/' + x['ticker'] + '/', '']
    if not TOPIC:
        raise RuntimeError('NTFY_TOPIC secret missing')
    response = requests.post('https://ntfy.sh/' + TOPIC, data='\\n'.join(lines).encode('utf-8'), headers={'Title':'China Smallcap Watch', 'Priority':'high', 'Tags':'chart_with_upwards_trend,mag', 'Content-Type':'text/plain; charset=utf-8'}, timeout=15)
    print('ntfy response:', response.status_code)
    response.raise_for_status()
    print('Notification sent successfully')

if __name__ == '__main__':
    main()
