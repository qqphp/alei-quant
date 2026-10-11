import math


class Strategy:
    def __init__(self, parameters):
        self.fast = parameters["fast"]
        self.slow = parameters["slow"]
        self.minimum_eps = parameters["minimum_eps"]
        self.exposure = parameters["exposure"]
        if self.fast >= self.slow:
            raise ValueError("短期均线必须小于长期均线")

    def on_bar(self, context):
        weights = {}
        allocation = self.exposure / len(context["codes"])
        for code in context["codes"]:
            bars = context["history"][code]
            if len(bars) < self.slow:
                raise ValueError(f"{code} 的指标预热不足")
            closes = [bar["signal_close"] for bar in bars]
            fast = sum(closes[-self.fast:]) / self.fast
            slow = sum(closes[-self.slow:]) / self.slow
            latest = context["data"]["profit"][code][-1]
            eps = float(latest["epsTTM"])
            if not math.isfinite(eps):
                raise ValueError(f"{code} 财报 EPS TTM 不是有限数值")
            weights[code] = allocation if fast > slow and eps > self.minimum_eps else 0
        return weights
