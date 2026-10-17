"""RVC001 固定公式及两种出场规则，使用已结束行情的连续信号价。"""
from collections import deque
from math import sqrt


def ema(previous, close, period):
    return close if previous is None else previous + (close - previous) * 2 / (period + 1)


def trend(fast, slow, previous_fast, previous_slow):
    if previous_fast is None:
        return {"state": None, "buy": False, "down_cross": False}
    if fast >= slow:
        state = "red" if fast >= previous_fast else "pink"
    else:
        state = "blue" if fast > previous_fast else "green"
    return {"state": state, "buy": fast > slow and previous_fast <= previous_slow,
            "down_cross": fast < slow and previous_fast >= previous_slow}


def next_target(previous, signal, exit_state, allocation):
    if previous > 0:
        exit_states = (exit_state,) if isinstance(exit_state, str) else exit_state
        return 0 if signal["state"] in exit_states else previous
    return allocation if signal["buy"] else 0


def strategy_targets(context, exposure, exit_state):
    allocation = exposure / len(context["codes"])
    weights = {}
    for code in context["codes"]:
        bars = context["history"][code]
        if len(bars) < 11:
            raise ValueError(f"{code} 的 RVC001 指标预热数据不足")
        current, previous = bars[-1], bars[-2]
        signal = trend(current["signal_ema3"], current["signal_ema8"],
                       previous["signal_ema3"], previous["signal_ema8"])
        weights[code] = next_target(context["targets"].get(code, 0), signal, exit_state, allocation)
    return weights


def attach_chart_indicator(bars, start, red_exit_mode="pink_or_green"):
    red_exit_states = ("pink", "green") if red_exit_mode == "pink_or_green" else ("pink",)
    closes = deque(maxlen=10)
    mid = previous_fast = previous_slow = None
    red_target = green_target = 0
    for bar in bars:
        close = bar["signal_close"]
        closes.append(close)
        mid = ema(mid, close, 10)
        mean = sum(closes) / len(closes)
        sd = sqrt(sum((value - mean) ** 2 for value in closes) / 9) if len(closes) == 10 else None
        fast, slow = bar["signal_ema3"], bar["signal_ema8"]
        signal = trend(fast, slow, previous_fast, previous_slow)
        red_exit = pink_exit = green_exit = False
        if bar["date"] >= start:
            red_exit = red_target > 0 and signal["state"] in red_exit_states
            pink_exit = red_exit and signal["state"] == "pink"
            green_exit = green_target > 0 and signal["state"] == "green"
            red_target = next_target(red_target, signal, red_exit_states, 1)
            green_target = next_target(green_target, signal, "green", 1)
        scale = bar["signal_scale"]
        bar["rvc001"] = {"open": bar["open"] * scale, "close": close,
                         "low": bar["low"] * scale, "high": bar["high"] * scale,
                         "fast": fast, "slow": slow, "mid": mid, "sd": sd,
                         "up": mid + sd if sd is not None else None,
                         "down": mid - sd if sd is not None else None,
                         "up1": mid + 2 * sd if sd is not None else None,
                         "down1": mid - 2 * sd if sd is not None else None,
                         **signal, "red_exit": red_exit, "pink_exit": pink_exit, "green_exit": green_exit}
        previous_fast, previous_slow = fast, slow
