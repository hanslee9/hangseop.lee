"""
Trailing Stop-Loss / Rebuy Backtest
====================================

로직
----
1. 진입 시점 가격을 '로컬 고점(peak)'으로 간주하고 보유 상태로 시작한다.
2. 보유 중에는 가격이 신고가를 갱신할 때마다 peak을 갱신한다.
   가격이 peak 대비 -stop_pct 이하로 하락하면 전량 매도(손절)하고,
   그 시점 가격을 '로컬 저점(trough)'으로 간주하며 관망 상태로 전환한다.
3. 관망 중에는 가격이 신저가를 갱신할 때마다 trough를 갱신한다.
   가격이 trough 대비 +rebuy_pct 이상 상승하면 전량 재매수하고,
   그 시점 가격을 새 peak으로 간주하며 다시 보유 상태로 전환한다.
4. 2~3을 반복한다.

데이터 소스: yfinance (한국 종목은 .KS/.KQ 접미사, 미국 종목/ETF는 티커 그대로)
"""

import warnings
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")


# ------------------------------------------------------------------
# 1. 데이터 로딩
# ------------------------------------------------------------------
def fetch_prices(ticker: str, start: str, end: str | None = None) -> pd.Series:
    """yfinance에서 일별 종가(수정주가)를 가져온다."""
    df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
    if df.empty:
        raise ValueError(f"'{ticker}' 데이터를 가져오지 못했습니다. 티커를 확인하세요.")
    close = df["Close"]
    if isinstance(close, pd.DataFrame):  # 멀티인덱스 컬럼 방어
        close = close.iloc[:, 0]
    close.name = ticker
    return close.dropna()


# ------------------------------------------------------------------
# 2. Trailing Stop / Rebuy 백테스트 엔진
# ------------------------------------------------------------------
@dataclass
class Trade:
    date: pd.Timestamp
    action: str          # 'BUY' or 'SELL'
    price: float
    reason: str           # 'ENTRY', 'STOP_LOSS', 'REBUY'


@dataclass
class BacktestResult:
    equity_curve: pd.Series
    trades: list = field(default_factory=list)

    def trades_df(self) -> pd.DataFrame:
        return pd.DataFrame([t.__dict__ for t in self.trades])


def run_trailing_stop_backtest(
    prices: pd.Series,
    stop_pct: float,
    rebuy_pct: float,
    initial_capital: float = 10_000_000,
) -> BacktestResult:
    """
    prices    : 일별 종가 시계열
    stop_pct  : 고점 대비 손절 하락률 (예: 0.10 -> 고점 대비 -10%)
    rebuy_pct : 저점 대비 재매수 상승률 (예: 0.05 -> 저점 대비 +5%)
    """
    dates = prices.index
    entry_price = prices.iloc[0]

    position = 1                 # 1: 보유, 0: 현금(관망)
    shares = initial_capital / entry_price
    cash = 0.0
    peak = entry_price           # 초기 진입가 = 로컬 고점으로 간주
    trough = None

    trades = [Trade(dates[0], "BUY", entry_price, "ENTRY")]
    equity = [initial_capital]

    for i in range(1, len(prices)):
        p = prices.iloc[i]
        d = dates[i]

        if position == 1:
            if p > peak:
                peak = p
            if p <= peak * (1 - stop_pct):
                cash = shares * p
                shares = 0.0
                position = 0
                trough = p
                trades.append(Trade(d, "SELL", p, "STOP_LOSS"))
        else:
            if p < trough:
                trough = p
            if p >= trough * (1 + rebuy_pct):
                shares = cash / p
                cash = 0.0
                position = 1
                peak = p
                trades.append(Trade(d, "BUY", p, "REBUY"))

        equity.append(shares * p + cash)

    equity_curve = pd.Series(equity, index=dates, name="Equity")
    return BacktestResult(equity_curve=equity_curve, trades=trades)


# ------------------------------------------------------------------
# 3. 성과 지표
# ------------------------------------------------------------------
def compute_metrics(equity: pd.Series, buy_hold: pd.Series) -> dict:
    n_years = (equity.index[-1] - equity.index[0]).days / 365.25

    def cagr(series):
        return (series.iloc[-1] / series.iloc[0]) ** (1 / n_years) - 1

    def mdd(series):
        cum_max = series.cummax()
        drawdown = series / cum_max - 1
        return drawdown.min()

    daily_ret = equity.pct_change().dropna()
    sharpe = (
        daily_ret.mean() / daily_ret.std() * np.sqrt(252) if daily_ret.std() > 0 else np.nan
    )

    return {
        "Start Value": equity.iloc[0],
        "End Value": equity.iloc[-1],
        "Total Return": equity.iloc[-1] / equity.iloc[0] - 1,
        "CAGR": cagr(equity),
        "MDD": mdd(equity),
        "Sharpe": sharpe,
        "Buy&Hold CAGR": cagr(buy_hold),
        "Buy&Hold MDD": mdd(buy_hold),
        "Buy&Hold Total Return": buy_hold.iloc[-1] / buy_hold.iloc[0] - 1,
    }


