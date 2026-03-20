from __future__ import annotations

import glob
import os
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests
from tqdm import tqdm

from .cache_fallback import try_cache_fallback_for_failed_tickers


def _load_historical_sp500_constituent_events(cfg: Any, force_refresh: bool = False) -> pd.DataFrame:
    """
    Load FMP historical S&P500 constituent events with a parquet cache.

    Cache path (Mac/Windows compatible):
        {cfg.fmp_cache_root}/universe/sp500_constituent_events.parquet
    """
    if not hasattr(cfg, "fmp_cache_root"):
        raise RuntimeError("Config에 fmp_cache_root 속성이 없습니다.")

    universe_dir = os.path.join(cfg.fmp_cache_root, "universe")
    cache_path = os.path.join(universe_dir, "sp500_constituent_events.parquet")
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)

    # 1) 캐시 우선 사용 (force_refresh가 아닌 경우)
    if not force_refresh and os.path.exists(cache_path):
        try:
            df_cached = pd.read_parquet(cache_path)
            if isinstance(df_cached, pd.DataFrame) and not df_cached.empty:
                return df_cached
        except Exception:
            # 캐시가 깨졌으면 조용히 무시하고 새로 다운로드
            pass

    # 2) API에서 새로 다운로드
    api_key = getattr(cfg, "fmp_api_key", None) or getattr(cfg, "fmp_apikey", None)
    if not api_key:
        raise RuntimeError("Config에 FMP API 키가 없습니다. (fmp_api_key 또는 fmp_apikey)")

    base_url = "https://financialmodelingprep.com/api/v4/historical/sp500_constituent"

    try:
        resp = requests.get(base_url, params={"apikey": api_key}, timeout=60)
    except Exception as e:
        raise RuntimeError(f"FMP historical S&P500 constituent 요청 실패: {type(e).__name__}: {e}") from e

    try:
        resp.raise_for_status()
    except Exception as e:
        # 응답 바디 앞부분을 함께 포함
        text_head = (resp.text or "")[:500]
        raise RuntimeError(
            f"FMP historical S&P500 constituent HTTP 오류: {type(e).__name__}: {e} "
            f"status={resp.status_code} body_head={text_head!r}"
        ) from e

    payload = resp.json()

    # 3) 응답 검증
    if isinstance(payload, dict):
        msg = payload.get("Error") or payload.get("error") or str(payload)
        raise RuntimeError(f"FMP historical S&P500 constituent API 에러 응답: {msg}")
    if not isinstance(payload, list):
        raise RuntimeError(
            f"FMP historical S&P500 constituent 응답 타입이 list가 아닙니다: {type(payload)}"
        )

    rows: list[dict[str, Any]] = []
    for row in payload:
        if not isinstance(row, dict):
            continue

        symbol_raw = row.get("symbol") or row.get("Symbol")
        action_raw = row.get("action") or row.get("Action")
        date_raw = (
            row.get("date")
            or row.get("dateAdded")
            or row.get("dateRemoved")
            or row.get("Date")
        )

        symbol = str(symbol_raw or "").strip().upper()
        action = str(action_raw or "").strip().upper()
        if not symbol or not action or not date_raw:
            continue

        dt = pd.to_datetime(date_raw, errors="coerce")
        if pd.isna(dt):
            continue
        # tz-aware 이면 tz-naive 로 변환
        if getattr(dt, "tzinfo", None) is not None:
            dt = dt.tz_localize(None)

        rows.append(
            {
                "symbol": symbol,
                "action": action,
                "date": dt,
                "source": "FMP_V4_HIST_SP500_CONST",
            }
        )

    if not rows:
        raise RuntimeError("FMP historical S&P500 constituent 이벤트를 한 건도 받지 못했습니다.")

    df = pd.DataFrame(rows)
    df = df.sort_values("date").reset_index(drop=True)

    # 4) 캐시로 저장
    df.to_parquet(cache_path, index=False)

    return df


