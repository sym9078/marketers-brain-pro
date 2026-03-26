import streamlit as st
import google.generativeai as genai
import pandas as pd
import numpy as np
import requests
from bs4 import BeautifulSoup
import re
import json
import urllib.parse
from datetime import datetime
from collections import Counter
import time

# ==========================================
# 1. 클래스 정의: 블로그 진단 & 제안 생성
# ==========================================

class NaverBlogAuditor:
    """네이버 블로그의 건전성을 진단하는 클래스"""
    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Referer': 'https://m.blog.naver.com'
        }
        self.risk_criteria = {
            'FATAL': {'score': 50, 'keywords': ['대출', '이자', '부동산', '분양', '수익률', '가입', '보험']},
            'CAUTION': {'score': 20, 'keywords': ['의사', '진료', '시술', '병원', '의원', '성형', '다이어트약']},
            'COMMERCIAL': {'score': 10, 'keywords': ['소정의 원고료', '제품을 제공', '업체로부터', '제공받아', '협찬', '서비스를 지원']},
            'HYPE': {'score': 3, 'keywords': ['무료', '100%', '프리미엄', '체험', '한정', '최대', '할인', '선착순']}
        }

    def _extract_blog_id(self, url):
        match = re.search(r'blog\.naver\.com/([^/?]+)', url)
        return match.group(1) if match else None

    def _get_post_list(self, blog_id, count=20):
        list_api = f"https://blog.naver.com/PostTitleListAsync.naver?blogId={blog_id}&viewdate=&currentPage=1&categoryNo=&parentCategoryNo=&countPerPage={count}"
        try:
            res = requests.get(list_api, headers=self.headers)
            res.encoding = 'utf-8'
            data = json.loads(res.text.replace('\\', '\\\\'))
            return data.get('postList', [])
        except:
            return []

    def _analyze_frequency(self, posts):
        if not posts: return 0, []
        dates = []
        for post in posts:
            raw_date = urllib.parse.unquote(post['addDate']).replace('.', '').strip() 
            try:
                dt = datetime.strptime(raw_date, "%Y %m %d") 
                dates.append(dt.strftime("%Y-%m-%d"))
            except:
                continue
        
        daily_counts = Counter(dates)
        if not daily_counts: return 0, []

        max_daily = max(daily_counts.values())
        avg_daily = sum(daily_counts.values()) / len(daily_counts)
        
        risk = 0
        details = []
        if max_daily >= 5:
            risk += 50
            details.append(f"🏭 [공장형 의심] 하루 최대 {max_daily}개 포스팅")
        elif max_daily >= 3:
            risk += 20
            details.append(f"⚠️ [과부하] 하루 최대 {max_daily}개 포스팅")
        
        if avg_daily >= 2.5:
            risk += 10
            details.append(f"🤖 [기계적 업로드] 평균 {avg_daily:.1f}포/일")
            
        return risk, details

    def _analyze_content(self, blog_id, log_no):
        view_url = f"https://blog.naver.com/PostView.naver?blogId={blog_id}&logNo={log_no}"
        try:
            res = requests.get(view_url, headers=self.headers)
            soup = BeautifulSoup(res.text, 'html.parser')
            main_container = soup.find('div', {'class': 'se-main-container'}) or soup.find('div', {'id': 'postViewArea'})
            if not main_container: return None
            text = main_container.get_text(strip=True)
            
            k_risk = 0
            found = []
            for level, crit in self.risk_criteria.items():
                for kw in crit['keywords']:
                    if kw in text:
                        k_risk += crit['score']
                        found.append(kw)
                        break 
            return {'risk': k_risk, 'details': list(set(found))}
        except:
            return None

    def audit(self, url):
        blog_id = self._extract_blog_id(url)
        if not blog_id: return {"error": "유효하지 않은 URL입니다. (blog.naver.com 형식이 필요합니다)"}
        
        posts = self._get_post_list(blog_id)
        if not posts: return {"error": "게시글이 없거나 비공개 블로그입니다."}

        total_risk = 0
        details = []

        # 1. 빈도 분석
        f_risk, f_det = self._analyze_frequency(posts)
        total_risk += f_risk
        details.extend(f_det)

        # 2. 내용 분석 (최근 5개 샘플링)
        c_risk_sum = 0
        for post in posts[:5]:
            res = self._analyze_content(blog_id, post['logNo'])
            if res:
                c_risk_sum += res['risk']
                if res['details']:
                    details.append(f"키워드 검출: {', '.join(res['details'])}")
        
        total_risk += (c_risk_sum / 5)
        
        grade = "A"
        if total_risk >= 50: grade = "F"
        elif total_risk >= 20: grade = "C"
        elif total_risk >= 10: grade = "B"

        return {
            "id": blog_id,
            "risk_score": round(total_risk, 1),
            "grade": grade,
            "details": list(set(details))[:5]
        }


