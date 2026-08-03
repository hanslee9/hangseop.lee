import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go

from backtest_engine import (
    run_portfolio_backtest, compute_metrics, compute_rolling_returns, load_price_data
)

st.set_page_config(page_title="포트폴리오 백테스트", layout="wide")
st.title("포트폴리오 백테스트(테스트 v2)")
st.caption("한국(.KS/.KQ)·미국 종목/ETF 혼합, 최대 20종목, 배당 재투자, 리밸런싱, 정기 인출을 반영합니다.")

REBAL_LABEL = {'none': '없음(Buy&Hold)', 'M': '매월', 'Q': '매분기', 'Y': '매년'}
WITHDRAW_LABEL = {'none': '없음', 'monthly_fixed': '매월 고정금액', 'annual_fixed': '매년 고정금액', 'annual_pct': '매년 %'}

# ============================================================
# 1. Parameters
# ============================================================
st.subheader("Parameters")
c1, c2, c3, c4 = st.columns(4)
with c1:
    start_date = st.date_input("시작일", value=pd.to_datetime("2015-01-01"))
with c2:
    end_date = st.date_input("종료일", value=pd.to_datetime("today"))
with c3:
    initial_investment = st.number_input("초기 투자금액", min_value=100, value=10000, step=100)
with c4:
    use_benchmark = st.checkbox("벤치마크 사용", value=True)
    benchmark = st.text_input("벤치마크 티커", value="SPY", disabled=not use_benchmark)

c5, c6, c7 = st.columns(3)
with c5:
    withdraw_type = st.selectbox("정기 인출", options=list(WITHDRAW_LABEL.keys()),
                                  format_func=lambda k: WITHDRAW_LABEL[k])
with c6:
    withdraw_amount = st.number_input(
        "인출 비율(%)" if withdraw_type == 'annual_pct' else "인출 금액",
        min_value=0.0, value=0.0, step=1.0, disabled=(withdraw_type == 'none'))
    if withdraw_type == 'annual_pct':
        withdraw_amount = withdraw_amount / 100
with c7:
    st.write("")  # 레이아웃 여백

withdrawal = {'type': withdraw_type, 'amount': withdraw_amount}

# ============================================================
# 2. Portfolios (최대 4개 비교)
# ============================================================
st.subheader("Portfolios")

if 'n_portfolios' not in st.session_state:
    st.session_state.n_portfolios = 1

col_add, col_remove, _ = st.columns([1, 1, 6])
with col_add:
    if st.button("+ 포트폴리오 추가") and st.session_state.n_portfolios < 4:
        st.session_state.n_portfolios += 1
with col_remove:
    if st.button("− 포트폴리오 제거") and st.session_state.n_portfolios > 1:
        st.session_state.n_portfolios -= 1

tabs = st.tabs([f"Portfolio {i+1}" for i in range(st.session_state.n_portfolios)])
portfolio_configs = []

for i, tab in enumerate(tabs):
    with tab:
        colname, colrebal = st.columns([2, 1])
        with colname:
            name = st.text_input("이름", value=f"Portfolio {i+1}", key=f"name_{i}")
        with colrebal:
            rebal = st.selectbox("리밸런싱", options=list(REBAL_LABEL.keys()),
                                  format_func=lambda k: REBAL_LABEL[k], key=f"rebal_{i}")

        default_df = pd.DataFrame({"Ticker": ["VOO", "SCHD"], "Weight(%)": [60, 40]})
        edited = st.data_editor(
            default_df, num_rows="dynamic", key=f"editor_{i}",
            use_container_width=True,
            column_config={
                "Ticker": st.column_config.TextColumn("티커", help="예: 005930.KS, VOO"),
                "Weight(%)": st.column_config.NumberColumn("비중(%)", min_value=0, max_value=100, step=1),
            },
        )
        edited = edited[edited["Ticker"].astype(str).str.strip() != ""]
        edited = edited.head(20)

        total_w = edited["Weight(%)"].sum() if len(edited) else 0
        if abs(total_w - 100) < 0.01:
            st.success(f"비중 합계: {total_w:.1f}%  ({len(edited)}/20 종목)")
        else:
            st.warning(f"비중 합계: {total_w:.1f}% (100%가 되도록 조정하세요)  ({len(edited)}/20 종목)")

        portfolio_configs.append({
            "name": name, "rebalance": rebal,
            "tickers": [t.strip().upper() for t in edited["Ticker"].tolist()],
            "weights": edited["Weight(%)"].tolist(),
        })

# ============================================================
# 3. 실행
# ============================================================
run = st.button("백테스트 실행", type="primary")

