# Trailing Stop-Loss / Rebuy Backtest

한국(.KS/.KQ)·미국 주식/ETF를 대상으로, 트레일링 고점 대비 손절 → 로컬 저점 대비 재매수를 반복하는
심플한 룰 기반 전략을 yfinance 데이터로 백테스트하는 스크립트입니다.

## 로직

1. 진입 시점 가격을 **로컬 고점(peak)**으로 간주하고 보유 상태로 시작
2. 보유 중: 가격이 peak을 갱신하면 peak 갱신. **peak 대비 -stop_pct 이하로 하락하면 손절(매도)**하고
   해당 가격을 로컬 저점(trough)으로 간주, 관망 상태로 전환
3. 관망 중: 가격이 trough를 갱신하면 trough 갱신. **trough 대비 +rebuy_pct 이상 상승하면 재매수**하고
   해당 가격을 새 peak으로 간주, 다시 보유 상태로 전환
4. 2~3 반복

## 설치

```bash
pip install -r requirements.txt
```

## 실행 예시

```bash
# 한국 주식 (삼성전자), 고점대비 -10% 손절 / 저점대비 +5% 재매수
python stop_loss_backtest.py --ticker 005930.KS --start 2020-01-01 --stop-pct 0.10 --rebuy-pct 0.05

# 미국 ETF (SPY)
python stop_loss_backtest.py --ticker SPY --start 2015-01-01 --stop-pct 0.08 --rebuy-pct 0.04
```

### 옵션

| 옵션 | 설명 | 기본값 |
|---|---|---|
| `--ticker` | 종목 코드 (한국: `005930.KS`/`.KQ`, 미국: `AAPL`, `SPY` 등) | 필수 |
| `--start` | 백테스트 시작일 (YYYY-MM-DD) | 필수 |
| `--end` | 종료일 | 오늘 |
| `--stop-pct` | 고점 대비 손절 하락률 | 0.10 |
| `--rebuy-pct` | 저점 대비 재매수 상승률 | 0.05 |
| `--capital` | 초기 투자금 | 10,000,000 |
| `--out` | 결과 차트 저장 경로 | result.png |

## 출력

- 콘솔: Start/End Value, Total Return, CAGR, MDD, Sharpe, Buy&Hold 대비 지표, 매매 횟수
- `trades.csv`: 전체 매매 내역 (날짜, 매수/매도, 가격, 사유)
- `result.png`: 가격+매매신호 차트 / 전략 vs Buy&Hold 자산곡선

## Streamlit 앱 (Buy&Hold vs Stop-Loss/Rebuy 비교)

단일 종목 또는 여러 종목(동일비중 포트폴리오)에 대해 두 전략의 성과를 시각적으로 비교합니다.

```bash
streamlit run app.py
```

- 티커: 쉼표로 여러 개 입력 시 동일비중 포트폴리오로 계산 (예: `005930.KS, AAPL, SPY`)
- 시작일/종료일, 손절%, 재매수%를 사이드바에서 입력 후 **비교 실행**
- 자산곡선 비교 차트, 성과 지표(총수익률/CAGR/MDD/Sharpe), 종목별 매매내역, CSV 다운로드 제공

### Streamlit Community Cloud 배포

1. GitHub에 이 저장소 push
2. https://share.streamlit.io 접속 → New app → 저장소 선택 → Main file path: `app.py` → Deploy

## 참고 / 한계

- 슬리피지·거래세·수수료는 반영되지 않은 순수 로직 검증용입니다.
- 실거래 자동 주문 연동은 포함되어 있지 않습니다 (추후 한국투자증권 KIS API 등 연동 확장 가능).
- 데이터 소스가 yfinance이므로 야후파이낸스 서비스 상태에 의존합니다.
