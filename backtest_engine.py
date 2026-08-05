"""
포트폴리오 백테스트 엔진
- 다종목(한국/미국, 개별주+ETF 혼합) 배당 재투자 백테스트
- 리밸런싱, 정기 인출 지원
- 성과지표: CAGR, MWRR, MDD, Avg/Longest Drawdown, Volatility, Sharpe, Sortino,
            Calmar, Ulcer Index, UPI, Diversification Ratio, Beta
- Rolling Return: 1Y / 3Y / 5Y / 10Y
"""
import numpy as np
import pandas as pd
import yfinance as yf
from scipy.optimize import brentq
import streamlit as st


@st.cache_data(show_spinner=False, ttl=3600)
def load_price_data(tickers, start_date, end_date):
    """
    'Adj Close'(배당·액면분할이 모두 반영된 총수익 기준 조정종가)를 그대로 사용한다.
    이렇게 하면 배당 재투자·액면분할을 수동으로 재구성할 필요가 없어서,
    시장(한국/미국)마다 분할 처리 방식이 달라 생기던 이중/누락 반영 버그가
    구조적으로 사라진다. auto_adjust=False로 받아야 'Close'와 별도로
    'Adj Close'가 함께 내려온다.
    """
    data = {}
    first_dates = {}
    for t in tickers:
        df = yf.Ticker(t).history(start=start_date, end=end_date, auto_adjust=False)
        if df.empty:
            raise ValueError(f"{t}: 데이터를 가져오지 못했습니다. 티커를 확인하세요.")
        df.index = df.index.tz_localize(None)
        data[t] = df[['Adj Close']].rename(columns={'Adj Close': 'Price'})
        # yfinance는 요청한 시작일이 상장일 이전이어도 실제 상장일부터의 데이터만
        # 돌려주므로, 각 종목의 실제 데이터 시작일을 그대로 기록해두면 됨
        first_dates[t] = df.index.min()

    # 포트폴리오 공통 시작일 = 여러 종목 중 "가장 늦게 상장된" 종목의 시작일
    # (그래야 모든 종목이 동시에 데이터를 갖는 구간부터 계산 가능)
    effective_start = max(first_dates.values())
    effective_end = min(df.index.max() for df in data.values())

    all_dates = sorted(
        d for d in set().union(*[df.index for df in data.values()])
        if effective_start <= d <= effective_end
    )
    for t in data:
        data[t] = data[t].reindex(all_dates).ffill()

    meta = {'effective_start': effective_start, 'effective_end': effective_end, 'first_dates': first_dates}
    return data, pd.DatetimeIndex(all_dates), meta


@st.cache_data(show_spinner=False, ttl=86400)
def load_cpi_series(country):
    """
    country: 'US' 또는 'KR'
    FRED(세인트루이스 연은)의 공개 CSV 엔드포인트에서 월별 CPI를 받아
    일별로 보간(linear)한 뒤 ffill/bfill한 Series를 반환한다.
    - US: CPIAUCSL (미국 노동통계청 CPI, 매월 갱신)
    - KR: CPALTT01KRM659N (OECD 집계 한국 CPI, Index 2015=100, 월간)

    공식 CPI 발표는 몇 달의 지연이 있으므로(특히 국제 미러링 시리즈),
    데이터가 없는 최근 구간은 마지막 값을 그대로 반복(flat)하지 않고
    최근 12개월 평균 월간 변화율로 오늘 날짜까지 추세를 연장한다.
    (그렇지 않으면 최근 기간의 실질수익률이 인플레이션 0%로 왜곡됨)
    """
    series_id = 'CPIAUCSL' if country == 'US' else 'CPALTT01KRM659N'
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    df = pd.read_csv(url)
    df.columns = ['date', 'cpi']
    df['date'] = pd.to_datetime(df['date'])
    df['cpi'] = pd.to_numeric(df['cpi'], errors='coerce')
    df = df.dropna().set_index('date')['cpi']

    today = pd.Timestamp.today().normalize()
    last_date = df.index.max()
    if last_date < today:
        monthly_growth = df.pct_change().dropna()
        recent_growth = monthly_growth.tail(12).mean()
        if pd.isna(recent_growth):
            recent_growth = 0.0
        future_months = pd.date_range(last_date, today + pd.Timedelta(days=31), freq='MS')
        future_months = future_months[future_months > last_date]
        val = df.iloc[-1]
        extension = {}
        for d in future_months:
            val = val * (1 + recent_growth)
            extension[d] = val
        if extension:
            df = pd.concat([df, pd.Series(extension)]).sort_index()

    daily_index = pd.date_range(df.index.min(), max(df.index.max(), today), freq='D')
    daily = df.reindex(df.index.union(daily_index)).sort_index()
    daily = daily.interpolate(method='time').reindex(daily_index).ffill().bfill()
    return daily