def _setup_pipeline_environment(ctx: Any, cfg: Any) -> None:
    import platform

    if platform.system() == "Windows" and (cfg.fmp_cache_root.startswith("/") or "Users" in cfg.fmp_cache_root):
        cfg.fmp_cache_root, cfg.save_dir = ctx._FMP_CACHE_ROOT, ctx._OUTPUT_DIR

    os.makedirs(cfg.fmp_cache_root, exist_ok=True)
    os.makedirs(ctx._fmp_ohlcv_dir(cfg), exist_ok=True)
    os.makedirs(ctx._fmp_fin_dir(cfg), exist_ok=True)
    os.makedirs(ctx._fmp_mcap_dir(cfg), exist_ok=True)
    os.makedirs(ctx._fmp_mcap_reconstructed_dir(cfg), exist_ok=True)
    os.makedirs(cfg.save_dir, exist_ok=True)


def _print_pipeline_header(ctx: Any, cfg: Any, tickers: List[str], source: str, start: datetime, end_eff: datetime) -> None:
    print(f"[Tickers] count={len(tickers)} source={source}")
    print(f"[Factors] tech={len(ctx.TECH_INDICATOR_NAMES)} fund={len(ctx.FUND_FACTOR_NAMES)} total={len(ctx.INDICATOR_NAMES)}")
    print(f"[FMP Cache] root={cfg.fmp_cache_root}")
    print(f"[FMP Cache] ohlcv_dir={ctx._fmp_ohlcv_dir(cfg)} fin_dir={ctx._fmp_fin_dir(cfg)} mcap_dir={ctx._fmp_mcap_dir(cfg)}")
    print(f"[FMP Cache] mcap_reconstructed_dir={ctx._fmp_mcap_reconstructed_dir(cfg)}")
    print(f"[Output] save_dir={cfg.save_dir}")
    print(f"[Range] {start.strftime('%Y-%m-%d')} ~ {end_eff.strftime('%Y-%m-%d')} policy={cfg.ohlcv_policy}")
    print(f"[Fundamentals] {'ON' if cfg.enable_fundamentals else 'OFF'} lag_days={cfg.report_lag_days}")

    print(
        "[Scoring Regime Mode] "
        f"enabled={cfg.enable_regime_specific_weights} "
        f"mode={cfg.scoring_regime_mode} "
        f"names={cfg.scoring_regime_names}"
    )
    print(
        "[RegimeSpecific Weights] "
        f"BULL={cfg.regime_weight_bull:.2f} "
        f"SIDE={cfg.regime_weight_side:.2f} "
        f"DEFENSIVE={cfg.regime_weight_defensive:.2f}"
    )
    print(
        "[Regime Factor Specialization] "
        f"enabled={getattr(cfg, 'enable_regime_factor_specialization', False)} "
        f"mode={getattr(cfg, 'regime_factor_specialization_mode', 'OFF')}"
    )
    print(f"[BULL Allowed Pool] {getattr(cfg, 'bull_allowed_factor_pool', tuple())}")
    print(f"[SIDE Allowed Pool] {getattr(cfg, 'side_allowed_factor_pool', tuple())}")
    print(f"[DEF Allowed Pool] {getattr(cfg, 'def_allowed_factor_pool', tuple())}")
    print(
        "[Side Soft Bias] "
        f"enabled={getattr(cfg, 'enable_side_soft_bias', False)} "
        f"pool={getattr(cfg, 'side_core_factor_pool', tuple())} "
        f"threshold={getattr(cfg, 'side_core_soft_threshold', 0)} "
        f"bonus={getattr(cfg, 'side_soft_bonus', 0.0):.4f} "
        f"penalty={getattr(cfg, 'side_soft_penalty', 0.0):.4f}"
    )
    print(
        "[Bull Soft Bias] "
        f"breadth_enabled={getattr(cfg, 'enable_bull_breadth_soft_bias', False)} "
        f"min_categories={getattr(cfg, 'bull_breadth_soft_min_categories', 0)} "
        f"breadth_bonus={getattr(cfg, 'bull_breadth_bonus', 0.0):.4f} "
        f"breadth_penalty={getattr(cfg, 'bull_breadth_penalty', 0.0):.4f} "
        f"breakout_enabled={getattr(cfg, 'enable_bull_breakout_presence_bonus', False)} "
        f"breakout_bonus={getattr(cfg, 'bull_breakout_presence_bonus', 0.0):.4f}"
    )
    print(f"[Alpha Control] ga_alpha_floor={getattr(cfg, 'ga_alpha_floor', 0.0):.2f}")
    print(
        "[Diversity Params] "
        f"min_k={cfg.min_k_used} "
        f"weight_cap={cfg.weight_cap:.2f} "
        f"conc_penalty={cfg.conc_penalty:.2f} "
        f"entropy_bonus={cfg.entropy_bonus:.2f}"
    )
    print(
        "[Investability Targets] "
        f"MeanIC={cfg.invest_target_mean_ic:.4f} "
        f"Spread={cfg.invest_target_spread:.4f} "
        f"PosICRatio={cfg.invest_target_positive_ic_ratio:.4f} "
        f"IC_BEAR={cfg.invest_target_ic_bear:.4f} "
        f"FactorCount={cfg.invest_target_factor_count}"
    )
    print(
        "[FactorCorrPenalty] "
        f"enabled={cfg.enable_factor_corr_penalty} "
        f"lambda={cfg.factor_corr_penalty_lambda:.4f} "
        f"min_samples={cfg.factor_corr_min_samples} "
        f"use_abs={cfg.factor_corr_use_abs}"
    )
    print(
        "[BullFloorPenalty] "
        f"enabled={cfg.enable_bull_floor_penalty} "
        f"min_ic1={cfg.bull_min_ic_1m:.4f} "
        f"min_ic3={cfg.bull_min_ic_3m:.4f} "
        f"min_spread={cfg.bull_min_spread_mix:.4f} "
        f"lam_ic={cfg.bull_penalty_lambda_ic:.4f} "
        f"lam_spread={cfg.bull_penalty_lambda_spread:.4f}"
    )
    print(
        "[BullSpreadBonus] "
        f"enabled={cfg.enable_bull_spread_bonus} "
        f"threshold={cfg.bull_spread_bonus_threshold:.4f} "
        f"lambda={cfg.bull_spread_bonus_lambda:.4f}"
    )
    print(
        "[BullFactorMinConstraint] "
        f"enabled={cfg.enable_bull_factor_min_constraint} "
        f"min_keep={cfg.bull_factor_min_keep} "
        f"pool={cfg.bull_factor_pool}"
    )
    print(
        "[Stability Layer] "
        f"enabled={cfg.enable_stability_layer} "
        f"seed_runs={cfg.stability_seed_runs} "
        f"top_n={cfg.stability_top_n_seeds} "
        f"threshold={cfg.stability_selection_threshold:.2f}"
    )
    print(
        "[Portfolio Construction] "
        f"enabled={cfg.enable_portfolio_construction} "
        f"top_n={cfg.portfolio_top_n} "
        f"hold_buffer_n={cfg.portfolio_hold_buffer_n} "
        f"weight_mode={cfg.portfolio_weight_mode} "
        f"softmax_temp={cfg.portfolio_softmax_temp:.2f} "
        f"max_weight_cap={cfg.portfolio_max_weight_cap:.2f}"
    )
    print(
        "[Meta Search] "
        f"enabled={cfg.enable_meta_search} "
        f"mode={cfg.meta_search_mode} "
        f"trials={cfg.meta_search_trials} "
        f"templates={cfg.meta_template_names} "
        f"tpl_perturb={cfg.meta_allow_template_perturbation} "
        f"tpl_trials={cfg.meta_template_trials_per_template} "
        f"rand_extra={cfg.meta_random_extra_trials} "
        f"top_n_refine={cfg.meta_top_n_refine}"
    )
    if not bool(cfg.enable_meta_search):
        print(f"[Meta Search] disabled_template={getattr(cfg, 'meta_disabled_template_name', 'TPL_BALANCED')}")
    print(
        "[Meta Fast Inner] "
        f"ga_pop={cfg.meta_fast_ga_population} "
        f"ga_gen={cfg.meta_fast_ga_generations} "
        f"seed_runs={cfg.meta_fast_stability_seed_runs} "
        f"top_n={cfg.meta_fast_stability_top_n}"
    )


