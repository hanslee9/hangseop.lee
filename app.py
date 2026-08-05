import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go

from backtest_engine import (
    run_portfolio_backtest, compute_metrics, compute_rolling_returns, load_price_data
)

st.set_page_config(page_title="포트폴리오 백테스트", layout="wide")
st.title("포트폴리오 백테스트")
st.caption("한국(.KS/.KQ)·미국 종목/ETF 혼합, 최대 20종목, 배당 재투자, 리밸런싱, 정기 인출을 반영합니다.")

REBAL_LABEL = {'none': '없음(Buy&Hold)', 'M': '매월', 'Q': '매분기', 'Y': '매년'}
WITHDRAW_LABEL = {'none': '없음', 'monthly_fixed': '매월 고정금액', 'annual_fixed': '매년 고정금액', 'annual_pct': '매년 %'}

# ============================================================
# 1. Parameters
# ============================================================
st.subheader("Parameters")
c1, c2, c3, c4 = st.columns(4)
with c1:
    start_date = st.date_input(
        "시작일", value=pd.to_datetime("2015-01-01"),
        min_value=pd.to_datetime("1970-01-01"), max_value=pd.to_datetime("today"),
    )
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
        bench_result = None
        bench_name = None
        date_notices = []
        requested_start = pd.Timestamp(start_date)

        def _check_date_adjustment(label, meta):
            eff_start = meta['effective_start']
            if eff_start > requested_start:
                limiting = [t for t, d in meta['first_dates'].items() if d == eff_start]
                date_notices.append(
                    f"**{label}**: 요청한 시작일({requested_start.date()})보다 "
                    f"'{', '.join(limiting)}' 상장일이 늦어, **{eff_start.date()}**부터 계산했습니다."
                )

        if use_benchmark and benchmark.strip():
            bench_name = benchmark.strip().upper()
            try:
                # 벤치마크도 포트폴리오와 동일하게 (1) 배당 재투자(총수익) 기준,
                # (2) 사용자가 설정한 정기 인출 조건을 그대로 적용해서 계산
                # (인출 조건이 다르면 포트폴리오와 벤치마크를 공정하게 비교할 수 없음)
                bench_result, bench_withdrawn, bench_cashflows, bench_asset_returns, bench_meta = run_portfolio_backtest(
                    [bench_name], [100], str(start_date), str(end_date),
                    initial_investment, withdrawal, 'none'
                )
                benchmark_returns = bench_result['Portfolio_Value'].pct_change()
                _check_date_adjustment(f"{bench_name} (벤치마크)", bench_meta)
            except Exception as e:
                st.warning(f"벤치마크 데이터 조회 실패: {e}")

        for cfg in valid_configs:
            try:
                result, withdrawn, cashflows, asset_returns, cfg_meta = run_portfolio_backtest(
                    cfg["tickers"], cfg["weights"], str(start_date), str(end_date),
                    initial_investment, withdrawal, cfg["rebalance"]
                )
                _check_date_adjustment(cfg["name"], cfg_meta)
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

        if bench_result is not None:
            bench_metrics, bench_dd = compute_metrics(
                bench_result, initial_investment, bench_cashflows, bench_withdrawn,
                bench_asset_returns, [100], benchmark_returns
            )
            bench_rolling, bench_rolling_summary = compute_rolling_returns(bench_result)
            results[f"{bench_name} (벤치마크)"] = {"result": bench_result, "dd": bench_dd, "rolling": bench_rolling}
            rolling_all[f"{bench_name} (벤치마크)"] = bench_rolling_summary
            metrics_rows.append({"Portfolio": f"{bench_name} (벤치마크)", **bench_metrics})

    if not results:
        st.stop()

    # 체크박스(로그 스케일) 등 다른 위젯 조작으로 재실행되어도 결과가 사라지지
    # 않도록 세션에 저장
    st.session_state['bt_results'] = results
    st.session_state['bt_metrics_rows'] = metrics_rows
    st.session_state['bt_rolling_all'] = rolling_all
    st.session_state['bt_date_notices'] = date_notices

# ============================================================
# 4. 결과 표시 (세션에 저장된 결과가 있으면 항상 표시)
# ============================================================
if 'bt_results' in st.session_state:
    results = st.session_state['bt_results']
    if st.session_state.get('bt_date_notices'):
        st.info(
            "일부 종목의 상장일이 요청한 시작일보다 늦어 자동으로 조정되었습니다.\n\n"
            + "\n\n".join(st.session_state['bt_date_notices'])
        )
    metrics_rows = st.session_state['bt_metrics_rows']
    rolling_all = st.session_state['bt_rolling_all']

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
    log_scale = st.checkbox("로그 스케일(Log scale)", value=False)
    fig1 = go.Figure()
    for name, r in results.items():
        pv = r["result"]["Portfolio_Value"]
        line_style = dict(dash="dash") if "벤치마크" in name else {}
        fig1.add_trace(go.Scatter(x=pv.index, y=pv / pv.iloc[0] * 100, name=name, line=line_style))
    fig1.update_layout(
        height=420, margin=dict(l=10, r=10, t=10, b=10), yaxis_title="정규화 가치",
        yaxis_type="log" if log_scale else "linear",
    )
    st.plotly_chart(fig1, use_container_width=True)

    # --- Drawdown ---
    st.subheader("Drawdown")
    fig2 = go.Figure()
    for name, r in results.items():
        fig2.add_trace(go.Scatter(x=r["dd"].index, y=r["dd"] * 100, name=name, fill='tozeroy'))
    fig2.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10), yaxis_title="%")
    st.plotly_chart(fig2, use_container_width=True)

    # --- Rolling Return 요약 표 (Portfolio × Roll Period를 행으로 풀어써서
    # 포트폴리오 개수가 늘어나도 표가 옆으로 늘어나지 않도록 함) ---
    st.subheader("Rolling Returns")
    period_order = ['1Y', '3Y', '5Y', '7Y']
    table_rows = []
    for period in period_order:
        for name in results.keys():
            if period in rolling_all[name]:
                s = rolling_all[name][period]
                table_rows.append({
                    "Roll Period": period, "Portfolio": name,
                    "Average": s['Avg'], "High": s['Max'], "Low": s['Min'],
                })
    if table_rows:
        df_rolling = pd.DataFrame(table_rows).set_index(["Roll Period", "Portfolio"])
        st.dataframe(df_rolling.style.format("{:.2%}"), use_container_width=True)

    # --- Rolling Return 그래프: 기간별 탭 안에 모든 포트폴리오를 한 그래프에 겹쳐 표시 ---
    st.subheader("Annualized Rolling Return")
    available_periods = [p for p in period_order if any(p in r["rolling"] for r in results.values())]
    if available_periods:
        period_tabs = st.tabs(available_periods)
        for period, tab in zip(available_periods, period_tabs):
            with tab:
                fig3 = go.Figure()
                for name, r in results.items():
                    if period in r["rolling"]:
                        series = r["rolling"][period]
                        line_style = dict(dash="dash") if "벤치마크" in name else {}
                        fig3.add_trace(go.Scatter(x=series.index, y=series * 100, name=name, line=line_style))
                fig3.update_layout(height=380, margin=dict(l=10, r=10, t=10, b=10), yaxis_title="%")
                st.plotly_chart(fig3, use_container_width=True)