def ticker_country(ticker):
    """티커 접미사로 국가 판별 (.KS/.KQ = 한국, 그 외 = 미국)"""
    return 'KR' if ticker.upper().endswith(('.KS', '.KQ')) else 'US'


def compute_real_value(pv, tickers, weights_pct):
    """
    명목 포트폴리오 가치(pv)를 인플레이션 반영 실질 가치로 변환.
    포트폴리오에 섞인 종목의 국가별(미국/한국) CPI를 초기 비중으로 가중 블렌딩해서
    디플레이터를 만든 뒤 곱한다 (근사치 — 종목별 실제 시점별 비중 변화까지는 반영하지 않음).
    """
    weights = np.array(weights_pct, dtype=float)
    weights = weights / weights.sum()

    countries = [ticker_country(t) for t in tickers]
    needed = set(countries)

    cpi_by_country = {c: load_cpi_series(c) for c in needed}

    t0 = pv.index[0]
    deflator = pd.Series(0.0, index=pv.index)
    for t, w, c in zip(tickers, weights, countries):
        cpi = cpi_by_country[c].reindex(pv.index).ffill().bfill()
        deflator += w * (cpi.loc[t0] / cpi)

    return pv * deflator


def run_portfolio_backtest(tickers, weights_pct, start_date, end_date,
                            initial_investment, withdrawal, rebalance_freq):
    """
    tickers: ['VOO', 'SCHD', ...]
    weights_pct: [60, 40, ...]  (합계 100 기준, 내부에서 정규화)
    withdrawal: {'type': 'none'|'monthly_fixed'|'annual_fixed'|'annual_pct', 'amount': float}
    rebalance_freq: 'none' | 'M' | 'Q' | 'Y'
    """
    weights = np.array(weights_pct, dtype=float)
    weights = weights / weights.sum()

    data, dates, meta = load_price_data(tickers, start_date, end_date)

    shares = {}
    for t, w in zip(tickers, weights):
        first_price = data[t]['Price'].iloc[0]
        shares[t] = (initial_investment * w) / first_price

    portfolio_history = []
    cashflows = [(dates[0], -initial_investment)]
    withdrawn_total = 0.0
    last_rebalance_period = None
    last_withdraw_period = None

    for i, d in enumerate(dates):
        values = {t: shares[t] * data[t]['Price'].loc[d] for t in tickers}
        total_value = sum(values.values())

        if withdrawal['type'] != 'none' and i > 0:
            period = (d.year, d.month) if withdrawal['type'] == 'monthly_fixed' else d.year
            if last_withdraw_period is None:
                last_withdraw_period = period
            elif period != last_withdraw_period:
                if withdrawal['type'] == 'annual_pct':
                    amount = total_value * withdrawal['amount']
                else:
                    amount = withdrawal['amount']
                amount = min(amount, total_value)
                ratio = amount / total_value if total_value > 0 else 0
                for t in tickers:
                    shares[t] *= (1 - ratio)
                withdrawn_total += amount
                cashflows.append((d, amount))
                last_withdraw_period = period
                values = {t: shares[t] * data[t]['Price'].loc[d] for t in tickers}
                total_value = sum(values.values())

        if rebalance_freq != 'none':
            period = d.to_period(rebalance_freq)
            if last_rebalance_period is None:
                last_rebalance_period = period
            elif period != last_rebalance_period:
                for t, w in zip(tickers, weights):
                    shares[t] = (total_value * w) / data[t]['Price'].loc[d]
                last_rebalance_period = period

        portfolio_history.append(total_value)

    result = pd.DataFrame({'Portfolio_Value': portfolio_history}, index=dates)
    cashflows.append((dates[-1], result['Portfolio_Value'].iloc[-1]))

    asset_returns = pd.DataFrame({t: data[t]['Price'].pct_change() for t in tickers})

    return result, withdrawn_total, cashflows, asset_returns, meta