def _build_regime_inputs(ctx: Any, cfg: Any, start: datetime, end_eff: datetime) -> Tuple[pd.DataFrame, Optional[Dict[str, str]]]:
    regime_ts = pd.DataFrame()
    regime_by_date: Optional[Dict[str, str]] = None
    if cfg.enable_regime_diag:
        r_start = start - timedelta(days=max(400, cfg.lookback_days))
        r_end = end_eff
        regime_ts = ctx.build_regime_timeseries(cfg, r_start, r_end, probe=False)

        tmp = regime_ts.copy()
        tmp["Date"] = tmp.index.strftime("%Y-%m-%d")
        regime_by_date = {d: str(r) for d, r in zip(tmp["Date"].values, tmp["regime"].values)}

        print(
            f"[Regime] enabled symbol={cfg.regime_symbol} "
            f"rows={len(regime_ts)} "
            f"range={regime_ts.index.min().date()}~{regime_ts.index.max().date()}"
        )
    return regime_ts, regime_by_date


def _validate_loaded_precompute_pack(ctx: Any, pack: dict, cfg: Any) -> None:
    if pack is None:
        raise ValueError("pack None")

    inds = list(pack.get("indicator_names", []))
    if inds != ctx.INDICATOR_NAMES:
        raise ValueError("indicator_names mismatch -> rebuild")

    if bool(getattr(cfg, "enable_strict_feature_completeness", False)) and not bool(pack.get("feat_valid_from_cache", False)):
        raise ValueError("feat_valid missing in cached precompute -> rebuild")

    ph = ctx._panel_hash(cfg, list(pack["dates"]), list(pack["tickers"]))
    if str(pack.get("hash", "")) != ph:
        raise ValueError("precompute hash mismatch (range/universe/config changed)")


