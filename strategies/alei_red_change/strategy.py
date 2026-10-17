from stockapp.rvc001 import strategy_targets


class Strategy:
    def __init__(self, parameters):
        self.exposure = parameters["exposure"]

    def on_bar(self, context):
        return strategy_targets(context, self.exposure, ("pink", "green"))
