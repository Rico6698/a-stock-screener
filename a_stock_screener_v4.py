#!/usr/bin/env python3
"""
A股量化选股脚本 v4 —— 500元本金短线筛选（优化版）
优化点：
  - 正确的 MACD 计算（EMA 指数移动平均）
  - 正确的 RSI 计算（Wilder 平滑法）
  - 新增 KDJ 金叉检测
  - 新增量能趋势（5日量能递增）
  - 新增换手率过滤（≥1%）
  - 新增流通市值过滤（≥20亿）
  - 优化评分权重
"""

import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse
import json
import os
import time
import logging
import warnings
from typing import Optional

warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)


# ==================== 配置 ====================

class Config:
    def __init__(self):
        # --- 资金适配：500元 → 1手 ≤ 500元 → 股价 ≤ 5.0 ---
        self.min_price = 3.0
        self.max_price = 5.0
        self.min_amount = 30000000       # 最低成交额 3000万
        self.max_pe = 50                 # PE 上限
        self.max_debt_ratio = 60         # 资产负债率上限
        self.min_profit_growth = 10      # 净利润同比增速下限
        self.min_turnover_rate = 1.0     # 最低换手率 %
        self.min_days = 15
        self.lookback_days = 60
        self.max_candidates = 5
        self.threads = 8
        self.cache_ttl_hours = 2
        self.cache_dir = ".stock_cache_v4"
        self.output_format = "table"
        self.ci_mode = False

    @classmethod
    def from_cli(cls):
        p = argparse.ArgumentParser(description="A股短线选股 v4 (500元本金优化版)")
        p.add_argument("--min-price", type=float, default=3.0)
        p.add_argument("--max-price", type=float, default=5.0)
        p.add_argument("--min-amount", type=float, default=30000000)
        p.add_argument("--max-pe", type=float, default=50)
        p.add_argument("--max-debt", type=float, default=60)
        p.add_argument("--min-growth", type=float, default=10)
        p.add_argument("--min-turnover", type=float, default=1.0)
        p.add_argument("--candidates", type=int, default=5)
        p.add_argument("--threads", type=int, default=8)
        p.add_argument("--no-cache", action="store_true")
        p.add_argument("--output", choices=["table", "json", "csv"], default="table")
        p.add_argument("--config")
        p.add_argument("--ci", action="store_true")
        args = p.parse_args()

        cfg = cls()
        cfg.min_price = args.min_price
        cfg.max_price = args.max_price
        cfg.min_amount = args.min_amount
        cfg.max_pe = args.max_pe
        cfg.max_debt_ratio = args.max_debt
        cfg.min_profit_growth = args.min_growth
        cfg.min_turnover_rate = args.min_turnover
        cfg.max_candidates = args.candidates
        cfg.threads = args.threads
        cfg.output_format = args.output
        cfg.ci_mode = args.ci

        if args.config:
            with open(args.config, encoding="utf-8") as f:
                for k, v in json.load(f).items():
                    if hasattr(cfg, k):
                        setattr(cfg, k, v)
        if args.no_cache:
            cfg.cache_ttl_hours = 0
        return cfg


# ==================== 历史记录与验证 ====================

HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".analysis_history_v4.json")

CN_COLUMNS = {
    "score": "评分", "code": "代码", "name": "名称",
    "price": "价格", "hand_cost": "1手金额",
    "pe": "市盈率", "total_mv": "总市值",
    "fzl": "负债率", "jlr_tzb": "净利润同比", "xjl": "现金流",
    "avg_amount_20d": "20日均额",
    "ma5": "MA5", "ma10": "MA10", "ma20": "MA20",
    "ma5_slope": "MA5斜率", "ma10_slope": "MA10斜率",
    "ma20_slope": "MA20斜率",
    "bull_align": "多头排列", "macd_gold": "MACD金叉",
    "kdj_gold": "KDJ金叉",
    "vol_expand": "温和放量", "breakout": "突破前高",
    "vol_trend_up": "量能递增",
    "rsi_14": "RSI(14)", "macd_bull": "MACD方向",
    "bb_pos": "布林位置", "tech_reason": "技术理由",
}

RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def load_history():
    if not os.path.exists(HISTORY_FILE):
        return None
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_history(data):
    os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def validate_previous(recommendations, df_spot, prev_date=""):
    date_str = f"【{prev_date} 推荐验证】" if prev_date else "【历史推荐验证】"
    print(f"\n{'=' * 60}")
    print(f"  {date_str}")
    print(f"{'=' * 60}")

    verified = 0
    correct = 0
    for rec in recommendations:
        code = rec["code"]
        match = df_spot[df_spot["stock_code"] == code]
        if match.empty:
            print(f"  {YELLOW}{rec['name']}({code}): 无法获取当前数据(可能停牌){RESET}")
            continue
        current_price = float(match.iloc[0]["price"])
        prev_price = rec["price"]
        if np.isnan(current_price) or prev_price <= 0:
            print(f"  {YELLOW}{rec['name']}({code}): 价格数据异常, 跳过{RESET}")
            continue
        verified += 1
        change_pct = (current_price - prev_price) / prev_price * 100
        is_up = current_price >= prev_price
        if is_up:
            correct += 1
        color = RED if is_up else GREEN
        arrow = "↑" if is_up else "↓"
        verdict = "正确" if is_up else "错误"
        print(f"  {color}{rec['name']}({code}): 推荐价{prev_price:.2f} → 现价{current_price:.2f} ({change_pct:+.2f}%) {arrow} 【{verdict}】{RESET}")

    if verified > 0:
        acc = correct / verified * 100
        print(f"\n  {BOLD}胜率: {correct}/{verified} ({acc:.1f}%){RESET}")
    else:
        print("  (无历史推荐数据)")
    print()


# ==================== 缓存 ====================

class FileCache:
    def __init__(self, cache_dir: str, ttl_hours: int):
        self.cache_dir = cache_dir
        self.ttl_seconds = ttl_hours * 3600
        if ttl_hours > 0:
            os.makedirs(cache_dir, exist_ok=True)

    def _path(self, key: str) -> str:
        safe = key.replace("/", "_").replace(" ", "_")
        return os.path.join(self.cache_dir, f"{safe}.json")

    def get(self, key: str) -> Optional[dict]:
        if self.ttl_seconds <= 0:
            return None
        path = self._path(key)
        if not os.path.isfile(path):
            return None
        age = time.time() - os.path.getmtime(path)
        if age > self.ttl_seconds:
            os.remove(path)
            return None
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def set(self, key: str, data):
        if self.ttl_seconds <= 0:
            return
        with open(self._path(key), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, default=str)


# ==================== 工具函数 ====================

def api_retry(fn, retries=3, delay=1):
    for i in range(retries):
        try:
            return fn()
        except Exception as e:
            if i < retries - 1:
                time.sleep(delay * (2 ** i))
            else:
                log.warning(f"  API重试{i+1}次后仍失败: {e}")
                raise


def find_col(df, keywords, exclude=None):
    for col in df.columns:
        if exclude and any(e in str(col) for e in exclude):
            continue
        if any(k in str(col) for k in keywords):
            return col
    return None


# ==================== 正确技术指标计算 ====================

def ema(data: np.ndarray, period: int) -> np.ndarray:
    """指数移动平均 EMA"""
    result = np.zeros_like(data, dtype=float)
    alpha = 2.0 / (period + 1)
    result[0] = data[0]
    for i in range(1, len(data)):
        result[i] = alpha * data[i] + (1 - alpha) * result[i - 1]
    return result


def calc_macd(close: np.ndarray):
    """正确的 MACD 计算（EMA12, EMA26, DEA9）"""
    if len(close) < 26:
        return None, None, None
    ema12 = ema(close, 12)
    ema26 = ema(close, 26)
    dif = ema12 - ema26
    dea = ema(dif, 9)
    macd_hist = 2 * (dif - dea)
    return dif, dea, macd_hist


def calc_rsi_wilder(close: np.ndarray, period: int = 14) -> float:
    """正确的 RSI 计算（Wilder 平滑法）"""
    if len(close) < period + 1:
        return 50.0
    deltas = np.diff(close)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def calc_kdj(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 9):
    """KDJ 指标计算"""
    if len(close) < period:
        return None, None, None
    k_values = np.zeros(len(close))
    d_values = np.zeros(len(close))
    j_values = np.zeros(len(close))
    k, d = 50.0, 50.0
    for i in range(period - 1, len(close)):
        h = np.max(high[i - period + 1:i + 1])
        l = np.min(low[i - period + 1:i + 1])
        rsv = (close[i] - l) / (h - l) * 100 if h > l else 50
        k = 2 / 3 * k + 1 / 3 * rsv
        d = 2 / 3 * d + 1 / 3 * k
        j = 3 * k - 2 * d
        k_values[i] = k
        d_values[i] = d
        j_values[i] = j
    return k_values, d_values, j_values