def _try_load_cached_precompute_pack(
    ctx: Any,
    cfg: Any,
    tickers: List[str],
    start: datetime,
    end_eff: datetime,
) -> Tuple[Optional[dict], Optional[str], bool]:
    if not bool(cfg.enable_precompute):
        return None, None, False

    expected_start = start.strftime("%Y-%m-%d")
    expected_end = end_eff.strftime("%Y-%m-%d")
    expected_tickers = sorted([str(x).strip().upper() for x in tickers if str(x).strip()])
    pattern = os.path.join(cfg.save_dir, f"{cfg.precompute_npz_prefix}_*.npz")
    candidates = sorted(glob.glob(pattern), key=lambda p: os.path.getmtime(p), reverse=True)
    last_reason = ""

    for path in candidates:
        stem = os.path.splitext(os.path.basename(path))[0]
        prefix = f"{cfg.precompute_npz_prefix}_"
        if not stem.startswith(prefix):
            continue

        suffix = stem[len(prefix):]
        if len(suffix) < 21:
            continue

        start_s = suffix[:10]
        end_s = suffix[-10:]
        if start_s != expected_start or end_s > expected_end:
            continue

        try:
            pack = ctx.load_precompute_panel(cfg, start_s, end_s)
            _validate_loaded_precompute_pack(ctx, pack, cfg)
            loaded_tickers = sorted([str(x).strip().upper() for x in list(pack.get("tickers", [])) if str(x).strip()])
            if loaded_tickers != expected_tickers:
                raise ValueError("ticker set mismatch")
            return pack, path, True
        except Exception as e:
            last_reason = f"{type(e).__name__}: {e}"

    if last_reason:
        print(f"[Precompute] cache scan miss -> panel rebuild. last_reason={last_reason}")
    return None, None, False


