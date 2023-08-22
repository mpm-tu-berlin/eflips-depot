import eflips
import datetime
import matplotlib.pyplot as plt
import pandas as pd
import eflips.evaluation
import matplotlib.ticker as ticker

max_sim_time = 172800
price_data_path = "spot_day_ahead\\"

pd_price = pd.DataFrame()

# Spot price 1
start_date = datetime.date(2018, 10, 23)
price_item = eflips.depot.PowerFrame(max_sim_time, start_date, price_data_path)
price = price_item.pdframe()
pd_price["Day-Ahead Auktion 24.10.2018"] = price["price"]

# Spot price 2
start_date = datetime.date(2019, 2, 19)
price_item = eflips.depot.PowerFrame(max_sim_time, start_date, price_data_path)
price = price_item.pdframe()
pd_price["Day-Ahead Auktion 20.02.2019"] = price["price"]

# Spot price base scenario
start_date = datetime.date(2019, 3, 10)
price_item = eflips.depot.PowerFrame(max_sim_time, start_date, price_data_path)
price = price_item.pdframe()
pd_price["Day-Ahead Auktion 11.03.2019"] = price["price"]

# # Spot price 3
# start_date = datetime.date(2019, 1, 23)
# price_item = eflips.depot.PowerFrame(max_sim_time, start_date, price_data_path)
# price = price_item.pdframe()
# pd_price['24.01.2019'] = price['price']

# Average price 2019
pd_price["Durchschnitt 2019"] = 0.0433

# fig, ax = plt.subplots(figsize=(7, 4.5))
ax = pd_price.plot(color=["blue", "red", "green", "black"])

ax.set_xlim(86400, 172800)
eflips.depot.evaluation.setting_language("de")
eflips.depot.evaluation.to_dateaxis(ax)

plt.xlabel("Zeit")
plt.ylabel("Preis Spotmarkt [€/kWh]")


# plt.show()