if run:
    valid_configs = [c for c in portfolio_configs if len(c["tickers"]) > 0 and sum(c["weights"]) > 0]
    if not valid_configs:
        st.error("최소 1개 포트폴리오에 종목을 입력하세요.")
        st.stop()

    results = {}
    metrics_rows = []
    rolling_all = {}

    with st.spinner("데이터 조회 및 백테스트 실행 중..."):
        benchmark_returns = None
        if use_benchmark and benchmark.strip():
            try:
                bdata, _ = load_price_data([benchmark.strip().upper()], str(start_date), str(end_date))
                benchmark_returns = bdata[benchmark.strip().upper()]['Close'].pct_change()
            except Exception as e:
                st.warning(f"벤치마크 데이터 조회 실패: {e}")

        for cfg in valid_configs:
            try:
                result, withdrawn, cashflows, asset_returns = run_portfolio_backtest(
                    cfg["tickers"], cfg["weights"], str(start_date), str(end_date),
                    initial_investment, withdrawal, cfg["rebalance"]
                )
            except Exception as e:
                st.error(f"[{cfg['name']}] 백테스트 실패: {e}")
                continue

            metrics, dd = compute_metrics(
                result, initial_investment, cashflows, withdrawn,
                asset_returns, cfg["weights"], benchmark_returns
            )
            rolling, rolling_summary = compute_rolling_returns(result)

            results[cfg["name"]] = {"result": result, "dd": dd, "rolling": rolling}
            rolling_all[cfg["name"]] = rolling_summary
            metrics_rows.append({"Portfolio": cfg["name"], **metrics})

        if use_benchmark and benchmark_returns is not None:
            bdata, bdates = load_price_data([benchmark.strip().upper()], str(start_date), str(end_date))
            bench_pv = bdata[benchmark.strip().upper()]['Close']
            bench_pv = bench_pv / bench_pv.iloc[0] * initial_investment
            bench_result = pd.DataFrame({"Portfolio_Value": bench_pv})

    if not results:
        st.stop()

    # --- 성과지표 표 ---
    st.subheader("성과지표 요약")
    df_metrics = pd.DataFrame(metrics_rows).set_index("Portfolio")
    pct_cols = ['Cumulative Return', 'CAGR', 'MWRR', 'Max Drawdown', 'Avg Drawdown',
                'Volatility', 'Win Rate (Years)']
    fmt = {c: "{:.2%}" for c in pct_cols}
    fmt.update({
        'Start Value': "{:,.0f}", 'End Value': "{:,.0f}", 'Total Contributions': "{:,.0f}",
        'Total Withdrawn': "{:,.0f}", 'Longest Drawdown (yrs)': "{:.2f}",
        'Sharpe': "{:.2f}", 'Sortino': "{:.2f}", 'Calmar': "{:.2f}",
        'Ulcer Index': "{:.2f}", 'UPI': "{:.2f}",
        'Diversification Ratio': "{:.2f}", 'Beta': "{:.2f}",
    })
    st.dataframe(df_metrics.style.format(fmt), use_container_width=True)

    # --- 포트폴리오 가치 추이 (정규화) ---
    st.subheader("포트폴리오 가치 추이 (시작=100)")
    fig1 = go.Figure()
    for name, r in results.items():
        pv = r["result"]["Portfolio_Value"]
        fig1.add_trace(go.Scatter(x=pv.index, y=pv / pv.iloc[0] * 100, name=name))
    if use_benchmark and benchmark_returns is not None:
        fig1.add_trace(go.Scatter(
            x=bench_result.index, y=bench_result["Portfolio_Value"] / bench_result["Portfolio_Value"].iloc[0] * 100,
            name=f"{benchmark} (벤치마크)", line=dict(dash="dash")
        ))
    fig1.update_layout(height=420, margin=dict(l=10, r=10, t=10, b=10), yaxis_title="정규화 가치")
    st.plotly_chart(fig1, use_container_width=True)

    # --- Drawdown ---
    st.subheader("Drawdown")
    fig2 = go.Figure()
    for name, r in results.items():
        fig2.add_trace(go.Scatter(x=r["dd"].index, y=r["dd"] * 100, name=name, fill='tozeroy'))
    fig2.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10), yaxis_title="%")
    st.plotly_chart(fig2, use_container_width=True)

    # --- Rolling Return ---
    st.subheader("Rolling Annualized Return (1Y / 3Y / 5Y / 10Y)")
    rolling_tabs = st.tabs(list(results.keys()))
    for (name, r), tab in zip(results.items(), rolling_tabs):
        with tab:
            fig3 = go.Figure()
            for label, series in r["rolling"].items():
                fig3.add_trace(go.Scatter(x=series.index, y=series * 100, name=label))
            fig3.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10), yaxis_title="%")
            st.plotly_chart(fig3, use_container_width=True)

            summary_df = pd.DataFrame(rolling_all[name]).T
            st.dataframe(summary_df.style.format("{:.2%}"), use_container_width=True)