# ------------------------------------------------------------------
# 4. 시각화
# ------------------------------------------------------------------
def plot_results(prices: pd.Series, result: BacktestResult, ticker: str, out_path: str):
    import matplotlib.pyplot as plt

    plt.rcParams["axes.unicode_minus"] = False
    fig, axes = plt.subplots(2, 1, figsize=(13, 9), sharex=True)

    # 가격 + 매매 시그널
    axes[0].plot(prices.index, prices.values, color="#333333", linewidth=1, label=ticker)
    buys = [t for t in result.trades if t.action == "BUY"]
    sells = [t for t in result.trades if t.action == "SELL"]
    axes[0].scatter([t.date for t in buys], [t.price for t in buys],
                     marker="^", color="green", s=80, label="Buy", zorder=5)
    axes[0].scatter([t.date for t in sells], [t.price for t in sells],
                     marker="v", color="red", s=80, label="Sell (Stop-Loss)", zorder=5)
    axes[0].set_title(f"{ticker} Price & Trailing Stop / Rebuy Signals")
    axes[0].legend()
    axes[0].grid(True, alpha=0.3)

    # 자산 곡선 (전략 vs Buy&Hold)
    buy_hold = prices / prices.iloc[0] * result.equity_curve.iloc[0]
    axes[1].plot(result.equity_curve.index, result.equity_curve.values,
                 color="#185FA5", label="Trailing Stop Strategy")
    axes[1].plot(buy_hold.index, buy_hold.values,
                 color="#999999", linestyle="--", label="Buy & Hold")
    axes[1].set_title("Equity Curve")
    axes[1].legend()
    axes[1].grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(out_path, dpi=130)
    plt.close(fig)


# ------------------------------------------------------------------
# 5. 사용자 입력 (대화형)
# ------------------------------------------------------------------
def get_user_input() -> dict:
    print("=== Trailing Stop-Loss / Rebuy 백테스트 ===")
    print("한국 종목/ETF는 '005930.KS'(코스피) / '091160.KQ'(코스닥) 형식,")
    print("미국 종목/ETF는 'AAPL', 'SPY' 등 그대로 입력하세요.")
    print("분석 가격 기준: 해당일 종가(수정주가)\n")

    ticker = input("티커: ").strip().upper()
    start = input("시작일 (YYYY-MM-DD): ").strip()

    end = input("종료일 (YYYY-MM-DD, 미입력시 최근일까지): ").strip()
    end = end if end else None

    stop_pct = float(input("손절 % (고점 대비 하락률, 예: 10 -> -10%): ").strip()) / 100
    rebuy_pct = float(input("재매수 % (저점 대비 상승률, 예: 5 -> +5%): ").strip()) / 100

    capital_in = input("초기 투자금 (미입력시 10,000,000): ").strip()
    capital = float(capital_in) if capital_in else 10_000_000

    return dict(ticker=ticker, start=start, end=end,
                stop_pct=stop_pct, rebuy_pct=rebuy_pct, capital=capital)


# ------------------------------------------------------------------
# 6. 실행
# ------------------------------------------------------------------
def main():
    cfg = get_user_input()

    prices = fetch_prices(cfg["ticker"], cfg["start"], cfg["end"])
    result = run_trailing_stop_backtest(prices, cfg["stop_pct"], cfg["rebuy_pct"], cfg["capital"])
    buy_hold = prices / prices.iloc[0] * cfg["capital"]
    metrics = compute_metrics(result.equity_curve, buy_hold)

    print("\n" + "=" * 55)
    print(f"{cfg['ticker']}  |  Stop {cfg['stop_pct']:.1%}  /  Rebuy {cfg['rebuy_pct']:.1%}  "
          f"|  기간 {prices.index[0].date()} ~ {prices.index[-1].date()}")
    print("=" * 55)
    for k, v in metrics.items():
        if "Value" in k:
            print(f"{k:<24}: {v:,.0f}")
        else:
            print(f"{k:<24}: {v:.2%}" if isinstance(v, float) else f"{k:<24}: {v}")
    print(f"\n총 매매 횟수: {len(result.trades)}건")

    out_path = "result.png"
    result.trades_df().to_csv("trades.csv", index=False)
    plot_results(prices, result, cfg["ticker"], out_path)
    print(f"\n[저장 완료] trades.csv, {out_path}")


if __name__ == "__main__":
    main()
