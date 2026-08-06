# 포트폴리오 백테스트 (Streamlit)

## 로컬 실행
```bash
pip install -r requirements.txt
streamlit run app.py
```
브라우저에서 `http://localhost:8501` 자동으로 열립니다.

## 웹 주소로 배포 (무료, Streamlit Community Cloud)
1. GitHub에 새 저장소를 만들고 `app.py`, `backtest_engine.py`, `requirements.txt` 3개 파일을 업로드합니다.
2. https://share.streamlit.io 접속 → GitHub 계정으로 로그인.
3. "New app" → 방금 만든 저장소 선택 → Main file path에 `app.py` 입력 → Deploy.
4. 몇 분 후 `https://[앱이름].streamlit.app` 형태의 고유 URL이 생성됩니다.
   이후에는 이 주소로 들어가서 파라미터를 입력하고 "백테스트 실행" 버튼만 누르면 됩니다.

## 파일 구성
- `app.py`: 입력 화면 + 결과 시각화 (Streamlit UI)
- `backtest_engine.py`: 데이터 다운로드, 백테스트 시뮬레이션, 성과지표 계산 로직
- `requirements.txt`: 의존 패키지 목록
