"""
Streamlit App: Buy&Hold vs Trailing Stop-Loss/Rebuy 전략 비교
=============================================================

단일 종목 또는 여러 종목(동일비중 포트폴리오)에 대해
1) Buy & Hold
2) Trailing Stop-Loss / Rebuy (로컬 고점 대비 손절 -> 로컬 저점 대비 재매수)
두 전략의 기간별 수익률을 비교한다.

실행: streamlit run app.py
"""

from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from stop_loss_backtest import (
    compute_metrics,
    fetch_prices,
    run_trailing_stop_backtest,
)

st.set_page_config(page_title="Stop-Loss vs Buy&Hold 비교", layout="wide")

st.title("📉 Trailing Stop-Loss/Rebuy vs Buy&Hold 비교")
st.caption("로컬 고점 대비 -N% 하락 시 손절 → 로컬 저점 대비 +M% 상승 시 재매수. "
           "초기 진입가는 로컬 고점으로 간주합니다. 가격 기준: 일별 종가(수정주가, yfinance)")

# ----------------------------------------------------------------
# 사이드바 입력
# ----------------------------------------------------------------
with st.sidebar:
    st.header("설정")

    tickers_input = st.text_input(
        "티커 (쉼표로 구분, 여러 개 입력시 동일비중 포트폴리오)",
        value="005930.KS",
        help="한국: 005930.KS(코스피)/091160.KQ(코스닥) | 미국: AAPL, SPY 등",
    )

    col1, col2 = st.columns(2)
    with col1:
        start_date = st.date_input("시작일", value=date.today() - timedelta(days=365 * 3))
    with col2:
        end_date = st.date_input("종료일 (오늘까지 하려면 오늘 날짜)", value=date.today())

    stop_pct = st.number_input("손절 % (고점 대비 하락률)", min_value=0.1, max_value=90.0,
                                value=10.0, step=0.5) / 100
    rebuy_pct = st.number_input("재매수 % (저점 대비 상승률)", min_value=0.1, max_value=200.0,
                                 value=5.0, step=0.5) / 100
    capital = st.number_input("초기 투자금 (총액)", min_value=0, value=10_000_000, step=1_000_000)

    run_btn = st.button("▶ 비교 실행", type="primary", use_container_width=True)

# ----------------------------------------------------------------
# 실행
# ----------------------------------------------------------------
if run_btn:
    tickers = [t.strip().upper() for t in tickers_input.split(",") if t.strip()]
    if not tickers:
        st.error("티커를 입력하세요.")
        st.stop()
    if start_date >= end_date:
        st.error("시작일은 종료일보다 빨라야 합니다.")
        st.stop()

    weight_capital = capital / len(tickers)

    strategy_curves, buyhold_curves = {}, {}
    trades_by_ticker = {}
    errors = []

    with st.spinner("데이터 조회 및 백테스트 실행 중..."):
        for tk in tickers:
            try:
                prices = fetch_prices(tk, str(start_date), str(end_date))
                result = run_trailing_stop_backtest(prices, stop_pct, rebuy_pct, weight_capital)
                buy_hold = prices / prices.iloc[0] * weight_capital

                strategy_curves[tk] = result.equity_curve
                buyhold_curves[tk] = buy_hold
                trades_by_ticker[tk] = result.trades_df()
            except Exception as e:
                errors.append(f"{tk}: {e}")

    if errors:
        st.warning("일부 종목을 불러오지 못했습니다:\n" + "\n".join(errors))

    if not strategy_curves:
        st.error("유효한 데이터가 없습니다. 티커/기간을 확인하세요.")
        st.stop()

    # 포트폴리오 합산 (날짜 정렬 후 합)
    strategy_df = pd.DataFrame(strategy_curves).ffill().dropna()
    buyhold_df = pd.DataFrame(buyhold_curves).ffill().dropna()
    common_idx = strategy_df.index.intersection(buyhold_df.index)
    strategy_total = strategy_df.loc[common_idx].sum(axis=1)
    buyhold_total = buyhold_df.loc[common_idx].sum(axis=1)

    metrics = compute_metrics(strategy_total, buyhold_total)

    # ------------------------------------------------------------
    # 결과 - 지표 카드
    # ------------------------------------------------------------
    st.subheader("성과 비교")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("전략 총수익률", f"{metrics['Total Return']:.2%}",
              f"{(metrics['Total Return'] - metrics['Buy&Hold Total Return']):+.2%} vs B&H")
    m2.metric("전략 CAGR", f"{metrics['CAGR']:.2%}",
              f"{(metrics['CAGR'] - metrics['Buy&Hold CAGR']):+.2%} vs B&H")
    m3.metric("전략 MDD", f"{metrics['MDD']:.2%}",
              f"{(metrics['MDD'] - metrics['Buy&Hold MDD']):+.2%} vs B&H", delta_color="inverse")
    m4.metric("Buy&Hold 총수익률", f"{metrics['Buy&Hold Total Return']:.2%}")

    total_trades = sum(len(df) for df in trades_by_ticker.values())
    st.caption(f"총 매매 횟수: {total_trades}건 | Sharpe(전략): {metrics['Sharpe']:.2f}")

    # ------------------------------------------------------------
    # 자산 곡선 차트
    # ------------------------------------------------------------
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=strategy_total.index, y=strategy_total.values,
                              name="Stop-Loss/Rebuy 전략", line=dict(color="#185FA5", width=2)))
    fig.add_trace(go.Scatter(x=buyhold_total.index, y=buyhold_total.values,
                              name="Buy & Hold", line=dict(color="#999999", width=2, dash="dash")))
    fig.update_layout(title="자산 곡선 비교", xaxis_title="날짜", yaxis_title="평가금액",
                       height=480, hovermode="x unified", legend=dict(orientation="h", y=1.05))
    st.plotly_chart(fig, use_container_width=True)

    # ------------------------------------------------------------
    # 종목별 상세 (탭)
    # ------------------------------------------------------------
    if len(tickers) > 1:
        st.subheader("종목별 상세")
        tabs = st.tabs(list(trades_by_ticker.keys()))
        for tab, tk in zip(tabs, trades_by_ticker.keys()):
            with tab:
                st.dataframe(trades_by_ticker[tk], use_container_width=True, hide_index=True)
    else:
        st.subheader("매매 내역")
        st.dataframe(list(trades_by_ticker.values())[0], use_container_width=True, hide_index=True)

    # ------------------------------------------------------------
    # 지표 상세 테이블 + 다운로드
    # ------------------------------------------------------------
    with st.expander("전체 지표 보기"):
        metrics_df = pd.DataFrame(metrics.items(), columns=["지표", "값"])
        st.dataframe(metrics_df, use_container_width=True, hide_index=True)

    all_trades = pd.concat(
        [df.assign(ticker=tk) for tk, df in trades_by_ticker.items()], ignore_index=True
    )
    st.download_button("매매내역 CSV 다운로드", all_trades.to_csv(index=False).encode("utf-8-sig"),
                        file_name="trades.csv", mime="text/csv")
else:
    st.info("왼쪽에서 조건을 입력하고 **비교 실행** 버튼을 눌러주세요.")
