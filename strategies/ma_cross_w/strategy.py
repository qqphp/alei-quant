class Strategy:
    def __init__(self, parameters):
        self.fast = parameters["fast"]
        self.slow = parameters["slow"]
        self.exposure = parameters["exposure"]
        if self.fast >= self.slow:
            raise ValueError("短期均线必须小于长期均线")

    def on_bar(self, context):
        weights = {}
        allocation = self.exposure / len(context["codes"])
        for code in context["codes"]:
            bars = context["history"][code]
            if len(bars) < self.slow + 1:
                raise ValueError(f"{code} 的指标预热数据不足")
            closes = [b["signal_close"] for b in bars]
            fast = sum(closes[-self.fast:]) / self.fast
            slow = sum(closes[-self.slow:]) / self.slow
            previous_fast = sum(closes[-self.fast-1:-1]) / self.fast
            previous_slow = sum(closes[-self.slow-1:-1]) / self.slow
            # 状态持有，避免在短期均线持续高于长期均线时每日强制再平衡。
            previous = context["targets"].get(code, 0)
            if fast > slow and (previous_fast <= previous_slow or context["first_signal"]):
                weights[code] = allocation
            elif fast < slow and previous_fast >= previous_slow:
                weights[code] = 0
            else:
                weights[code] = previous
        return weights