class GeminiActionGenerator:
    """진단 결과를 바탕으로 제안서를 작성하는 클래스"""
    def __init__(self, api_key):
        self.api_key = api_key
        if self.api_key:
            try:
                genai.configure(api_key=self.api_key)
                self.model = genai.GenerativeModel('gemini-flash-latest') # 최신 모델 사용
            except:
                self.model = None

    def generate(self, audit_res, info):
        if not self.api_key or not self.model:
            return "⚠️ API 키 오류", "Google API Key를 확인해주세요."

        grade = audit_res['grade']
        blog_id = audit_res['id']
        
        if grade == 'F':
            return "⛔ [경고] 블랙리스트 의심", "이 블로그는 공장형/스팸으로 의심되어 섭외를 진행하지 않는 것이 좋습니다."

        # 프롬프트 설계
        if grade == 'A':
            tone = "진정성 있고, 정중하며, 팬심이 느껴지는 감성적인 톤. 창작의 자유를 존중하는 느낌."
            strategy = "단순 배포가 아닌, 작가님의 고유한 시선이 담긴 콘텐츠를 요청함."
        else:
            tone = "명확하고, 직관적이며, 비즈니스적인 톤. 군더더기 없이 단가와 조건을 제시."
            strategy = "가이드라인 준수가 필수임을 강조하고, 상위노출 가능 여부를 타진함."

        email_prompt = f"""
        당신은 노련한 마케팅 팀장입니다. 블로거에게 보낼 '제안 메일'을 작성하세요.
        [타겟: {blog_id}, 등급: {grade}급]
        [제품: {info['name']}, 카테고리: {info['cat']}, USP: {info['usp']}]
        
        톤앤매너: {tone}
        전략: {strategy}
        USP가 메일 내용에 자연스럽게 녹아들어야 함.
        """

        guide_prompt = f"""
        블로거가 포스팅 작성 시 지켜야 할 '가이드라인'을 작성하세요.
        [제품: {info['name']}, 필수 키워드: {info['kw']}, 필수 미션: {info['mission']}]
        
        등급({grade}급)별 전략:
        - A급: 자율성 부여 (사진 구도 자유 등)
        - B/C급: 엄격한 통제 (사진 15장 이상, 공정위 문구 필수, 타 글 복붙 금지 등)
        """

        try:
            email_res = self.model.generate_content(email_prompt)
            guide_res = self.model.generate_content(guide_prompt)
            return email_res.text, guide_res.text
        except Exception as e:
            return "❌ 생성 실패", f"오류 발생: {str(e)}"


# ==========================================
# 2. Streamlit 메인 UI
# ==========================================

st.set_page_config(page_title="Marketer's Brain Pro", layout="wide", page_icon="🧠")

st.markdown("""
    <style>
    .main { background-color: #FFFFFF; color: #37352F; }
    h1, h2, h3 { font-family: 'Segoe UI', sans-serif; }
    .stButton>button { width: 100%; border-radius: 5px; font-weight: bold;}
    div[data-testid="stMetricValue"] { font-size: 1.5rem; }
    </style>
    """, unsafe_allow_html=True)

# --- 사이드바: 통합 설정 ---
with st.sidebar:
    st.title("🧠 Marketer's Brain")
    st.caption("All-in-One Marketing Tool")
    
    # API 키 입력
    api_key = st.text_input("🔑 Google API Key", type="password", help="Gemini 사용을 위해 필요합니다.")
    
    st.markdown("---")
    st.header("📝 브랜드/제품 정보")
    brand_name = st.text_input("브랜드/제품명", "덴티스테")
    category = st.text_input("카테고리", "생활용품/치약")
    target = st.text_input("핵심 타겟", "30대 직장인")
    usp = st.text_area("USP (핵심 강점)", "밤사이 입냄새 원인을 잡아주는 나이트타임 치약")

    with st.expander("📢 블로그 섭외 설정 (선택)", expanded=False):
        keywords = st.text_input("필수 키워드", "입냄새제거, 구취케어")
        mission = st.text_input("필수 미션", "사용 전후 상쾌함 비교")

    # 정보 딕셔너리 생성 (함수 전달용)
    product_info = {
        "name": brand_name, "cat": category, "usp": usp, 
        "target": target, "kw": keywords, "mission": mission
    }

# --- Gemini 모델 설정 ---
model = None
if api_key:
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-flash-latest')
    except:
        pass

# --- 메인 영역 ---
st.title("🚀 Marketer's Brain: AI + Influencer")
st.markdown("Strategy • Copywriting • **Influencer Audit** • Performance")

if not api_key:
    st.warning("👈 왼쪽 사이드바에 **Google API Key**를 입력하면 시작됩니다.")
    st.stop()

# 탭 구성 (5개)
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "1️⃣ 전략 기획", 
    "2️⃣ 카피라이팅", 
    "3️⃣ 상세페이지", 
    "4️⃣ 블로그 진단(NEW)", 
    "5️⃣ 성과 진단"
])

