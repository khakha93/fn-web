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

def get_active_position(ticker):
    """
    포지션 DB에서 해당 티커의 '상태'가 '진입중'인 포지션 페이지의 ID를 찾아 반환합니다.
    존재하지 않으면 None을 반환합니다.
    """
    database_id = st.secrets["notion"]["database_id"]
    url = f"https://api.notion.com/v1/databases/{database_id}/query"
    headers = get_notion_headers()
    
    payload = {
        "filter": {
            "and": [
                {
                    "property": "티커",
                    "title": {
                        "equals": ticker.strip().upper()
                    }
                },
                {
                    "property": "상태",
                    "select": {
                        "equals": "진입중"
                    }
                }
            ]
        }
    }
    
    try:
        response = requests.post(url, json=payload, headers=headers)
        if response.status_code == 200:
            results = response.json().get("results", [])
            if results:
                return results[0]["id"]
        return None
    except Exception as e:
        st.warning(f"Notion 액티브 포지션 조회 실패: {e}")
        return None

def create_position_journal(ticker, price, comment):
    """
    최초 진입 시 노션 DB에 새로운 저널(포지션) 페이지를 생성하고 본문 뼈대를 만듭니다.
    """
    database_id = st.secrets["notion"]["database_id"]
    url = "https://api.notion.com/v1/pages"
    headers = get_notion_headers()
    
    date_str = datetime.date.today().strftime("%Y-%m-%d")
    journal_title = f"{ticker.strip().upper()} ({datetime.date.today().strftime('%y.%m')})"
    
    # 1차 시도: 기획안에 정의된 모든 속성을 담아 보냄
    payload = {
        "parent": { "database_id": database_id },
        "properties": {
            "티커": { "title": [ { "text": { "content": journal_title } } ] },
            "날짜": { "date": { "start": date_str } },
            "진입 주가": { "number": float(price) },
            "상태": { "select": { "name": "진입중" } }
        },
        "children": [
            {
                "object": "block",
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [ { "type": "text", "text": { "content": "📌 1. 최초 진입 당시 분석" } } ]
                }
            },
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [ { "type": "text", "text": { "content": comment if comment else "별도의 판단 근거 미입력" } } ]
                }
            },
            {
                "object": "block",
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [ { "type": "text", "text": { "content": "⛓️ 2. 매매 내역 및 시점별 판단 근거" } } ]
                }
            }
        ]
    }
    
    try:
        response = requests.post(url, json=payload, headers=headers)
        
        # 만약 400 에러(사용자가 데이터베이스에 '상태'나 '진입 주가' 컬럼을 만들어두지 않음)가 발생할 경우
        # 최소한의 기본 필드(티커, 날짜)만 포함하여 재시도 (Fallback)
        if response.status_code == 400:
            fallback_payload = {
                "parent": { "database_id": database_id },
                "properties": {
                    "티커": { "title": [ { "text": { "content": journal_title } } ] },
                    "날짜": { "date": { "start": date_str } }
                },
                "children": payload["children"]
            }
            response = requests.post(url, json=fallback_payload, headers=headers)
            
        if response.status_code in [200, 201]:
            return response.json().get("id")
        else:
            st.warning(f"Notion 저널 생성 실패: {response.text}")
            return None
    except Exception as e:
        st.error(f"Notion API 요청 중 오류 발생: {e}")
        return None

def add_order_to_journal(page_id, action_type, shares, price, comment):
    """
    페이지 본문에 매수/매도 개별 거래 내역 텍스트를 추가합니다.
    """
    url = f"https://api.notion.com/v1/blocks/{page_id}/children"
    headers = get_notion_headers()
    
    now_str = datetime.datetime.now().strftime("%m/%d %H:%M")
    action_text = "매수" if action_type.upper() == "LONG" or "BUY" in action_type.upper() else "매도"
    
    bullet_content = f"📅 {now_str}: [{action_text}] {shares}주 (@${price:,.2f})"
    comment_content = f"\n  └ 💡 근거: {comment}" if comment else "\n  └ 💡 근거: 미작성"
    
    payload = {
        "children": [
            {
                "object": "block",
                "type": "bulleted_list_item",
                "bulleted_list_item": {
                    "rich_text": [
                        {
                            "type": "text",
                            "text": { "content": bullet_content }
                        },
                        {
                            "type": "text",
                            "text": { "content": comment_content },
                            "annotations": { "italic": True, "color": "gray" }
                        }
                    ]
                }
            }
        ]
    }
    
    try:
        response = requests.patch(url, json=payload, headers=headers)
        return response.status_code in [200, 201]
    except Exception as e:
        st.warning(f"Notion 주문 내역 추가 실패: {e}")
        return False