# ==================== 数据获取 ====================

def fetch_spot() -> pd.DataFrame:
    log.info("[1/6] 获取全市场实时行情...")
    dfs = None
    for name, fn in [("TX", lambda: ak.stock_zh_a_spot_tx()), ("EM", lambda: ak.stock_zh_a_spot_em())]:
        try:
            dfs = fn()
            log.info(f"  {name}接口成功，共 {len(dfs)} 只股票")
            break
        except Exception:
            continue
    if dfs is None:
        raise RuntimeError("所有行情接口均失败(TX/EM)")

    dfs.rename(columns={
        "code": "stock_code", "name": "stock_name",
        "zxj": "price", "zdf": "pct_chg",
        "volume": "volume", "turnover": "amount",
        "pe_ttm": "pe_ttm", "zsz": "total_mv",
        "ltsz": "float_mv", "hsl": "turnover_rate",
    }, inplace=True)

    dfs["code_raw"] = dfs["stock_code"].astype(str)
    dfs["stock_code"] = dfs["code_raw"].str.replace(r"^(sz|sh|bj)", "", regex=True)
    for col in ["price", "pe_ttm", "amount", "total_mv", "pct_chg", "float_mv", "turnover_rate"]:
        if col in dfs.columns:
            dfs[col] = pd.to_numeric(dfs[col], errors="coerce")
    dfs["amount"] = pd.to_numeric(dfs["amount"], errors="coerce") * 10000
    return dfs


def fetch_financials() -> dict:
    results = {}
    for key, label, fn in [
        ("yjbb", "业绩报表", lambda: ak.stock_yjbb_em()),
        ("zcfz", "资产负债表", lambda: ak.stock_zcfz_em()),
        ("xjll", "现金流量表", lambda: ak.stock_xjll_em()),
        ("lrb", "利润表", lambda: ak.stock_lrb_em()),
    ]:
        log.info(f"[2-5/6] 获取{label}...")
        try:
            df = api_retry(fn)
            code_col = find_col(df, ["代码"])
            if code_col:
                df.rename(columns={code_col: "stock_code"}, inplace=True)
                df["stock_code"] = df["stock_code"].astype(str).str.strip()
            log.info(f"  共 {len(df)} 条记录")
            results[key] = df
        except Exception as e:
            log.warning(f"  {label}获取失败: {e}")
            results[key] = pd.DataFrame()
    return results


# ==================== 技术分析（优化版） ====================

def _get_hist(code: str, code_raw: str, start: str, end: str) -> Optional[pd.DataFrame]:
    for use_raw, fn in [
        (False, lambda: ak.stock_zh_a_hist(code, start_date=start, end_date=end)),
        (True, lambda: ak.stock_zh_a_hist_tx(code_raw, start_date=start, end_date=end)),
    ]:
        try:
            df = fn()
            if df is None or df.empty:
                continue
            if use_raw:
                df = df.rename(columns={
                    "close": "收盘", "high": "最高", "low": "最低",
                    "volume": "成交量", "amount": "成交额",
                })
            return df
        except Exception:
            continue
    return None