# [Tab 1] 전략 기획
with tab1:
    st.subheader("🎯 STP 전략 및 캠페인 컨셉")
    if st.button("🤖 AI 전략 생성하기", key="btn_st"):
        if not usp: st.error("USP를 입력해주세요.")
        else:
            with st.spinner("AI가 전략을 수립 중입니다..."):
                prompt = f"마케팅 전략가로서 [제품: {brand_name}, USP: {usp}]의 포지셔닝, 캠페인 메시지(3가지), SWOT 기회 전략을 마크다운으로 작성해줘."
                st.markdown(model.generate_content(prompt).text)

# [Tab 2] 카피라이팅
with tab2:
    st.subheader("✍️ 광고 카피라이팅")
    if st.button("🤖 AI 카피 생성하기", key="btn_cp"):
        with st.spinner("AI가 카피를 작성 중입니다..."):
            prompt = f"카피라이터로서 [제품: {brand_name}, USP: {usp}]의 인스타 피드 광고 본문, 배너 문구 3세트, 검색광고 제목 5개를 작성해줘."
            st.markdown(model.generate_content(prompt).text)

# [Tab 3] 상세페이지
with tab3:
    st.subheader("📄 상세페이지 기획 (PASONA)")
    if st.button("🤖 AI 상세페이지 기획하기", key="btn_ld"):
        with st.spinner("PASONA 구조로 기획 중입니다..."):
            prompt = f"상세페이지 기획자로서 [제품: {brand_name}, USP: {usp}]의 상세페이지를 PASONA 구조(Problem-Agitation-Solution-Offer-Narrowing-Action)로 기획해줘."
            st.markdown(model.generate_content(prompt).text)

# [Tab 4] 블로그 진단 (Integrated from 뒷부분.py)
with tab4:
    st.subheader("🔍 네이버 블로그 공장형 판독 & 섭외")
    st.caption("블로그 URL을 넣으면 '건전성'을 진단하고, 등급에 맞는 '제안서'를 써줍니다.")
    
    col_b1, col_b2 = st.columns([3, 1])
    with col_b1:
        blog_url = st.text_input("분석할 네이버 블로그 URL", placeholder="https://blog.naver.com/아이디")
    with col_b2:
        st.write("") 
        st.write("")
        audit_btn = st.button("🚀 진단 및 제안 생성", type="primary", use_container_width=True)

    if audit_btn and blog_url:
        auditor = NaverBlogAuditor()
        generator = GeminiActionGenerator(api_key)

        with st.spinner("데이터 분석 및 AI 생성 중... (약 5초 소요)"):
            # 1. 진단
            audit_result = auditor.audit(blog_url)
            
            if "error" in audit_result:
                st.error(audit_result['error'])
            else:
                # 2. 결과 출력
                st.markdown("---")
                c1, c2, c3 = st.columns(3)
                grade = audit_result['grade']
                color = "green" if grade == 'A' else ("orange" if grade in ['B', 'C'] else "red")
                
                with c1: st.metric("블로그 ID", audit_result['id'])
                with c2: st.metric("위험 지수", f"{audit_result['risk_score']}점")
                with c3: st.markdown(f"### 등급: :{color}[{grade}급]")

                if audit_result['details']:
                    with st.expander("🚨 감점 요인 (클릭하여 확인)", expanded=True):
                        for det in audit_result['details']: st.warning(f"- {det}")
                else:
                    st.success("✅ 감점 요인 없는 클린 블로그입니다.")

                # 3. AI 제안서 생성
                st.markdown("---")
                st.subheader(f"⚡ {grade}급 맞춤형 제안 (AI 작성)")
                
                email_txt, guide_txt = generator.generate(audit_result, product_info)
                
                if grade == 'F':
                    st.error(email_txt)
                else:
                    sub_tab1, sub_tab2 = st.tabs(["📧 섭외 메일 초안", "📝 가이드라인"])
                    with sub_tab1: st.text_area("메일 복사하기", email_txt, height=400)
                    with sub_tab2: st.markdown(guide_txt)

# [Tab 5] 성과 진단
with tab5:
    st.subheader("🚑 퍼포먼스 성과 진단")
    c1, c2, c3, c4 = st.columns(4)
    imp = c1.number_input("노출수", 10000, step=1000)
    click = c2.number_input("클릭수", 150, step=10)
    cost = c3.number_input("광고비", 100000, step=10000)
    conv = c4.number_input("전환수", 3, step=1)

    if imp > 0 and click > 0:
        ctr = (click / imp) * 100
        cpc = cost / click
        cvr = (conv / click) * 100
        
        st.markdown("---")
        m1, m2, m3 = st.columns(3)
        m1.metric("CTR", f"{ctr:.2f}%")
        m2.metric("CPC", f"{cpc:,.0f}원")
        m3.metric("CVR", f"{cvr:.2f}%")
        
        if ctr < 1.5: st.error("🚨 [진단] CTR 저조 -> Tab 2에서 Hook 메시지 개선 필요")
        elif cvr < 2.0: st.warning("⚠️ [진단] CVR 부족 -> Tab 3에서 상세페이지 공감 보강 필요")
        else: st.success("✅ [진단] 성과 우수 -> 예산 증액 추천")