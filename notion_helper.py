import os
import datetime
import requests
import streamlit as st

def get_notion_headers():
    token = st.secrets["notion"]["token"]
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28"
    }

def send_journal_to_notion(ticker, price, yield_val, comment):
    """
    Streamlit에서 입력한 종목 분석 정보를 Notion DB에 페이지로 추가합니다.
    """
    url = "https://api.notion.com/v1/pages"
    headers = get_notion_headers()
    database_id = st.secrets["notion"]["database_id"]

    # 배당률이 0~100 사이의 퍼센트(%)로 들어오면 소수로 변환해 저장 (예: 4.5% -> 0.045)
    # yfinance 배당률이 통상 소수(0.045)로 계산되므로, 들어오는 포맷에 대응
    try:
        f_price = float(price)
    except (TypeError, ValueError):
        f_price = 0.0

    try:
        f_yield = float(yield_val)
        # 만약 1 이상인 값(예: 4.5)이면 소수점 변환 적용 (4.5% -> 0.045)
        if f_yield > 1.0:
            f_yield = f_yield / 100.0
    except (TypeError, ValueError):
        f_yield = 0.0

    payload = {
        "parent": { "database_id": database_id },
        "properties": {
            "티커": {
                "title": [
                    { "text": { "content": ticker } }
                ]
            },
            "날짜": {
                "date": {
                    "start": datetime.date.today().strftime("%Y-%m-%d")
                }
            },
            "진입 주가": { "number": f_price },
            "진입 배당률": { "number": f_yield },
            "판단 근거": {
                "rich_text": [
                    { "text": { "content": comment } }
                ]
            }
        }
    }

    try:
        response = requests.post(url, json=payload, headers=headers)
        return response.status_code in [200, 201]
    except Exception as e:
        st.error(f"Notion API 요청 중 오류 발생: {e}")
        return False

def backup_notion_to_local(target_dir="docs/journals"):
    """
    Notion 데이터베이스에 쌓인 모든 페이지를 로컬 마크다운 파일로 다운로드합니다.
    (페이지네이션 지원)
    """
    os.makedirs(target_dir, exist_ok=True)
    
    database_id = st.secrets["notion"]["database_id"]
    url = f"https://api.notion.com/v1/databases/{database_id}/query"
    headers = get_notion_headers()
    
    has_more = True
    next_cursor = None
    success_count = 0

    while has_more:
        payload = {}
        if next_cursor:
            payload["start_cursor"] = next_cursor
            
        try:
            response = requests.post(url, json=payload, headers=headers)
            if response.status_code != 200:
                raise Exception(f"Notion DB Query 실패 (상태 코드: {response.status_code})")
                
            data = response.json()
            results = data.get("results", [])
            
            for page in results:
                props = page.get("properties", {})
                
                # 티커 추출 (title)
                ticker_prop = props.get("티커", {}).get("title", [])
                ticker = ticker_prop[0].get("text", {}).get("content", "UNKNOWN").strip().upper() if ticker_prop else "UNKNOWN"
                
                # 날짜 추출 (date)
                date_prop = props.get("날짜", {}).get("date", {})
                date_val = date_prop.get("start", datetime.date.today().strftime("%Y-%m-%d")) if date_prop else datetime.date.today().strftime("%Y-%m-%d")
                
                # 주가 및 배당률
                price_val = props.get("진입 주가", {}).get("number", 0.0)
                yield_val = props.get("진입 배당률", {}).get("number", 0.0)
                if price_val is None: price_val = 0.0
                if yield_val is None: yield_val = 0.0
                
                # 투자 판단 근거 (rich_text)
                comment_prop = props.get("판단 근거", {}).get("rich_text", [])
                comment = comment_prop[0].get("text", {}).get("content", "") if comment_prop else ""
                
                # 마크다운 템플릿 생성
                filename = f"{ticker}_{date_val}.md"
                # 파일명에 유효하지 않은 문자 필터링
                filename = "".join(c for c in filename if c.isalnum() or c in ['.', '_', '-']).strip()
                file_path = os.path.join(target_dir, filename)
                
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(f"# {ticker} 투자 저널 ({date_val})\n\n")
                    f.write(f"## 📊 진입 당시 주요 지표\n")
                    f.write(f"* **주가**: ${price_val:,.2f}\n")
                    f.write(f"* **배당수익률**: {yield_val * 100:.2f}%\n\n")
                    f.write(f"## ✍️ 투자 판단 근거 및 코멘트\n")
                    f.write(f"{comment}\n")
                
                success_count += 1
                
            has_more = data.get("has_more", False)
            next_cursor = data.get("next_cursor", None)
            
        except Exception as e:
            st.error(f"백업 중 오류 발생: {e}")
            break
            
    return success_count