def update_position_properties(page_id, avg_price=None, shares=None, status="진입중", return_rate=None, return_val=None, market_regime=None, emotion=None, adherence=None):
    """
    포지션 상태와 최종 성적(수익률, 손익) 등의 메타데이터 속성들을 업데이트합니다.
    없는 속성 필드로 인한 API 에러가 나면 Safe Fallback 처리합니다.
    """
    url = f"https://api.notion.com/v1/pages/{page_id}"
    headers = get_notion_headers()
    
    properties = {}
    
    # 1. 상태 업데이트
    properties["상태"] = { "select": { "name": status } }
    
    # 2. 평단가 및 보유 수량 업데이트 (존재 시)
    if avg_price is not None:
        properties["진입 주가"] = { "number": float(avg_price) }
    
    # 3. 실현 손익 및 최종 수익률 (완청 시에만 전달)
    if return_rate is not None:
        properties["최종 수익률"] = { "number": float(return_rate) }
    if return_val is not None:
        properties["실현 손익"] = { "number": float(return_val) }
        
    # 4. 사용자 정성 분석 메타데이터 필드 (존재하고 속성이 설정되어 있을 때만 전송)
    if market_regime:
        properties["진입 당시 시장 환경"] = { "select": { "name": market_regime } }
    if adherence:
        properties["원칙 준수 여부"] = { "select": { "name": adherence } }
    if emotion:
        # 다중 선택(Multi-select)
        properties["심리 상태"] = { "multi_select": [{"name": emo.strip()} for emo in emotion] }

    payload = { "properties": properties }
    
    try:
        response = requests.patch(url, json=payload, headers=headers)
        
        # 만약 400 에러 발생 시, 없는 속성(예: '최종 수익률', '원칙 준수 여부' 등)이 원인일 수 있으므로
        # 가장 기초적이고 범용적인 '상태'와 '진입 주가'만 업데이트 시도 (Fallback)
        if response.status_code == 400:
            safe_properties = {
                "상태": { "select": { "name": status } }
            }
            if avg_price is not None:
                safe_properties["진입 주가"] = { "number": float(avg_price) }
            
            fallback_payload = { "properties": safe_properties }
            response = requests.patch(url, json=fallback_payload, headers=headers)
            
        return response.status_code in [200, 201]
    except Exception as e:
        st.warning(f"Notion 속성 업데이트 실패: {e}")
        return False

def close_position_journal(page_id, return_rate, return_val, feedback, adherence=None):
    """
    청산 완료 시 최종 피드백 본문 텍스트를 노션에 추가하고 상태를 청산완료로 닫습니다.
    """
    # 1. 속성 정보 청산 마감 처리
    update_position_properties(page_id, status="청산완료", return_rate=return_rate, return_val=return_val, adherence=adherence)
    
    # 2. 본문에 최종 피드백 및 복기내용 추가
    url = f"https://api.notion.com/v1/blocks/{page_id}/children"
    headers = get_notion_headers()
    
    payload = {
        "children": [
            {
                "object": "block",
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [ { "type": "text", "text": { "content": "🏁 3. 최종 청산 복기 및 피드백" } } ]
                }
            },
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [ { "type": "text", "text": { "content": feedback if feedback else "청산 피드백이 기록되지 않았습니다." } } ]
                }
            }
        ]
    }
    
    try:
        response = requests.patch(url, json=payload, headers=headers)
        return response.status_code in [200, 201]
    except Exception as e:
        st.warning(f"Notion 최종 피드백 본문 추가 실패: {e}")
        return False

def backup_notion_to_local(target_dir="docs/journals"):
    """
    기존 호환성 유지용 백업 기능
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
                
                # 주가
                price_val = props.get("진입 주가", {}).get("number", 0.0)
                if price_val is None: price_val = 0.0
                
                # 투자 판단 근거는 속성이 없을 수도 있으니 방어 처리
                comment = ""
                comment_prop = props.get("판단 근거", {}).get("rich_text", [])
                if comment_prop:
                    comment = comment_prop[0].get("text", {}).get("content", "")
                
                # 마크다운 템플릿 생성
                filename = f"{ticker}_{date_val}.md"
                filename = "".join(c for c in filename if c.isalnum() or c in ['.', '_', '-']).strip()
                file_path = os.path.join(target_dir, filename)
                
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(f"# {ticker} 투자 저널 ({date_val})\n\n")
                    f.write(f"## 📊 진입 당시 주요 지표\n")
                    f.write(f"* **주가**: ${price_val:,.2f}\n\n")
                    f.write(f"## ✍️ 투자 판단 근거 및 코멘트\n")
                    f.write(f"{comment}\n")
                
                success_count += 1
                
            has_more = data.get("has_more", False)
            next_cursor = data.get("next_cursor", None)
            
        except Exception as e:
            st.error(f"백업 중 오류 발생: {e}")
            break
            
    return success_count