def compute_drawdown_periods(pv):
    cum_max = pv.cummax()
    dd = (pv - cum_max) / cum_max
    periods = []
    in_dd, start, trough = False, None, 0.0
    for date, val in dd.items():
        if val < 0 and not in_dd:
            in_dd, start, trough = True, date, val
        elif val < 0 and in_dd:
            trough = min(trough, val)
        elif val >= 0 and in_dd:
            periods.append({'start': start, 'end': date, 'trough': trough, 'days': (date - start).days})
            in_dd = False
    if in_dd:
        periods.append({'start': start, 'end': dd.index[-1], 'trough': trough,
                         'days': (dd.index[-1] - start).days})
    return dd, periods


def compute_mwrr(cashflows):
    t0 = cashflows[0][0]

    def npv(r):
        return sum(cf / (1 + r) ** ((d - t0).days / 365.0) for d, cf in cashflows)

    try:
        return brentq(npv, -0.9999, 10)
    except ValueError:
        return np.nan


def compute_metrics(result, initial_investment, cashflows, withdrawn_total,
                     asset_returns, weights_pct, benchmark_returns=None, risk_free_rate=0.02):
    weights = np.array(weights_pct, dtype=float)
    weights = weights / weights.sum()

    pv = result['Portfolio_Value']
    start_value = pv.iloc[0]
    end_value = pv.iloc[-1]
    total_return = end_value / initial_investment - 1

    days = (pv.index[-1] - pv.index[0]).days
    years = days / 365.25
    cagr = (end_value / start_value) ** (1 / years) - 1 if years > 0 else np.nan

    daily_ret = pv.pct_change().dropna()
    volatility = daily_ret.std() * np.sqrt(252)

    downside = daily_ret[daily_ret < 0]
    sortino_vol = downside.std() * np.sqrt(252) if len(downside) > 0 else np.nan
    sharpe = (daily_ret.mean() * 252 - risk_free_rate) / volatility if volatility else np.nan
    sortino = (daily_ret.mean() * 252 - risk_free_rate) / sortino_vol if sortino_vol else np.nan

    dd, dd_periods = compute_drawdown_periods(pv)
    mdd = dd.min()
    avg_dd = np.mean([p['trough'] for p in dd_periods]) if dd_periods else 0.0
    longest_dd_years = (max([p['days'] for p in dd_periods]) / 365.25) if dd_periods else 0.0
    calmar = cagr / abs(mdd) if mdd != 0 else np.nan

    ulcer_index = np.sqrt((dd ** 2).mean())
    upi = (cagr - risk_free_rate) / ulcer_index if ulcer_index else np.nan

    mwrr = compute_mwrr(cashflows)

    annual = pv.resample('YE').last()
    annual_ret = annual.pct_change().dropna()
    win_rate = (annual_ret > 0).mean() if len(annual_ret) > 0 else np.nan

    indiv_vol = asset_returns.std() * np.sqrt(252)
    weighted_avg_vol = (indiv_vol.values * weights).sum()
    diversification_ratio = weighted_avg_vol / volatility if volatility else np.nan

    beta = np.nan
    if benchmark_returns is not None:
        aligned = pd.concat([daily_ret, benchmark_returns], axis=1).dropna()
        aligned.columns = ['port', 'bench']
        cov = np.cov(aligned['port'], aligned['bench'])[0, 1]
        var_bench = aligned['bench'].var()
        beta = cov / var_bench if var_bench else np.nan

    return {
        'Start Value': start_value, 'End Value': end_value,
        'Total Contributions': initial_investment, 'Cumulative Return': total_return,
        'CAGR': cagr, 'MWRR': mwrr, 'Max Drawdown': mdd, 'Avg Drawdown': avg_dd,
        'Longest Drawdown (yrs)': longest_dd_years, 'Volatility': volatility,
        'Sharpe': sharpe, 'Sortino': sortino, 'Calmar': calmar,
        'Ulcer Index': ulcer_index, 'UPI': upi,
        'Diversification Ratio': diversification_ratio, 'Beta': beta,
        'Win Rate (Years)': win_rate, 'Total Withdrawn': withdrawn_total,
    }, dd


def compute_rolling_returns(result):
    pv = result['Portfolio_Value']
    windows = {'1Y': 1, '3Y': 3, '5Y': 5, '7Y': 7}
    rolling, summary = {}, {}
    for label, yrs in windows.items():
        window_days = int(252 * yrs)
        if len(pv) <= window_days:
            continue
        ratio = pv / pv.shift(window_days)
        ann_ret = (ratio ** (1 / yrs) - 1).dropna()
        rolling[label] = ann_ret
        summary[label] = {'Avg': ann_ret.mean(), 'Min': ann_ret.min(), 'Max': ann_ret.max()}
    return rolling, summary