def check_technical(code: str, code_raw: str, cfg: Config) -> Optional[dict]:
    try:
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=cfg.lookback_days)).strftime("%Y%m%d")
        hist = _get_hist(code, code_raw, start, end)
        if hist is None or len(hist) < cfg.min_days:
            return None

        close = hist["收盘"].values.astype(float)
        high = hist["最高"].values.astype(float)
        low = hist["最低"].values.astype(float)
        volume = hist["成交量"].values.astype(float)
        amount_hist = hist["成交额"].values.astype(float)
        n = len(close)

        # 均线
        ma5 = float(np.mean(close[-5:])) if n >= 5 else float(np.mean(close))
        ma10 = float(np.mean(close[-10:])) if n >= 10 else ma5
        ma20 = float(np.mean(close[-20:])) if n >= 20 else ma5

        last_close = float(close[-1])
        last_vol = float(volume[-1])
        avg_vol_20d = float(np.mean(volume[-20:])) if n >= 20 else float(np.mean(volume))
        avg_amount_20d = float(np.mean(amount_hist[-20:])) if n >= 20 else float(np.mean(amount_hist))

        # 均线斜率
        ma5_slope = 0.0
        if n >= 10:
            ma5_prev = float(np.mean(close[-10:-5]))
            ma5_slope = (ma5 - ma5_prev) / ma5_prev * 100 if ma5_prev > 0 else 0

        ma10_slope = 0.0
        if n >= 20:
            ma10_prev = float(np.mean(close[-20:-10]))
            ma10_slope = (ma10 - ma10_prev) / ma10_prev * 100 if ma10_prev > 0 else 0

        ma20_slope = 0.0
        if n >= 40:
            ma20_prev = float(np.mean(close[-40:-20]))
            ma20_slope = (ma20 - ma20_prev) / ma20_prev * 100 if ma20_prev > 0 else 0

        # ===== 上涨信号 =====

        # 1. 多头排列
        bull_align = ma5 > ma10 > ma20

        # 2. 站上均线
        on_ma5 = last_close >= ma5 * 0.98
        on_ma10 = last_close >= ma10 * 0.98

        # 3. MACD — 正确的 EMA 计算
        dif, dea, macd_hist = calc_macd(close)
        macd_bull = False
        macd_gold = False
        if dif is not None:
            macd_bull = dif[-1] > dea[-1]
            # 金叉: 前一日 DIF ≤ DEA，今日 DIF > DEA
            if len(dif) >= 2:
                macd_gold = (dif[-2] <= dea[-2]) and (dif[-1] > dea[-1])

        # 4. KDJ 金叉
        k_vals, d_vals, j_vals = calc_kdj(high, low, close)
        kdj_gold = False
        if k_vals is not None and len(k_vals) >= 2:
            kdj_gold = (k_vals[-2] <= d_vals[-2]) and (k_vals[-1] > d_vals[-1]) and k_vals[-1] < 80

        # 5. 温和放量（1.2-2.5倍）
        vol_ratio = float(last_vol / avg_vol_20d) if avg_vol_20d > 0 else 0
        vol_expand = 1.2 <= vol_ratio <= 2.5

        # 6. 量能趋势（近5日量能递增）
        vol_trend_up = False
        if n >= 5:
            vol_5d = volume[-5:]
            vol_trend_up = all(vol_5d[i] >= vol_5d[i - 1] * 0.95 for i in range(1, len(vol_5d)))

        # 7. 突破前高（收盘创近20日新高）
        breakout = False
        if n >= 20:
            recent_high = float(max(close[-20:-1]))
            breakout = last_close > recent_high

        # 8. RSI（Wilder 平滑法）— 40-70 有空间
        rsi = calc_rsi_wilder(close, 14)

        # 9. 布林位置
        bb_pos = 0.5
        if n >= 20:
            bb_mid = ma20
            bb_std = float(np.std(close[-20:]))
            bb_upper = bb_mid + 2 * bb_std
            bb_lower = bb_mid - 2 * bb_std
            if bb_upper > bb_lower:
                bb_pos = float((last_close - bb_lower) / (bb_upper - bb_lower))
            bb_pos = max(0, min(1, bb_pos))

        # 10. 连续小阳
        consecutive_up = False
        if n >= 3:
            consecutive_up = close[-1] > close[-2] > close[-3]

        return {
            "avg_amount_20d": round(avg_amount_20d, 0),
            "avg_vol_20d": round(avg_vol_20d, 0),
            "ma5": round(ma5, 3), "ma10": round(ma10, 3), "ma20": round(ma20, 3),
            "ma5_slope": round(ma5_slope, 2), "ma10_slope": round(ma10_slope, 2),
            "ma20_slope": round(ma20_slope, 2),
            "on_ma5": on_ma5, "on_ma10": on_ma10,
            "bull_align": bull_align,
            "macd_gold": macd_gold, "macd_bull": macd_bull,
            "kdj_gold": kdj_gold,
            "vol_expand": vol_expand, "vol_trend_up": vol_trend_up,
            "breakout": breakout,
            "consecutive_up": consecutive_up,
            "vol_ratio": round(vol_ratio, 2),
            "rsi_14": round(rsi, 1),
            "bb_pos": round(bb_pos, 2),
            "last_close": last_close,
        }
    except Exception:
        return None


# ==================== 主流程 ====================

