import re
import json
import time

from pytz import UTC

from signals import Signals
from datetime import datetime
from babel.numbers import format_currency
from babel.dates import format_timedelta


class SingleBot:
    def __init__(
        self, tg_data, bot_data, account_data, attributes, p3cw, logging, asyncState
    ):
        self.tg_data = tg_data
        self.bot_data = bot_data
        self.account_data = account_data
        self.attributes = attributes
        self.p3cw = p3cw
        self.logging = logging
        self.asyncState = asyncState
        self.signal = Signals(logging)
        self.prefix = self.attributes.get("prefix", "3CQSBOT", "dcabot")
        self.subprefix = self.attributes.get("subprefix", "SINGLE", "dcabot")
        self.suffix = self.attributes.get("suffix", "dcabot", "dcabot")
        self.bot_name = (
            self.prefix
            + "_"
            + self.subprefix
            + "_"
            + self.attributes.get("market")
            + "(.*)"
            + "_"
            + self.suffix
        )

    def strategy(self):
        if self.attributes.get("deal_mode", "", self.asyncState.dca_conf) == "signal":
            strategy = [{"strategy": "nonstop"}]
        else:
            try:
                strategy = json.loads(
                    self.attributes.get("deal_mode", "", self.asyncState.dca_conf)
                )
            except ValueError:
                self.logging.error(
                    "Either missing ["
                    + self.asyncState.dca_conf
                    + "] section with DCA settings or decoding JSON string of deal_mode failed. Please check https://jsonformatter.curiousconcept.com/ for correct format"
                )

        return strategy

    def deal_count(self):
        account = self.account_data
        deals = 0

        for bot in self.bot_data:
            if re.search(self.bot_name, bot["name"]):
                deals += int(bot["active_deals_count"])

        self.logging.debug(
            "Active deals of single bots (enabled and disabled): " + str(deals)
        )

        return deals

    def bot_count(self):

        bots = []

        for bot in self.bot_data:
            if re.search(self.bot_name, bot["name"]) and bot["is_enabled"]:
                bots.append(bot)

        self.logging.debug("Enabled single bots: " + str(len(bots)))

        return len(bots), bots

    def disabled_bot_active_deals_count(self):

        bots = []

        for bot in self.bot_data:
            if (
                re.search(self.bot_name, bot["name"])
                and not bot["is_enabled"]
                and bot["active_deals_count"] > 0
            ):
                bots.append(bot["name"])

        self.logging.debug("Disabled single bots with active deals: " + str(len(bots)))

        return len(bots)

    def report_funds_needed(self, maxdeals):

        tp = self.attributes.get("tp", "", self.asyncState.dca_conf)
        bo = self.attributes.get("bo", "", self.asyncState.dca_conf)
        so = self.attributes.get("so", "", self.asyncState.dca_conf)
        os = self.attributes.get("os", "", self.asyncState.dca_conf)
        ss = self.attributes.get("ss", "", self.asyncState.dca_conf)
        sos = self.attributes.get("sos", "", self.asyncState.dca_conf)
        mstc = self.attributes.get("mstc", "", self.asyncState.dca_conf)

        fundsneeded = bo + so
        socalc = so
        pd = sos
        for i in range(mstc - 1):
            socalc = socalc * os
            fundsneeded += socalc
            pd = (pd * ss) + sos

        self.logging.info(
            "["
            + self.asyncState.dca_conf
            + "] TP: "
            + str(tp)
            + "%  BO: $"
            + str(bo)
            + "  SO: $"
            + str(so)
            + "  OS: "
            + str(os)
            + "  SS: "
            + str(ss)
            + "  SOS: "
            + str(sos)
            + "%  MSTC: "
            + str(mstc)
            + " - covering max. price deviation: "
            + f"{pd:2.1f}"
            + "%",
            True,
        )
        self.logging.info(
            "Max possible single bot deals: "
            + str(maxdeals)
            + "   Funds per single bot deal: "
            + format_currency(fundsneeded, "USD", locale="en_US")
            + "   Total funds needed: "
            + format_currency(maxdeals * fundsneeded, "USD", locale="en_US"),
            True,
        )

        return

    def report_deals(self):

        running_bots, bots = self.bot_count()

        self.logging.info(
            "Single bots active: "
            + str(running_bots)
            + "/"
            + str(self.attributes.get("single_count")),
            True,
        )

        for bot in bots:
            self.logging.info(
                "Bot "
                + bot["pairs"][0]
                + " with "
                + str(bot["finished_deals_count"])
                + " finished deals. Total profit: "
                + format_currency(
                    bot["finished_deals_profit_usd"], "USD", locale="en_US"
                ),
                True,
            )

            error, data = self.p3cw.request(
                entity="deals",
                action="",
                action_id="",
                additional_headers={"Forced-Mode": self.attributes.get("trade_mode")},
                payload={"limit": 100, "bot_id": bot["id"], "scope": "active"},
            )
            if error:
                self.logging.error(error["msg"])
            else:
                for deals in data:
                    if (
                        deals["bought_volume"] == None
                    ):  # if no bought_volume, then use base_order_volume for bought_volume
                        bought_volume = format_currency(
                            deals["base_order_volume"], "USD", locale="en_US"
                        )
                    else:
                        bought_volume = format_currency(
                            deals["bought_volume"], "USD", locale="en_US"
                        )

                    self.logging.info(
                        "Last deal "
                        + deals["pair"]
                        + " open since "
                        + format_timedelta(
                            datetime.utcnow()
                            - datetime.strptime(
                                deals["created_at"], "%Y-%m-%dT%H:%M:%S.%fZ"
                            ),
                            locale="en_US",
                        )
                        + "   Actual profit: "
                        + format_currency(
                            deals["actual_usd_profit"], "USD", locale="en_US"
                        )
                        + " ("
                        + deals["actual_profit_percentage"]
                        + "%)"
                        + "   Bought volume: "
                        + bought_volume
                        + "   Deal error: "
                        + str(deals["deal_has_error"]),
                        True,
                    )

        return

    def payload(self, pair, new_bot):
        payload = {
            "name": self.attributes.get("prefix", "3CQSBOT", "dcabot")
            + "_"
            + self.attributes.get("subprefix", "SINGLE", "dcabot")
            + "_"
            + pair
            + "_"
            + self.attributes.get("suffix", "dcabot", "dcabot"),
            "account_id": self.account_data["id"],
            "pairs": self.tg_data["pair"],
            "max_active_deals": self.attributes.get(
                "mad", "", self.asyncState.dca_conf
            ),
            "base_order_volume": self.attributes.get(
                "bo", "", self.asyncState.dca_conf
            ),
            "take_profit": self.attributes.get("tp", "", self.asyncState.dca_conf),
            "safety_order_volume": self.attributes.get(
                "so", "", self.asyncState.dca_conf
            ),
            "martingale_volume_coefficient": self.attributes.get(
                "os", "", self.asyncState.dca_conf
            ),
            "martingale_step_coefficient": self.attributes.get(
                "ss", "", self.asyncState.dca_conf
            ),
            "max_safety_orders": self.attributes.get(
                "mstc", "", self.asyncState.dca_conf
            ),
            "safety_order_step_percentage": self.attributes.get(
                "sos", "", self.asyncState.dca_conf
            ),
            "take_profit_type": "total",
            "active_safety_orders_count": self.attributes.get(
                "max", "", self.asyncState.dca_conf
            ),
            "cooldown": self.attributes.get("cooldown", 0, self.asyncState.dca_conf),
            "strategy_list": self.strategy(),
            "trailing_enabled": self.attributes.get(
                "trailing", False, self.asyncState.dca_conf
            ),
            "trailing_deviation": self.attributes.get(
                "trailing_deviation", 0.2, self.asyncState.dca_conf
            ),
            "min_volume_btc_24h": self.attributes.get(
                "btc_min_vol", 0, self.asyncState.dca_conf
            ),
            "disable_after_deals_count": self.attributes.get(
                "deals_count", 0, self.asyncState.dca_conf
            ),
        }

        if new_bot:
            if payload["disable_after_deals_count"] == 0:
                self.logging.debug(
                    "This is a new bot and deal_count set to 0, removing from payload"
                )
                payload.pop("disable_after_deals_count")

        if self.attributes.get("trade_future", False):
            payload.update(
                {
                    "leverage_type": self.attributes.get("leverage_type"),
                    "leverage_custom_value": self.attributes.get("leverage_value"),
                    "stop_loss_percentage": self.attributes.get("stop_loss_percent"),
                    "stop_loss_type": self.attributes.get("stop_loss_type"),
                    "stop_loss_timeout_enabled": self.attributes.get(
                        "stop_loss_timeout_enabled"
                    ),
                    "stop_loss_timeout_in_seconds": self.attributes.get(
                        "stop_loss_timeout_seconds"
                    ),
                }
            )

        return payload

    def update(self, bot):
        # Update settings on an existing bot

        error, data = self.p3cw.request(
            entity="bots",
            action="update",
            action_id=str(bot["id"]),
            additional_headers={"Forced-Mode": self.attributes.get("trade_mode")},
            payload=self.payload(bot["pairs"][0], new_bot=False),
        )

        if error:
            self.logging.error(error["msg"])

    def enable(self, bot):

        self.logging.info(
            "Enabling single bot with pair "
            + bot["pairs"][0]
            + ". Applying following DCA settings:",
            True,
        )

        if self.attributes.get("singlebot_update", "True"):
            self.update(bot)

        # Enables an existing bot
        error, data = self.p3cw.request(
            entity="bots",
            action="enable",
            action_id=str(bot["id"]),
            additional_headers={"Forced-Mode": self.attributes.get("trade_mode")},
        )

        if error:
            self.logging.error(error["msg"])
        else:
            self.asyncState.bot_active = True
            i = 0
            for bot in self.bot_data:
                if bot["name"] == data["name"]:
                    self.bot_data[i]["is_enabled"] = True
                    break
                i += 1

    def disable(self, bot, allbots=False):
        botname = (
            self.attributes.get("prefix", "3CQSBOT", "dcabot")
            + "_"
            + self.attributes.get("subprefix", "SINGLE", "dcabot")
            + "_"
            + self.attributes.get("market")
        )

        # Disable all bots
        error = {}

        if allbots:
            self.asyncState.bot_active = False
            self.logging.info(
                "Disabling all 3cqs single bots because btc-pulse is signaling downtrend.",
                True,
            )

            for bots in bot:
                if botname in bots["name"] and bot["is_enabled"]:

                    self.logging.info(
                        "Disabling single bot "
                        + bots["name"]
                        + " because of a STOP signal",
                        True,
                    )

                    error, data = self.p3cw.request(
                        entity="bots",
                        action="disable",
                        action_id=str(bots["id"]),
                        additional_headers={
                            "Forced-Mode": self.attributes.get("trade_mode")
                        },
                    )

                    if error:
                        self.logging.error(error["msg"])
        else:
            # Disables an existing bot
            self.logging.info(
                "Disabling single bot " + bot["name"] + " because of a STOP signal",
                True,
            )

            error, data = self.p3cw.request(
                entity="bots",
                action="disable",
                action_id=str(bot["id"]),
                additional_headers={"Forced-Mode": self.attributes.get("trade_mode")},
            )

            if error:
                self.logging.error(error["msg"])

    def create(self):
        # Creates a single bot with start signal
        error, data = self.p3cw.request(
            entity="bots",
            action="create_bot",
            additional_headers={"Forced-Mode": self.attributes.get("trade_mode")},
            payload=self.payload(self.tg_data["pair"], new_bot=True),
        )

        self.logging.info(
            "Creating single bot with pair "
            + self.tg_data["pair"]
            + " and name "
            + data["name"]
            + ".",
            True,
        )

        if error:
            self.logging.error(error["msg"])
        else:
            # Insert new bot at the begin of all bot data
            self.bot_data.insert(0, data)
            # Fix - 3commas needs some time for bot creation
            time.sleep(2)
            self.enable(data)

    def delete(self, bot):
        if bot["active_deals_count"] == 0 and self.attributes.get(
            "delete_single_bots", False
        ):
            # Deletes a single bot with stop signal
            self.logging.info(
                "Delete single bot with pair " + self.tg_data["pair"], True
            )
            error, data = self.p3cw.request(
                entity="bots",
                action="delete",
                action_id=str(bot["id"]),
                additional_headers={"Forced-Mode": self.attributes.get("trade_mode")},
            )

            if error:
                self.logging.error(error["msg"])
        # Only perform the disable request if necessary
        elif bot["is_enabled"]:
            self.logging.info(
                "Disabling single bot with pair "
                + self.tg_data["pair"]
                + " unable to delete because of active deals or configuration.",
                True,
            )
            self.disable(bot, False)
        # No bot to delete or disable
        else:
            self.logging.info("Bot not enabled, nothing to do!")

    def trigger(self):
        # Triggers a single bot deal
        new_bot = True
        pair = self.tg_data["pair"]
        running_bots, bots = self.bot_count()
        running_deals = self.deal_count()
        disabled_bot_deals = self.disabled_bot_active_deals_count()
        maxdeals = self.attributes.get("single_count")

        botname = (
            self.attributes.get("prefix", "3CQSBOT", "dcabot")
            + "_"
            + self.attributes.get("subprefix", "SINGLE", "dcabot")
            + "_"
            + pair
            + "_"
            + self.attributes.get("suffix", "dcabot", "dcabot")
        )

        if self.bot_data:
            for bot in self.bot_data:
                if botname == bot["name"]:
                    new_bot = False
                    break

            if new_bot:
                if self.tg_data["action"] == "START":
                    if running_bots < self.attributes.get("single_count"):

                        if self.attributes.get("topcoin_filter", False):
                            pair = self.signal.topcoin(
                                pair,
                                self.attributes.get("topcoin_limit", 3500),
                                self.attributes.get("topcoin_volume", 0),
                                self.attributes.get("topcoin_exchange", "binance"),
                                self.attributes.get("market"),
                            )
                        else:
                            self.logging.info(
                                "Topcoin filter disabled, not filtering pairs!"
                            )

                        if pair:
                            # avoid deals over limit
                            if running_deals < self.attributes.get("single_count"):
                                if (
                                    running_bots + disabled_bot_deals
                                ) < self.attributes.get("single_count"):
                                    self.create()
                                    self.report_funds_needed(maxdeals)
                                    self.report_deals()
                                else:
                                    self.logging.info(
                                        "Single bot not created. Blocking new deals, max deals of "
                                        + str(maxdeals)
                                        + " reached."
                                    )
                            else:
                                self.logging.info(
                                    "Single bot not created. Blocking new deals, max deals of "
                                    + str(maxdeals)
                                    + " reached."
                                )
                        else:
                            self.logging.info(
                                "Pair "
                                + pair
                                + " is not in the top coin list - not added!"
                            )
                    else:
                        self.logging.info(
                            "Maximum bots/deals of "
                            + str(maxdeals)
                            + " reached. Single bot with "
                            + pair
                            + " not added."
                        )

                elif self.tg_data["action"] == "STOP":
                    self.logging.info(
                        "Stop command on non-existing single bot for pair "
                        + pair
                        + " ignored."
                    )
            else:
                self.logging.debug("Pair: " + pair)
                self.logging.debug("Bot-Name: " + bot["name"])

                if self.tg_data["action"] == "START":
                    if running_bots < self.attributes.get("single_count"):
                        # avoid deals over limit
                        if running_deals < self.attributes.get("single_count"):
                            if (
                                running_bots + disabled_bot_deals
                            ) < self.attributes.get("single_count"):
                                self.enable(bot)
                                self.report_funds_needed(maxdeals)
                                self.report_deals()
                            else:
                                self.logging.info(
                                    "Blocking new deals, because last enabled bot can potentially reach max deals of "
                                    + str(maxdeals)
                                    + "."
                                )
                        else:
                            self.logging.info(
                                "Blocking new deals, maximum active deals of "
                                + str(maxdeals)
                                + " reached."
                            )

                    else:
                        self.logging.info(
                            "Maximum enabled bots of "
                            + str(maxdeals)
                            + " reached. No single bot with "
                            + pair
                            + " created/enabled."
                        )
                else:
                    self.delete(bot)

        else:
            self.logging.info("No single bots found", True)
            self.create()
            self.report_funds_needed(maxdeals)
            self.report_deals()