def _build_panel_dataframe(
    ctx: Any,
    cfg: Any,
    tickers: List[str],
) -> Tuple[pd.DataFrame, pd.DataFrame, int, int, int]:
    panels = []
    timing_rows = []
    ok = fail = 0
    dup_drop_total = 0

    pbar = tqdm(tickers, total=len(tickers), desc="Panel build (v4.12 FMP cache)", unit="ticker")
    t_last = time.perf_counter()
    tot_list = []

    for tkr in pbar:
        try:
            panel, tt, dup_dropped = ctx.process_ticker_panel_for_qresearch(
                tkr, cfg.start_panel_date, cfg.end_date, cfg
            )
            timing_rows.append(tt)
            tot_list.append(float(tt.get("ElapsedSec", np.nan)))
            dup_drop_total += int(dup_dropped)

            if tt.get("Status") == "OK" and panel is not None and not panel.empty:
                panels.append(panel)
                ok += 1
            else:
                fail += 1
        except Exception as e:
            timing_rows.append(
                {
                    "Ticker": tkr,
                    "Status": "FAIL",
                    "Reason": f"Exception:{e}",
                    "ElapsedSec": np.nan,
                    "Rows": 0,
                }
            )
            fail += 1

        now = time.perf_counter()
        if now - t_last >= 0.6:
            pbar.set_postfix(ok=ok, fail=fail, tot_ms=f"{ctx._safe_ms(ctx._nanmean(tot_list)):.1f}")
            t_last = now

    df_timing = pd.DataFrame(timing_rows)
    print(f"[Panel] OK={ok} FAIL={fail} dup_drop_total={dup_drop_total}")

    # Fallback: if some tickers failed due to missing cache, try one-time download + retry.
    if fail > 0 and bool(getattr(cfg, "enable_panel_cache_fallback_download", True)):
        fb = try_cache_fallback_for_failed_tickers(ctx, cfg, df_timing, cfg.start_panel_date, cfg.end_date)
        if fb.get("requested", 0) > 0:
            print(
                "[Panel Fallback] "
                f"requested={fb.get('requested', 0)} "
                f"cached={fb.get('success', 0)} "
                f"failed={fb.get('failed', 0)}"
            )
            for tkr in fb.get("tickers", []):
                try:
                    panel, tt, dup_dropped = ctx.process_ticker_panel_for_qresearch(
                        tkr, cfg.start_panel_date, cfg.end_date, cfg
                    )
                    timing_rows.append(tt)
                    dup_drop_total += int(dup_dropped)
                    if tt.get("Status") == "OK" and panel is not None and not panel.empty:
                        panels.append(panel)
                        ok += 1
                        fail = max(0, fail - 1)
                except Exception as e:
                    timing_rows.append(
                        {
                            "Ticker": tkr,
                            "Status": "FAIL",
                            "Reason": f"FallbackRetry:{type(e).__name__}:{e}",
                            "ElapsedSec": np.nan,
                            "Rows": 0,
                        }
                    )
            df_timing = pd.DataFrame(timing_rows)

    if not panels:
        if "Reason" in df_timing.columns and not df_timing.empty:
            vc = df_timing["Reason"].fillna("").value_counts().head(10)
            print("[Panel] Top FAIL reasons:")
            print(vc)
        raise RuntimeError("Panel build failed: no valid panels")

    df_panel = pd.concat(panels, ignore_index=True)
    return df_panel, df_timing, ok, fail, dup_drop_total


def _resolve_input_ticker_universe(ctx: Any, cfg: Any, ttl_days: int = 7) -> Tuple[List[str], str]:
    if hasattr(ctx, "resolve_input_ticker_universe"):
        return ctx.resolve_input_ticker_universe(cfg, ttl_days=ttl_days)

    current_tickers, source = ctx.load_sp500_tickers_ttl(cfg, ttl_days=ttl_days)
    current_syms = sorted(set(str(s).strip().upper() for s in current_tickers if str(s).strip()))

    if not bool(getattr(cfg, "enable_historical_universe", False)):
        return current_syms, source
    if not bool(getattr(cfg, "historical_universe_expand_tickers", True)):
        return current_syms, source

    try:
        events = _load_historical_sp500_constituent_events(cfg, force_refresh=False)
        hist_syms = set()
        for col in ("symbol", "removedTicker", "removed_ticker", "ticker"):
            if col not in events.columns:
                continue
            for v in events[col].tolist():
                s = str(v).strip().upper()
                if s and s not in ("NAN", "NONE", "NULL"):
                    hist_syms.add(s)
    except Exception as e:
        print(f"[HistUniverse][WARN] historical event load failed -> fallback current universe: {e}")
        return current_syms, f"{source}+HIST_FALLBACK"

    if not hist_syms:
        print("[HistUniverse][WARN] no historical symbols resolved -> fallback current universe")
        return current_syms, f"{source}+HIST_EMPTY"

    merged = sorted(set(current_syms).union(hist_syms))
    added = len(set(merged) - set(current_syms))
    return merged, f"{source}+HIST_EVENTS(add={added})"