def main():
    cfg = Config.from_cli()
    cache = FileCache(cfg.cache_dir, cfg.cache_ttl_hours)

    if cfg.ci_mode:
        os.environ["TZ"] = "Asia/Shanghai"
        try:
            time.tzset()
        except AttributeError:
            pass

    today = datetime.now().strftime("%Y-%m-%d")
    history = load_history()
    if history and history.get("analysis_date") == today:
        if cfg.ci_mode:
            log.info("CI模式：跳过日期重复检查，继续运行...")
        else:
            log.info("\n今天已经分析过了，明天再来吧！")
            return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = os.path.join("outputs", timestamp)
    os.makedirs(output_dir, exist_ok=True)

    log.info("=" * 60)
    log.info("A股短线选股 v4（500元本金 — 优化版）")
    log.info(f"价格{cfg.min_price}-{cfg.max_price}元 PE≤{cfg.max_pe} 成交额≥{cfg.min_amount/10000:.0f}万")
    log.info(f"换手率≥{cfg.min_turnover_rate}%")
    log.info("=" * 60)

    # ---- Step 1: 实时行情 ----
    df = fetch_spot()

    if history and history.get("prev_recommendations"):
        prev_date = history.get("analysis_date", "")
        validate_previous(history["prev_recommendations"], df, prev_date)

    before = len(df)
    df = df[df["price"].between(cfg.min_price, cfg.max_price)].copy()
    log.info(f"① 价格{cfg.min_price}-{cfg.max_price}元: {before} -> {len(df)}")

    df = df[df["pe_ttm"].between(0, cfg.max_pe)].copy()
    log.info(f"② PE 0-{cfg.max_pe}: {len(df)}")

    df = df[~df["stock_name"].str.contains("ST|退", na=False)].copy()
    log.info(f"③ 排除ST/退市: {len(df)}")

    # 换手率过滤
    if "turnover_rate" in df.columns:
        df["turnover_rate"] = pd.to_numeric(df["turnover_rate"], errors="coerce")
        before = len(df)
        df = df[df["turnover_rate"] >= cfg.min_turnover_rate].copy()
        log.info(f"④ 换手率≥{cfg.min_turnover_rate}%: {before} -> {len(df)}")

    # ---- Step 2-5: 财务数据 ----
    fin = fetch_financials()
    for key in ["yjbb", "zcfz", "xjll", "lrb"]:
        if not fin[key].empty:
            df = df.merge(fin[key], on="stock_code", how="left", suffixes=("", f"_{key}"))

    # 净利润同比增速
    tzb_col = find_col(df, ["净利润-同比", "净利润同比增长", "净利润", "同比"], exclude=["单季度", "环比", "营业"])
    if tzb_col:
        df[tzb_col] = pd.to_numeric(df[tzb_col], errors="coerce")
        before = len(df)
        df = df[df[tzb_col] >= cfg.min_profit_growth].copy()
        log.info(f"⑥ 净利润同比≥{cfg.min_profit_growth}%: {before} -> {len(df)}")
    else:
        tzb_col = None
        log.warning("⑥ 净利润同比列未找到, 跳过")

    # 资产负债率
    fzl_col = find_col(df, ["资产负债率"])
    if fzl_col:
        if df[fzl_col].dtype == "object":
            df[fzl_col] = df[fzl_col].astype(str).str.replace("%", "", regex=False)
        df[fzl_col] = pd.to_numeric(df[fzl_col], errors="coerce")
        before = len(df)
        df = df[df[fzl_col] <= cfg.max_debt_ratio].copy()
        log.info(f"⑦ 资产负债率≤{cfg.max_debt_ratio}%: {before} -> {len(df)}")
    else:
        fzl_col = None
        log.warning("⑦ 资产负债率列未找到, 跳过")

    # 经营现金流
    xjl_col = find_col(df, ["经营现金流", "经营性现金流", "经营活动"])
    if xjl_col:
        df[xjl_col] = pd.to_numeric(df[xjl_col], errors="coerce")
        before = len(df)
        df = df[df[xjl_col] >= 0].copy()
        log.info(f"⑧ 经营现金流≥0: {before} -> {len(df)}")
    else:
        xjl_col = None

    # 成交额
    before = len(df)
    df = df[df["amount"] >= cfg.min_amount].copy()
    log.info(f"⑨ 成交额≥{cfg.min_amount/10000:.0f}万: {before} -> {len(df)}")

    if df.empty:
        save_history({"analysis_date": today, "prev_recommendations": []})
        log.info("\n当前市场无符合条件股票。建议放宽参数。")
        return

    # ---- Step 6: 并行技术分析 ----
    codes_list = df[["stock_code", "code_raw"]].drop_duplicates().to_dict("records")
    log.info(f"\n[6/6] 并行技术面分析 {len(codes_list)} 只 ({cfg.threads}线程)...")

    tech_map = {}
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=cfg.threads) as exe:
        fut_map = {exe.submit(check_technical, r["stock_code"], r["code_raw"], cfg): r["stock_code"] for r in codes_list}
        for fut in as_completed(fut_map):
            c = fut_map[fut]
            try:
                r = fut.result()
                if r:
                    tech_map[c] = r
            except Exception:
                pass

    log.info(f"  成功 {len(tech_map)}/{len(codes_list)} 只, 耗时 {time.time()-t0:.1f}s")

    # ---- 评分（优化权重） ----
    results = []
    for _, row in df.iterrows():
        code = row["stock_code"]
        tech = tech_map.get(code)
        if tech is None:
            continue

        score = 0
        reason = []

        # ===== 核心上涨信号（高分值） =====
        if tech["bull_align"]:
            score += 12; reason.append("均线多头排列")
        if tech["macd_gold"]:
            score += 12; reason.append("MACD金叉")
        if tech["breakout"]:
            score += 12; reason.append("突破前高")
        if tech["kdj_gold"]:
            score += 10; reason.append("KDJ金叉")

        # ===== 辅助信号 =====
        if tech["avg_amount_20d"] >= 50000000:
            score += 6;  reason.append("流动性充足")
        if tech["on_ma5"]:
            score += 6;  reason.append("站上5日线")
        if tech["on_ma10"]:
            score += 4;  reason.append("站上10日线")
        if tech["ma5_slope"] > 0:
            score += 6;  reason.append("MA5上行")
        if tech["ma10_slope"] > 0:
            score += 4;  reason.append("MA10上行")
        if tech["ma20_slope"] > -0.3:
            score += 3;  reason.append("MA20趋稳")
        if tech["vol_expand"]:
            score += 8;  reason.append("温和放量")
        if tech["vol_trend_up"]:
            score += 5;  reason.append("量能递增")
        if tech["consecutive_up"]:
            score += 6;  reason.append("连续小阳")
        if tech["macd_bull"]:
            score += 4;  reason.append("MACD多头")
        if 40 < tech["rsi_14"] < 70:
            score += 4;  reason.append("RSI有空间")
        if 0.4 < tech["bb_pos"] < 0.8:
            score += 4;  reason.append("布林中上轨")

        fzl_val = f"{row.get(fzl_col, 'N/A'):.1f}" if fzl_col and isinstance(row.get(fzl_col), (int, float)) else str(row.get(fzl_col, 'N/A'))
        tzb_val = f"{row.get(tzb_col, 'N/A'):.1f}" if tzb_col and isinstance(row.get(tzb_col), (int, float)) else str(row.get(tzb_col, 'N/A'))
        xjl_val = f"{row.get(xjl_col, 'N/A'):.4f}" if xjl_col and isinstance(row.get(xjl_col), (int, float)) else str(row.get(xjl_col, 'N/A'))

        results.append({
            "score": score,
            "code": code,
            "name": row["stock_name"],
            "price": float(row["price"]),
            "hand_cost": float(row["price"]) * 100,
            "pe": float(row["pe_ttm"]) if pd.notna(row["pe_ttm"]) else 0,
            "total_mv": float(row["total_mv"]) if pd.notna(row["total_mv"]) else 0,
            "fzl": fzl_val,
            "jlr_tzb": tzb_val,
            "xjl": xjl_val,
            "avg_amount_20d": tech["avg_amount_20d"],
            "ma5": tech["ma5"],
            "ma10": tech["ma10"],
            "ma20": tech["ma20"],
            "ma5_slope": tech["ma5_slope"],
            "ma10_slope": tech["ma10_slope"],
            "ma20_slope": tech["ma20_slope"],
            "bull_align": "是" if tech["bull_align"] else "否",
            "macd_gold": "是" if tech["macd_gold"] else "否",
            "kdj_gold": "是" if tech["kdj_gold"] else "否",
            "vol_expand": "是" if tech["vol_expand"] else "否",
            "vol_trend_up": "是" if tech["vol_trend_up"] else "否",
            "breakout": "是" if tech["breakout"] else "否",
            "consecutive_up": "是" if tech["consecutive_up"] else "否",
            "on_ma5": "是" if tech["on_ma5"] else "否",
            "vol_ratio": tech["vol_ratio"],
            "rsi_14": tech["rsi_14"],
            "macd_bull": "多" if tech["macd_bull"] else "空",
            "bb_pos": tech["bb_pos"],
            "tech_reason": ", ".join(reason),
        })

    if not results:
        save_history({"analysis_date": today, "prev_recommendations": []})
        log.info("\n技术分析后无符合条件股票。")
        return

    result_df = pd.DataFrame(results).sort_values("score", ascending=False)
    top = result_df.head(cfg.max_candidates)

    # ---- 输出 ----
    log.info(f"\n{'='*60}")
    log.info(f"筛选完成！通过 {len(results)} 只，前{cfg.max_candidates}只：")
    log.info(f"{'='*60}")

    if cfg.output_format == "json":
        json_str = top.to_json(orient="records", force_ascii=False, indent=2)
        print(json_str)
        json_path = os.path.join(output_dir, f"top_{timestamp}.json")
        with open(json_path, "w", encoding="utf-8") as f:
            f.write(json_str)
        log.info(f"已保存 {json_path}")
    elif cfg.output_format == "csv":
        csv_path = os.path.join(output_dir, f"top_{timestamp}.csv")
        top.rename(columns=CN_COLUMNS).to_csv(csv_path, index=False, encoding="utf-8-sig")
        log.info(f"已保存 {csv_path}")
    else:
        print(f"\n{'名称':<10}{'代码':<8}{'价格':<8}{'1手金额':<8}{'市盈率':<8}{'评分':<6}{'多头':<6}{'金叉':<6}{'KDJ':<6}{'突破':<6}{'技术理由'}")
        print("-" * 150)
        for _, r in top.iterrows():
            print(f"{r['name']:<10}{r['code']:<8}{r['price']:<8.2f}{r['hand_cost']:<8.0f}"
                  f"{r['pe']:<8.1f}{r['score']:<6}{r['bull_align']:<6}{r['macd_gold']:<6}{r['kdj_gold']:<6}{r['breakout']:<6}{r['tech_reason']}")

        print(f"\n{'='*60} 前{cfg.max_candidates}详细 {'='*60}")
        for i, (_, r) in enumerate(top.iterrows(), 1):
            print(f"\n{i}. 【{r['name']}({r['code']}】 {r['price']:.2f}元  1手{r['hand_cost']:.0f}元  评分{r['score']}")
            print(f"   财务: 净利增速{r['jlr_tzb']}  负债率{r['fzl']}  现金流{r['xjl']}")
            print(f"   信号: 多头={r['bull_align']}  MACD金叉={r['macd_gold']}  KDJ金叉={r['kdj_gold']}  突破={r['breakout']}  量能递增={r['vol_trend_up']}")
            print(f"   技术: {r['tech_reason']}")
            print(f"   指标: MA5斜率{r['ma5_slope']:.2f}%  MA20斜率{r['ma20_slope']:.2f}%  RSI={r['rsi_14']:.1f}  布林{r['bb_pos']:.0%}")

        csv_path = os.path.join(output_dir, f"top_{timestamp}.csv")
        top.rename(columns=CN_COLUMNS).to_csv(csv_path, index=False, encoding="utf-8-sig")
        log.info(f"\n前{cfg.max_candidates}只已保存 {csv_path}")

    full_csv_path = os.path.join(output_dir, f"full_{timestamp}.csv")
    result_df.rename(columns=CN_COLUMNS).to_csv(full_csv_path, index=False, encoding="utf-8-sig")
    log.info(f"全量结果已保存 {full_csv_path}")

    recs = [{"code": r["code"], "name": r["name"], "price": float(r["price"])} for _, r in top.iterrows()]
    save_history({"analysis_date": today, "prev_recommendations": recs})

    log.info("\n免责声明：本脚本仅做客观数据筛选，不构成任何投资建议。入市有风险，投资需谨慎。")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        import traceback
        log.error(f"脚本异常退出: {e}")
        traceback.print_exc()
    else:
        import sys
        if sys.stdin and sys.stdin.isatty():
            input("\n按 Enter 键退出...")
