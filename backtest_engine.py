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
    data = {}
    for t in tickers:
        # auto_adjust=False: 원본(비조정) 종가를 받아야 Dividends/Stock Splits를
        # 아래에서 수동으로 반영할 때 이중 반영(더블 카운팅)이 발생하지 않음
        df = yf.Ticker(t).history(start=start_date, end=end_date, auto_adjust=False)
        if df.empty:
            raise ValueError(f"{t}: 데이터를 가져오지 못했습니다. 티커를 확인하세요.")
        df.index = df.index.tz_localize(None)
        data[t] = df[['Close', 'Dividends', 'Stock Splits']]

    all_dates = sorted(set().union(*[df.index for df in data.values()]))
    for t in data:
        data[t] = data[t].reindex(all_dates).ffill()
        data[t][['Dividends', 'Stock Splits']] = data[t][['Dividends', 'Stock Splits']].fillna(0)

    return data, pd.DatetimeIndex(all_dates)


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

    data, dates = load_price_data(tickers, start_date, end_date)

    shares = {}
    for t, w in zip(tickers, weights):
        first_price = data[t]['Close'].iloc[0]
        shares[t] = (initial_investment * w) / first_price

    portfolio_history = []
    cashflows = [(dates[0], -initial_investment)]
    withdrawn_total = 0.0
    last_rebalance_period = None
    last_withdraw_period = None

    for i, d in enumerate(dates):
        for t in tickers:
            row = data[t].loc[d]
            if row['Dividends'] > 0:
                shares[t] += shares[t] * row['Dividends'] / row['Close']
            if row['Stock Splits'] > 0:
                shares[t] *= row['Stock Splits']

        values = {t: shares[t] * data[t]['Close'].loc[d] for t in tickers}
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
                values = {t: shares[t] * data[t]['Close'].loc[d] for t in tickers}
                total_value = sum(values.values())

        if rebalance_freq != 'none':
            period = d.to_period(rebalance_freq)
            if last_rebalance_period is None:
                last_rebalance_period = period
            elif period != last_rebalance_period:
                for t, w in zip(tickers, weights):
                    shares[t] = (total_value * w) / data[t]['Close'].loc[d]
                last_rebalance_period = period

        portfolio_history.append(total_value)

    result = pd.DataFrame({'Portfolio_Value': portfolio_history}, index=dates)
    cashflows.append((dates[-1], result['Portfolio_Value'].iloc[-1]))

    asset_returns = pd.DataFrame({t: data[t]['Close'].pct_change() for t in tickers})

    return result, withdrawn_total, cashflows, asset_returns


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
    windows = {'1Y': 1, '3Y': 3, '5Y': 5, '10Y': 10}
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
