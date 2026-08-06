import io

import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go

from backtest_engine import (
    run_portfolio_backtest, compute_metrics, compute_rolling_returns, load_price_data,
    compute_real_value,
)

st.set_page_config(page_title="포트폴리오 백테스트", layout="wide")
st.title("포트폴리오 백테스트")
st.caption("한국(.KS/.KQ)·미국 종목/ETF 혼합, 최대 20종목, 배당 재투자, 리밸런싱, 정기 인출을 반영합니다.")

REBAL_LABEL = {'none': '없음(Buy&Hold)', 'M': '매월', 'Q': '매분기', 'Y': '매년'}
WITHDRAW_LABEL = {'none': '없음', 'monthly_fixed': '매월 고정금액', 'annual_fixed': '매년 고정금액', 'annual_pct': '매년 %'}

_table_counter = {'n': 0}
GROUP_SHADE_PALETTE = ['#EAF2FB', '#FBEAEA']  # 옅은 파랑 / 옅은 붉은색 번갈아


def render_table(df, fmt=None, wrap_headers=True, max_col_width=78, filename="table", shade_groups=False):
    """표 렌더링 공용 헬퍼: 헤더는 줄바꿈해서 폭을 줄이고, 음수는 빨간색으로 표시.
    shade_groups=True면 MultiIndex 컬럼의 최상위 그룹(포트폴리오)별로 옅은 배경색을
    번갈아 적용한다. 우측 상단에 엑셀(.xlsx) 다운로드 버튼도 함께 제공."""
    _table_counter['n'] += 1
    key = f"dl_{filename}_{_table_counter['n']}"

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Sheet1')
    st.download_button(
        "엑셀 다운로드", data=buffer.getvalue(), file_name=f"{filename}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key=key,
    )

    styler = df.style
    if fmt:
        styler = styler.format(fmt)
    styler = styler.map(lambda v: 'color:#c0392b' if isinstance(v, (int, float)) and v < 0 else '')

    if shade_groups and isinstance(df.columns, pd.MultiIndex):
        groups = list(dict.fromkeys(df.columns.get_level_values(0)))
        for i, g in enumerate(groups):
            color = GROUP_SHADE_PALETTE[i % len(GROUP_SHADE_PALETTE)]
            cols = df.columns[df.columns.get_level_values(0) == g]
            styler = styler.set_properties(
                subset=pd.IndexSlice[:, cols], **{'background-color': color}
            )

    header_props = [('font-size', '11px'), ('padding', '4px 5px'), ('text-align', 'center'),
                     ('background-color', '#f0f2f6')]
    if wrap_headers:
        header_props += [('white-space', 'normal'), ('word-break', 'break-word'),
                          ('max-width', f'{max_col_width}px')]
    styler = styler.set_table_styles([
        {'selector': 'th', 'props': header_props},
        {'selector': 'td', 'props': [('font-size', '12px'), ('padding', '3px 6px'), ('text-align', 'right')]},
    ])
    st.markdown(styler.to_html(), unsafe_allow_html=True)


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

        if i == 0:
            default_df = pd.DataFrame({"Ticker": ["VOO", "SCHD"], "Weight(%)": [60, 40]})
        else:
            default_df = pd.DataFrame({"Ticker": ["", ""], "Weight(%)": [0, 0]})
        edited = st.data_editor(
            default_df, num_rows="dynamic", key=f"editor_{i}",
            use_container_width=True,
            column_config={
                "Ticker": st.column_config.TextColumn("티커", help="예: 005930.KS, VOO"),
                "Weight(%)": st.column_config.NumberColumn("비중(%)", min_value=0, max_value=100, step=1),
            },
        )
        edited = edited.copy()
        edited["Ticker"] = edited["Ticker"].fillna("").astype(str).str.strip()
        edited["Weight(%)"] = pd.to_numeric(edited["Weight(%)"], errors="coerce").fillna(0)
        edited = edited[edited["Ticker"] != ""]
        edited = edited.head(20)

        total_w = edited["Weight(%)"].sum() if len(edited) else 0
        if abs(total_w - 100) < 0.01:
            st.success(f"비중 합계: {total_w:.1f}%  ({len(edited)}/20 종목)")
        else:
            st.warning(f"비중 합계: {total_w:.1f}% (100%가 되도록 조정하세요)  ({len(edited)}/20 종목)")

        portfolio_configs.append({
            "name": name, "rebalance": rebal,
            "tickers": [t.upper() for t in edited["Ticker"].tolist()],
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
        requested_start = pd.Timestamp(start_date)

        # --- 0단계: 모든 포트폴리오 + 벤치마크의 실제 데이터 시작 가능일을 먼저 조회 ---
        # (여러 종목/포트폴리오 중 가장 늦게 상장된 종목 기준으로 전체를 통일하기 위함)
        entities = list(valid_configs)
        if use_benchmark and benchmark.strip():
            bench_name = benchmark.strip().upper()
            entities = entities + [{"name": f"{bench_name} (벤치마크)", "tickers": [bench_name]}]

        global_start = requested_start
        limiting_label, limiting_tickers = None, []
        for ent in entities:
            try:
                _, _, ent_meta = load_price_data(ent["tickers"], str(start_date), str(end_date))
            except Exception as e:
                st.error(f"[{ent['name']}] 데이터 조회 실패: {e}")
                continue
            if ent_meta['effective_start'] > global_start:
                global_start = ent_meta['effective_start']
                limiting_label = ent["name"]
                limiting_tickers = [t for t, d in ent_meta['first_dates'].items() if d == global_start]

        common_start = str(global_start.date())

        # --- 1단계: 통일된 시작일(common_start)로 벤치마크 실행 ---
        if use_benchmark and benchmark.strip():
            try:
                # 벤치마크도 포트폴리오와 동일하게 (1) 배당 재투자(총수익) 기준,
                # (2) 사용자가 설정한 정기 인출 조건을 그대로 적용해서 계산
                # (인출 조건이 다르면 포트폴리오와 벤치마크를 공정하게 비교할 수 없음)
                bench_result, bench_withdrawn, bench_cashflows, bench_asset_returns, bench_meta = run_portfolio_backtest(
                    [bench_name], [100], common_start, str(end_date),
                    initial_investment, withdrawal, 'none'
                )
                benchmark_returns = bench_result['Portfolio_Value'].pct_change()
            except Exception as e:
                st.warning(f"벤치마크 데이터 조회 실패: {e}")

        # --- 2단계: 통일된 시작일(common_start)로 포트폴리오 실행 ---
        for cfg in valid_configs:
            try:
                result, withdrawn, cashflows, asset_returns, cfg_meta = run_portfolio_backtest(
                    cfg["tickers"], cfg["weights"], common_start, str(end_date),
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

            results[cfg["name"]] = {"result": result, "dd": dd, "rolling": rolling,
                                     "tickers": cfg["tickers"], "weights": cfg["weights"]}
            rolling_all[cfg["name"]] = rolling_summary
            metrics_rows.append({"Portfolio": cfg["name"], **metrics})

        if bench_result is not None:
            bench_metrics, bench_dd = compute_metrics(
                bench_result, initial_investment, bench_cashflows, bench_withdrawn,
                bench_asset_returns, [100], benchmark_returns
            )
            bench_rolling, bench_rolling_summary = compute_rolling_returns(bench_result)
            results[f"{bench_name} (벤치마크)"] = {"result": bench_result, "dd": bench_dd, "rolling": bench_rolling,
                                                    "tickers": [bench_name], "weights": [100]}
            rolling_all[f"{bench_name} (벤치마크)"] = bench_rolling_summary
            metrics_rows.append({"Portfolio": f"{bench_name} (벤치마크)", **bench_metrics})

    if not results:
        st.stop()

    # 체크박스(로그 스케일) 등 다른 위젯 조작으로 재실행되어도 결과가 사라지지
    # 않도록 세션에 저장
    st.session_state['bt_results'] = results
    st.session_state['bt_metrics_rows'] = metrics_rows
    st.session_state['bt_rolling_all'] = rolling_all
    st.session_state['bt_start_notice'] = (
        f"'{limiting_label}'의 '{', '.join(limiting_tickers)}' 상장일이 가장 늦어, "
        f"전체 분석 시작일을 **{global_start.date()}**로 통일했습니다 (그 이전 구간은 분석에서 제외)."
        if global_start > requested_start else None
    )

# ============================================================
# 4. 결과 표시 (세션에 저장된 결과가 있으면 항상 표시)
# ============================================================
if 'bt_results' in st.session_state:
    results = st.session_state['bt_results']
    if st.session_state.get('bt_start_notice'):
        st.info(st.session_state['bt_start_notice'])
    metrics_rows = st.session_state['bt_metrics_rows']
    rolling_all = st.session_state['bt_rolling_all']

    # 포트폴리오/벤치마크별 고정 색상 (모든 그래프에서 동일하게 사용해 구분이 쉽도록)
    COLOR_PALETTE = ['#1F5FA6', '#D64545', '#2E9E5B', '#8E5CC7', '#E08A2A', '#1AA6A6']
    COLOR_MAP = {name: COLOR_PALETTE[i % len(COLOR_PALETTE)] for i, name in enumerate(results.keys())}

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
    render_table(df_metrics, fmt, filename="성과지표_요약")

    # --- 포트폴리오 가치 추이 & 누적 수익률 ---
    st.subheader("포트폴리오 가치 추이 & 누적 수익률")
    c_opt1, c_opt2 = st.columns(2)
    with c_opt1:
        log_scale = st.checkbox("로그 스케일(Log scale)", value=False)
    with c_opt2:
        adjust_inflation = st.checkbox("인플레이션 반영(실질 가치)", value=False)

    plot_series = {}  # name -> (원본 pv 또는 인플레이션 조정된 pv)
    if adjust_inflation:
        with st.spinner("국가별 CPI 데이터 조회 중..."):
            for name, r in results.items():
                try:
                    plot_series[name] = compute_real_value(
                        r["result"]["Portfolio_Value"], r["tickers"], r["weights"]
                    )
                except Exception as e:
                    st.warning(f"[{name}] 인플레이션 데이터 조회 실패, 명목 가치로 표시합니다: {e}")
                    plot_series[name] = r["result"]["Portfolio_Value"]
        st.caption(
            "실질 가치는 종목의 국가(.KS/.KQ=한국, 그 외=미국)별 CPI를 초기 비중으로 "
            "가중 평균해서 디플레이터로 사용한 근사치입니다."
        )
    else:
        for name, r in results.items():
            plot_series[name] = r["result"]["Portfolio_Value"]

    if adjust_inflation:
        st.markdown("**실질(인플레이션 반영) CAGR 비교**")
        real_rows = []
        for name, r in results.items():
            pv = r["result"]["Portfolio_Value"]
            real_pv = plot_series[name]
            years = (pv.index[-1] - pv.index[0]).days / 365.25
            nominal_cagr = (pv.iloc[-1] / pv.iloc[0]) ** (1 / years) - 1
            real_cagr = (real_pv.iloc[-1] / real_pv.iloc[0]) ** (1 / years) - 1
            real_rows.append({
                "Portfolio": name,
                "명목 CAGR": nominal_cagr,
                "실질 CAGR": real_cagr,
                "연평균 인플레이션 효과": nominal_cagr - real_cagr,
            })
        df_real = pd.DataFrame(real_rows).set_index("Portfolio")
        render_table(df_real, "{:.2%}", filename="실질_CAGR_비교")

    LINE_WIDTH = 1.8

    def hex_to_rgba(hex_color, alpha):
        h = hex_color.lstrip('#')
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return f"rgba({r},{g},{b},{alpha})"

    fig1 = go.Figure()
    for name, pv in plot_series.items():
        normalized = pv / pv.iloc[0] * 100
        # 마지막 지점에만 금액 텍스트를 붙임(trace 자체의 text) - 별도 annotation
        # 레이어 방식은 로그축에서 표시가 누락되는 경우가 있어, 데이터 포인트에
        # 직접 붙이는 방식이 더 안정적임
        text_labels = [""] * (len(pv) - 1) + [f"{pv.iloc[-1]:,.0f}"]
        fig1.add_trace(go.Scatter(
            x=pv.index, y=normalized, name=name, mode="lines+text",
            text=text_labels, textposition="middle right",
            textfont=dict(size=11, color=COLOR_MAP[name]),
            line=dict(width=LINE_WIDTH, color=COLOR_MAP[name]),
        ))
    fig1.update_layout(
        height=480, margin=dict(l=10, r=75, t=30, b=90),
        yaxis_title="정규화 가치 (시작=100)" + ("(실질)" if adjust_inflation else ""),
        yaxis_type="log" if log_scale else "linear",
        title="Performance Summary" + (" - 로그 스케일" if log_scale else ""),
        showlegend=True,
        legend=dict(orientation="h", yanchor="top", y=-0.2, xanchor="center", x=0.5),
    )
    st.plotly_chart(
        fig1, use_container_width=True,
        config={"toImageButtonOptions": {"filename": "Performance_Summary"}, "displayModeBar": True},
    )

    fig1b = go.Figure()
    for name, pv in plot_series.items():
        pct = (pv / pv.iloc[0] - 1) * 100
        fig1b.add_trace(go.Scatter(
            x=pv.index, y=pct, name=name,
            line=dict(width=LINE_WIDTH, color=COLOR_MAP[name]),
        ))
    fig1b.update_layout(
        height=420, margin=dict(l=10, r=10, t=30, b=10),
        yaxis_title="누적 수익률 (%)" + ("(실질)" if adjust_inflation else ""),
        title="누적 수익률",
    )
    st.plotly_chart(
        fig1b, use_container_width=True,
        config={"toImageButtonOptions": {"filename": "누적수익률"}, "displayModeBar": True},
    )

    # --- Drawdown ---
    st.subheader("Drawdown")
    fig2 = go.Figure()
    for name, r in results.items():
        fig2.add_trace(go.Scatter(
            x=r["dd"].index, y=r["dd"] * 100, name=name, fill='tozeroy',
            line=dict(width=LINE_WIDTH, color=COLOR_MAP[name]),
            fillcolor=hex_to_rgba(COLOR_MAP[name], 0.15),
        ))
    fig2.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10), yaxis_title="%")
    st.plotly_chart(fig2, use_container_width=True, config={"toImageButtonOptions": {"filename": "drawdown"}, "displayModeBar": True})

    # --- 연도별(달력연도) 수익 표: 기본 3열(Return/Balance/Profit·Loss),
    # 인플레이션 반영 시 Real 3열(Real Return/Real Balance/Real Profit·Loss) 추가 ---
    def build_annual_table(nominal_dict, real_dict=None):
        rows = {}
        for name, s_nom in nominal_dict.items():
            annual_nom = s_nom.resample('YE').last()
            annual_nom.index = annual_nom.index.year
            prev_nom = s_nom.iloc[0]
            ret, bal, pl = {}, {}, {}
            for yr, val in annual_nom.items():
                ret[yr] = val / prev_nom - 1
                bal[yr] = val
                pl[yr] = val - prev_nom
                prev_nom = val
            rows[(name, 'Return')] = pd.Series(ret)
            rows[(name, 'Balance')] = pd.Series(bal)
            rows[(name, 'Profit/Loss')] = pd.Series(pl)

            if real_dict is not None:
                s_real = real_dict[name]
                annual_real = s_real.resample('YE').last()
                annual_real.index = annual_real.index.year
                prev_real = s_real.iloc[0]
                rret, rbal, rpl = {}, {}, {}
                for yr, val in annual_real.items():
                    rret[yr] = val / prev_real - 1
                    rbal[yr] = val
                    rpl[yr] = val - prev_real
                    prev_real = val
                rows[(name, 'Real Return')] = pd.Series(rret)
                rows[(name, 'Real Balance')] = pd.Series(rbal)
                rows[(name, 'Real Profit/Loss')] = pd.Series(rpl)

        df = pd.DataFrame(rows).sort_index()
        df.columns = pd.MultiIndex.from_tuples(df.columns)
        df.index = [f"{y}년" for y in df.index]
        df.index.name = "기간"
        return df

    st.subheader("연도별 수익률")
    nominal_series = {name: r["result"]["Portfolio_Value"] for name, r in results.items()}
    real_series = plot_series if adjust_inflation else None

    df_annual = build_annual_table(nominal_series, real_series)

    fmt_annual = {}
    for name in results.keys():
        fmt_annual[(name, 'Return')] = "{:.2%}"
        fmt_annual[(name, 'Balance')] = "{:,.0f}"
        fmt_annual[(name, 'Profit/Loss')] = "{:,.0f}"
        if adjust_inflation:
            fmt_annual[(name, 'Real Return')] = "{:.2%}"
            fmt_annual[(name, 'Real Balance')] = "{:,.0f}"
            fmt_annual[(name, 'Real Profit/Loss')] = "{:,.0f}"
    render_table(df_annual, fmt_annual, filename="연도별_수익률", shade_groups=True)

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
        render_table(df_rolling, "{:.2%}", filename="Rolling_Returns")

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
                        fig3.add_trace(go.Scatter(
                            x=series.index, y=series * 100, name=name,
                            line=dict(width=LINE_WIDTH, color=COLOR_MAP[name]),
                        ))
                fig3.update_layout(height=380, margin=dict(l=10, r=10, t=10, b=10), yaxis_title="%")
                st.plotly_chart(fig3, use_container_width=True, config={"toImageButtonOptions": {"filename": f"rolling_return_{period}"}, "displayModeBar": True})