def prepare_inputs(ctx: Any, cfg: Any) -> Dict[str, Any]:
    _setup_pipeline_environment(ctx, cfg)

    tickers, source = _resolve_input_ticker_universe(ctx, cfg, ttl_days=7)
    start = cfg.start_panel_date
    end = cfg.end_date
    end_eff = end - timedelta(days=1) if str(cfg.ohlcv_policy).upper() == "UP_TO_D1" else end

    _print_pipeline_header(ctx, cfg, tickers, source, start, end_eff)
    regime_ts, regime_by_date = _build_regime_inputs(ctx, cfg, start, end_eff)

    pre_t0 = time.perf_counter()
    pack, npz_path, loaded_from_npz = _try_load_cached_precompute_pack(ctx, cfg, tickers, start, end_eff)

    if loaded_from_npz:
        print(f"[Precompute] loaded cached npz: {npz_path}")
        df_timing = pd.DataFrame()
        ok = len(tickers)
        fail = 0
        dup_drop_total = 0
        print("[Panel] skipped -> using valid precompute cache")
    else:
        df_panel, df_timing, ok, fail, dup_drop_total = _build_panel_dataframe(ctx, cfg, tickers)
        dates = ctx._normalize_dates_any(df_panel["Date"].unique())
        start_s, end_s = dates[0], dates[-1]

        if cfg.enable_precompute:
            try:
                pack = ctx.load_precompute_panel(cfg, start_s, end_s)
                _validate_loaded_precompute_pack(ctx, pack, cfg)
                npz_path = ctx._precompute_npz_path(cfg, start_s, end_s)
                loaded_from_npz = True
                print(f"[Precompute] loaded npz: {npz_path}")
            except Exception as e:
                print(f"[Precompute] invalid -> rebuild. reason={type(e).__name__}: {e}")
                pack, npz_path = ctx.build_precompute_panel(df_panel, cfg)
                print(f"[Precompute] rebuilt npz: {npz_path}")
        else:
            pack, npz_path = ctx.build_precompute_panel(df_panel, cfg)
            print(f"[Precompute] built (no-cache) npz: {npz_path}")

    if pack is not None:
        if "build_mcap_diag" in pack:
            ctx.print_precompute_marketcap_coverage(pack)
        if loaded_from_npz and "loaded_mcap_diag" in pack:
            ctx.print_loaded_pack_marketcap_coverage(pack)

    quality_tables = ctx.build_pack_data_quality_tables(pack, cfg)

    pre_sec = float(time.perf_counter() - pre_t0)
    return {
        "cfg": cfg,
        "tickers": tickers,
        "source": source,
        "start": start,
        "end_eff": end_eff,
        "regime_ts": regime_ts,
        "regime_by_date": regime_by_date,
        "df_timing": df_timing,
        "ok": ok,
        "fail": fail,
        "dup_drop_total": dup_drop_total,
        "pack": pack,
        "npz_path": npz_path,
        "loaded_from_npz": loaded_from_npz,
        "pre_sec": pre_sec,
        "data_quality_summary_df": quality_tables.get("summary_df", pd.DataFrame()),
        "factor_coverage_df": quality_tables.get("factor_coverage_df", pd.DataFrame()),
        "data_quality_sample_df": quality_tables.get("sample_df", pd.DataFrame()),
        "historical_universe_debug_summary_df": quality_tables.get("historical_universe_debug_summary_df", pd.DataFrame()),
        "historical_universe_yearly_df": quality_tables.get("historical_universe_yearly_df", pd.DataFrame()),
    }
